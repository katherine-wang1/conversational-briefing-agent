"""Story clustering (TDD §7 N3), packaged for reuse.

Two users:
- the newsroom pipeline (`app/agents/newsroom/pipeline.py`), once per run;
- the evaluation track (`eval/archive/labels.py`, TDD §15.4), which clusters a whole archive window
  with the *same method* to compute hindsight coverage labels.

Nothing here knows about beats, configs, prompts or the model layer. The only LLM hook is the
optional `tiebreaker` callable, so the module also runs fully offline.

Method
------
1. Embed each item's text (title + snippet) with an `Embedder`. Vectors are L2-normalised.
2. Cosine-similarity matrix.
3. Agglomerative clustering, **average linkage**, merging while the best cluster-pair similarity is
   `>= tau` (default 0.78, tuned for `BAAI/bge-small-en-v1.5`).
4. Cluster pairs whose average similarity falls in the ambiguous band `[tau - band, tau)` are sent,
   most similar first and at most `max_tiebreak_pairs`, to the `tiebreaker`. Pairs it answers "same
   event" for are merged (union-find). Without a tiebreaker they stay separate.
5. Output is deterministic: items are processed in the order of their ids (sorted), ties break on
   the lowest index, and clusters are returned sorted by their first member id.

`match_to_prior` uses the same embedding and thresholds to link new clusters to earlier stories
(story identity across runs and days): a shared URL is a match; otherwise the cluster-to-story
average similarity `>= tau` is a match and the ambiguous band goes to the tiebreaker.

Example (offline, as the evaluation track would use it)::

    from app.agents.newsroom.clustering import FastEmbedEmbedder, cluster_texts
    result = await cluster_texts(ids, texts, embedder=FastEmbedEmbedder(), tau=0.78, band=0.08)
    for members in result.clusters: ...

Complexity: O(n^2) similarity memory. Fine up to a few thousand items per call; for a long window,
cluster per day and chain days with `match_to_prior` (that is what the newsroom does).
"""

from __future__ import annotations

import hashlib
import heapq
import math
import os
import re
import threading
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

try:  # optional acceleration; the module works without numpy
    import numpy as _np
except ImportError:  # pragma: no cover - depends on environment
    _np = None

Vector = list[float]


# ---- embedders ---------------------------------------------------------------------------------


