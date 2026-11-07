"""The clustering module is reused by the evaluation track: test it standalone, offline."""

import random

import pytest

from app.agents.newsroom.clustering import (
    HashingEmbedder,
    PriorRef,
    TiebreakPair,
    agglomerative,
    cluster_texts,
    cosine_matrix,
    match_to_prior,
)

TEXTS = {
    "a1": "Acme Labs releases Falcon-3 open-weight language model",
    "a2": "Acme Labs releases open-weight Falcon-3 model under Apache license",
    "a3": "Falcon-3: Acme Labs' new open-weight language model",
    "b1": "Granite Ventures leads $50M round in chip startup Volt",
    "b2": "Chip startup Volt raises $50M round led by Granite Ventures",
    "c1": "Sparse attention for protein structure prediction",
    "d1": "City council debates new parking rules",
}
EMB = HashingEmbedder(name_weight=4)


async def run(ids, **kw):
    return await cluster_texts(ids, [TEXTS[i] for i in ids], embedder=EMB, **kw)


def test_agglomerative_average_linkage_threshold():
    sim = [
        [1.0, 0.9, 0.8, 0.1],
        [0.9, 1.0, 0.7, 0.1],
        [0.8, 0.7, 1.0, 0.1],
        [0.1, 0.1, 0.1, 1.0],
    ]
    # 0 and 1 merge first (0.9); then 2 joins only if avg(0.8, 0.7) = 0.75 >= tau
    clusters, _ = agglomerative(sim, 0.74)
    assert clusters == [[0, 1, 2], [3]]
    clusters, _ = agglomerative(sim, 0.78)  # single linkage would still add 2 (0.8); average doesn't
    assert clusters == [[0, 1], [2], [3]]


def test_cosine_matrix_unit_diagonal():
    m = cosine_matrix(EMB.embed(["one two", "three four"]))
    assert m[0][0] == pytest.approx(1.0)
    assert m[0][1] == pytest.approx(m[1][0])


async def test_clusters_group_events_and_are_deterministic():
    ids = list(TEXTS)
    r1 = await run(ids, tau=0.5, band=0.0)
    assert ["a1", "a2", "a3"] in r1.clusters
    assert ["b1", "b2"] in r1.clusters
    assert ["c1"] in r1.clusters and ["d1"] in r1.clusters
    for seed in range(5):
        shuffled = ids[:]
        random.Random(seed).shuffle(shuffled)
        r2 = await run(shuffled, tau=0.5, band=0.0)
        assert r2.clusters == r1.clusters


async def test_ambiguous_band_goes_to_tiebreaker():
    seen: list[TiebreakPair] = []

    async def yes(pairs):
        seen.extend(pairs)
        return {p.pair_id: True for p in pairs}

    async def no(pairs):
        return {}

    # tau so high nothing merges directly; band wide enough to cover the Acme pairs
    r_no = await run(["a1", "a2", "b1"], tau=0.99, band=0.6, tiebreaker=no)
    assert r_no.clusters == [["a1"], ["a2"], ["b1"]]
    r_yes = await run(["a1", "a2", "b1"], tau=0.99, band=0.6, tiebreaker=yes)
    assert all(0.39 <= p.similarity < 0.99 for p in seen)
    assert ["a1", "a2"] in r_yes.clusters or ["a1", "a2", "b1"] in r_yes.clusters
    assert len(r_yes.tiebreaks) == len(seen)


async def test_tiebreak_cap():
    calls = []

    async def tb(pairs):
        calls.append(len(pairs))
        return {}

    await run(list(TEXTS), tau=0.99, band=0.99, tiebreaker=tb, max_tiebreak_pairs=3)
    assert calls == [3]


async def test_match_to_prior_url_embedding_and_tiebreak():
    ids = ["a1", "a2", "b1"]
    r = await run(ids, tau=0.5, band=0.0)
    groups = r.clusters
    texts = [TEXTS[g[0]] for g in groups]
    urls = [{f"https://x.example/{m}" for m in g} for g in groups]
    vecs = [[r.vectors[m] for m in g] for g in groups]
    priors = [
        PriorRef("old-acme", "Acme Labs releases Falcon-3 open-weight model", frozenset()),
        PriorRef("old-volt", "Unrelated", frozenset({"https://x.example/b1"})),
    ]
    m = await match_to_prior(groups, texts, urls, vecs, priors, embedder=EMB, tau=0.5, band=0.0)
    by = {groups[x.cluster_index][0]: x for x in m}
    assert by["a1"].ref_id == "old-acme" and by["a1"].method == "embedding"
    assert by["b1"].ref_id == "old-volt" and by["b1"].method == "url"

    async def yes(pairs):
        return {p.pair_id: True for p in pairs}

    m2 = await match_to_prior(
        groups, texts, urls, vecs, priors[:1], embedder=EMB, tau=0.99, band=0.9, tiebreaker=yes
    )
    assert any(x.method == "tiebreak" and x.ref_id == "old-acme" for x in m2)


async def test_ids_must_be_unique():
    with pytest.raises(ValueError):
        await cluster_texts(["a", "a"], ["x", "y"], embedder=EMB)


@pytest.mark.parametrize("seed", range(6))
def test_agglomerative_numpy_matches_pure_python(seed):
    np = pytest.importorskip("numpy")
    from app.agents.newsroom.clustering import _agglomerative_np, _agglomerative_py

    rng = np.random.default_rng(seed)
    base = rng.normal(size=(12, 32))
    v = base[rng.integers(0, 12, size=90)] + rng.normal(scale=0.45, size=(90, 32))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    sim = (v @ v.T).tolist()
    for tau, floor in [(0.78, 0.70), (0.6, None), (0.9, 0.82)]:
        cp, lp = _agglomerative_py(sim, tau, floor)
        cn, ln = _agglomerative_np(sim, tau, floor)
        assert cn == cp
        assert ln["roots"] == lp["roots"]
        assert ln["avg"].keys() == lp["avg"].keys()
        for k, val in lp["avg"].items():
            assert ln["avg"][k] == pytest.approx(val, abs=1e-12)