class Embedder(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> list[Vector]: ...


def _l2(v: Vector) -> Vector:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 0 else v


# One ONNX session per model per process: concurrent newsroom runs in the worker share it instead
# of each loading its own copy (on a ~750 MB Railway service, four copies got the worker OOM-killed).
_FASTEMBED_MODELS: dict[str, Any] = {}
_FASTEMBED_LOCK = threading.Lock()
# fastembed's default batch is 256 texts; ONNX activation memory grows with it.
FASTEMBED_BATCH_SIZE = 32


def _fastembed_threads() -> int | None:
    """ONNX Runtime sizes its thread pool from the host's core count, which in a container capped at
    ~2 vCPU (Railway) means dozens of threads fighting over two CPUs. FASTEMBED_THREADS caps it
    (set to 2 in the Docker image); unset leaves ONNX's default."""
    v = os.environ.get("FASTEMBED_THREADS", "").strip()
    return int(v) if v else None


def _fastembed_model(model: str) -> Any:
    with _FASTEMBED_LOCK:
        impl = _FASTEMBED_MODELS.get(model)
        if impl is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as e:  # pragma: no cover - depends on environment
                raise RuntimeError(
                    "fastembed is not installed: pip install -e '.[newsroom]' or pass another Embedder"
                ) from e
            impl = _FASTEMBED_MODELS[model] = TextEmbedding(model_name=model, threads=_fastembed_threads())
        return impl


class FastEmbedEmbedder:
    """Local CPU embeddings via fastembed (install extra `newsroom`).

    The model is downloaded once on first use (network) and cached by fastembed; in replay and
    tests, pass a pre-cached model or another `Embedder`. The loaded model is shared by every
    instance in the process, and calls into it are serialised.
    """

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5") -> None:
        self.model = model
        self.name = f"fastembed:{model}"

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        impl = _fastembed_model(self.model)
        with _FASTEMBED_LOCK:
            vecs = list(impl.embed(list(texts), batch_size=FASTEMBED_BATCH_SIZE))
        return [_l2([float(x) for x in v]) for v in vecs]


_STOP = frozenset(
    "a an and are as at be by for from has have in into is it its of on or that the this to was "
    "were will with after over new says said how why what who".split()
)
_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")


class HashingEmbedder:
    """Deterministic, dependency-free bag-of-features embedder (words, bigrams, char 4-grams).

    Meant for tests and offline smoke runs, not for production clustering. `name_weight` adds
    weight to tokens that mix letters and digits (model/product names such as "atlas-5"). Its
    similarity scale differs from fastembed's: the newsroom tests use `name_weight=4`, tau 0.6 and
    band 0.2 instead of the configured 0.78 / 0.08.
    """

    def __init__(self, dims: int = 1024, name_weight: float = 0.0) -> None:
        self.dims = dims
        self.name_weight = name_weight
        self.name = f"hashing:{dims}:{name_weight:g}"

    def _bucket(self, feat: str) -> tuple[int, float]:
        h = hashlib.blake2b(feat.encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "big") % self.dims
        sign = 1.0 if h[4] & 1 else -1.0
        return idx, sign

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        out = []
        for t in texts:
            words = [w for w in _TOKEN.findall(t.lower()) if w not in _STOP]
            feats: dict[str, float] = {}
            for w in words:
                feats["w:" + w] = feats.get("w:" + w, 0.0) + 1.0
                padded = f"#{w}#"
                for i in range(max(1, len(padded) - 3)):
                    g = "c:" + padded[i : i + 4]
                    feats[g] = feats.get(g, 0.0) + 0.25
            for w in words:  # product / model names like "atlas-5", "px-9": strong event identity
                if self.name_weight and any(ch.isdigit() for ch in w) and any(ch.isalpha() for ch in w):
                    feats["n:" + w] = feats.get("n:" + w, 0.0) + self.name_weight
            for a, b in zip(words, words[1:], strict=False):
                feats[f"b:{a} {b}"] = feats.get(f"b:{a} {b}", 0.0) + 0.5
            v = [0.0] * self.dims
            for f, c in feats.items():
                idx, sign = self._bucket(f)
                v[idx] += sign * (1.0 + math.log(c)) if c >= 1 else sign * c
            out.append(_l2(v))
        return out


# ---- similarity and agglomerative clustering -----------------------------------------------------


def cosine_matrix(vectors: Sequence[Vector]) -> list[list[float]]:
    """Pairwise cosine similarities of L2-normalised vectors."""
    if not vectors:
        return []
    if _np is not None:
        m = _np.asarray(vectors, dtype=float)
        return (m @ m.T).tolist()
    n = len(vectors)
    sim = [[0.0] * n for _ in range(n)]
    for i in range(n):
        vi = vectors[i]
        sim[i][i] = sum(x * x for x in vi)
        for j in range(i + 1, n):
            s = sum(a * b for a, b in zip(vi, vectors[j], strict=True))
            sim[i][j] = sim[j][i] = s
    return sim


def _key(x: float) -> float:
    return round(x, 9)  # stable ordering despite float noise


def agglomerative(sim: list[list[float]], tau: float) -> tuple[list[list[int]], dict]:
    """Average-linkage agglomerative clustering with a similarity threshold.

    Returns (clusters, linkage) where clusters are sorted lists of indices ordered by first index,
    and linkage maps (root_a, root_b) -> summed pairwise similarity, for later band queries.
    """
    n = len(sim)
    members: dict[int, list[int]] = {i: [i] for i in range(n)}
    sums: dict[int, dict[int, float]] = {i: {j: sim[i][j] for j in range(n) if j != i} for i in range(n)}
    version = dict.fromkeys(range(n), 0)
    heap: list[tuple[float, int, int, int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            if sim[i][j] >= tau:
                heap.append((-_key(sim[i][j]), i, j, 0, 0))
    heapq.heapify(heap)
    while heap:
        neg, i, j, vi, vj = heapq.heappop(heap)
        if i not in members or j not in members or version[i] != vi or version[j] != vj:
            continue
        if -neg < tau:
            break
        # merge j into i (i < j keeps the lowest index as the root)
        members[i].extend(members.pop(j))
        version[i] += 1
        sj = sums.pop(j)
        for k in members:
            if k == i:
                continue
            s = sums[i][k] + sj[k]
            sums[i][k] = s
            sums[k][i] = s
            del sums[k][j]
            avg = s / (len(members[i]) * len(members[k]))
            if avg >= tau:
                a, b = (i, k) if i < k else (k, i)
                heapq.heappush(heap, (-_key(avg), a, b, version[a], version[b]))
        del sums[i][j]
    roots = sorted(members)
    clusters = [sorted(members[r]) for r in roots]
    linkage = {
        (a, b): sums[a][b] / (len(members[a]) * len(members[b]))
        for x, a in enumerate(roots)
        for b in roots[x + 1 :]
    }
    return clusters, {"roots": roots, "avg": linkage}


# ---- tiebreak hook --------------------------------------------------------------------------------


@dataclass(frozen=True)
class TiebreakPair:
    pair_id: str
    a_id: str
    b_id: str
    a_text: str
    b_text: str
    similarity: float


Tiebreaker = Callable[[list[TiebreakPair]], Awaitable[dict[str, bool]]]
"""Async callable: pairs -> {pair_id: same_event}. Missing pair ids count as "no"."""


@dataclass
class ClusterResult:
    ids: list[str]  # item ids in processing order (sorted)
    clusters: list[list[str]]  # member ids per cluster, sorted; clusters sorted by first id
    vectors: dict[str, Vector]
    tiebreaks: list[tuple[TiebreakPair, bool]] = field(default_factory=list)

    def cluster_of(self) -> dict[str, int]:
        return {m: ci for ci, members in enumerate(self.clusters) for m in members}


def _medoid(idx: list[int], sim: list[list[float]]) -> int:
    if len(idx) == 1:
        return idx[0]
    return max(idx, key=lambda i: (_key(sum(sim[i][j] for j in idx if j != i)), -i))


async def cluster_texts(
    ids: Sequence[str],
    texts: Sequence[str],
    *,
    embedder: Embedder,
    tau: float = 0.78,
    band: float = 0.08,
    tiebreaker: Tiebreaker | None = None,
    max_tiebreak_pairs: int = 40,
    vectors: Sequence[Vector] | None = None,
) -> ClusterResult:
    """Cluster items (id, text) into events. See the module docstring for the method.

    `vectors`, if given, must align with `ids` and skips embedding.
    """
    if len(ids) != len(texts) or len(set(ids)) != len(ids):
        raise ValueError("ids must be unique and align with texts")
    order = sorted(range(len(ids)), key=lambda k: ids[k])
    sid = [ids[k] for k in order]
    stext = [texts[k] for k in order]
    vecs = [list(vectors[k]) for k in order] if vectors is not None else embedder.embed(stext)
    sim = cosine_matrix(vecs)
    groups, link = agglomerative(sim, tau)

    tiebreaks: list[tuple[TiebreakPair, bool]] = []
    if tiebreaker is not None and len(groups) > 1 and band > 0:
        roots = link["roots"]
        root_to_group = {r: g for r, g in zip(roots, groups, strict=True)}
        cands = sorted(
            ((avg, a, b) for (a, b), avg in link["avg"].items() if tau - band <= avg < tau),
            key=lambda x: (-_key(x[0]), x[1], x[2]),
        )[:max_tiebreak_pairs]
        pairs = []
        for avg, a, b in cands:
            ma, mb = _medoid(root_to_group[a], sim), _medoid(root_to_group[b], sim)
            pid = hashlib.sha1(f"{sid[a]}|{sid[b]}".encode()).hexdigest()[:12]
            pairs.append(TiebreakPair(pid, sid[a], sid[b], stext[ma], stext[mb], round(avg, 4)))
        decisions = await tiebreaker(pairs) if pairs else {}
        parent = {r: r for r in roots}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        index_of = {s: i for i, s in enumerate(sid)}
        for p in pairs:
            same = bool(decisions.get(p.pair_id, False))
            tiebreaks.append((p, same))
            if same:
                ra, rb = find(index_of[p.a_id]), find(index_of[p.b_id])
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
        merged: dict[int, list[int]] = {}
        for r, g in zip(roots, groups, strict=True):
            merged.setdefault(find(r), []).extend(g)
        groups = [sorted(merged[r]) for r in sorted(merged)]

    clusters = [[sid[i] for i in g] for g in groups]
    clusters.sort(key=lambda c: c[0])
    return ClusterResult(
        ids=sid, clusters=clusters, vectors=dict(zip(sid, vecs, strict=True)), tiebreaks=tiebreaks
    )


# ---- identity across runs -------------------------------------------------------------------------


@dataclass(frozen=True)
class PriorRef:
    """An earlier story to match against: its id, a representative text, and its item URLs."""

    ref_id: str
    text: str
    urls: frozenset[str]


@dataclass(frozen=True)
class PriorMatch:
    cluster_index: int
    ref_id: str
    method: str  # "url" | "embedding" | "tiebreak"
    similarity: float
    shared_urls: int


async def match_to_prior(
    clusters: Sequence[Sequence[str]],
    cluster_texts_: Sequence[str],
    cluster_urls: Sequence[set[str]],
    item_vectors: Sequence[Sequence[Vector]],
    priors: Sequence[PriorRef],
    *,
    embedder: Embedder,
    tau: float = 0.78,
    band: float = 0.08,
    tiebreaker: Tiebreaker | None = None,
    max_tiebreak_pairs: int = 40,
) -> list[PriorMatch]:
    """Best earlier story for each cluster, if any (at most one per cluster).

    Ranking per cluster: most shared URLs, then highest average similarity between the cluster's
    item vectors and the prior story's text, then the prior's id. Ambiguous-band candidates (no
    shared URL, similarity in [tau - band, tau)) are confirmed by the tiebreaker.
    """
    if not priors or not clusters:
        return []
    priors = sorted(priors, key=lambda p: p.ref_id)
    pvecs = embedder.embed([p.text for p in priors])
    matches: list[PriorMatch] = []
    pending: list[tuple[int, PriorRef, float, TiebreakPair]] = []
    for ci in range(len(clusters)):
        best: tuple | None = None
        for p, pv in zip(priors, pvecs, strict=True):
            shared = len(cluster_urls[ci] & p.urls)
            vs = item_vectors[ci]
            s = sum(sum(a * b for a, b in zip(v, pv, strict=True)) for v in vs) / max(1, len(vs))
            if shared == 0 and s < tau - band:
                continue
            key = (shared, _key(s), p.ref_id)
            if best is None or key[:2] > best[0][:2]:
                best = (key, p, s, shared)
        if best is None:
            continue
        _, p, s, shared = best
        if shared > 0:
            matches.append(PriorMatch(ci, p.ref_id, "url", round(s, 4), shared))
        elif s >= tau:
            matches.append(PriorMatch(ci, p.ref_id, "embedding", round(s, 4), 0))
        elif tiebreaker is not None:
            pid = hashlib.sha1(f"prior|{clusters[ci][0]}|{p.ref_id}".encode()).hexdigest()[:12]
            pair = TiebreakPair(pid, clusters[ci][0], p.ref_id, cluster_texts_[ci], p.text, round(s, 4))
            pending.append((ci, p, s, pair))
    if pending:
        pending.sort(key=lambda x: (-_key(x[2]), x[3].a_id))
        pending = pending[:max_tiebreak_pairs]
        decisions = await tiebreaker([x[3] for x in pending])  # type: ignore[misc]
        for ci, p, s, pair in pending:
            if decisions.get(pair.pair_id, False):
                matches.append(PriorMatch(ci, p.ref_id, "tiebreak", round(s, 4), 0))
    matches.sort(key=lambda m: m.cluster_index)
    return matches
