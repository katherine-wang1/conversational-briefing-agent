"""Pool the arms' stories into one blind list, rate them, and report per-arm metrics."""

from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from pathlib import Path

from eval.newsroom_compare.common import ROOT, CompareStory, is_paywalled, read_arm

GAIN = {"must": 2, "nice": 1, "skip": 0}
POOL_DEPTH = 15  # top-N per arm that go into the blind pool


def _same(a: CompareStory, b: dict) -> bool:
    from rapidfuzz import fuzz

    if set(a.urls) & set(b["urls"]):
        return True
    return fuzz.token_set_ratio(a.headline.lower(), b["headline"].lower()) >= 82


def build_pool(days: list[str], beats: list[str], depth: int = POOL_DEPTH, seed: int = 7) -> Path:
    items: list[dict] = []
    for day in days:
        for beat in beats:
            d = ROOT / day / beat
            for path in sorted(d.glob("*.json")):
                if path.name in ("collect_meta.json",) or path.name.endswith(".ledger.jsonl"):
                    continue
                res = read_arm(path)
                for s in res.stories[:depth]:
                    same_slice = (it for it in items if it["day"] == day and it["beat"] == beat)
                    match = next((it for it in same_slice if _same(s, it)), None)
                    if match is None:
                        match = {
                            "id": f"{day}:{beat}:{len(items)}",
                            "day": day,
                            "beat": beat,
                            "headline": s.headline,
                            "summary": s.summary,
                            "urls": list(s.urls),
                            "outlets": list(s.outlets),
                            "found_by": {},
                        }
                        items.append(match)
                    else:
                        match["urls"] = list(dict.fromkeys(match["urls"] + s.urls))
                        match["outlets"] = list(dict.fromkeys(match["outlets"] + s.outlets))
                        if len(s.summary) > len(match["summary"]):
                            match["summary"] = s.summary
                    match["found_by"].setdefault(res.arm, s.rank)
    random.Random(seed).shuffle(items)
    out = ROOT / f"pool_{'_'.join(days)}.json"
    out.write_text(json.dumps(items, indent=1))
    print(f"{len(items)} pooled stories -> {out}")
    return out


def rate(pool_path: Path) -> None:
    items = json.loads(pool_path.read_text())
    ratings_path = pool_path.with_suffix(".ratings.jsonl")
    done = set()
    if ratings_path.exists():
        done = {json.loads(line)["id"] for line in ratings_path.read_text().splitlines() if line}
    todo = [it for it in items if it["id"] not in done]
    print(f"{len(todo)} to rate ({len(done)} done). 1 = must-know, 2 = nice-to-know, 3 = skip, q = quit.\n")
    keys = {"1": "must", "2": "nice", "3": "skip"}
    with ratings_path.open("a") as f:
        for n, it in enumerate(todo, 1):
            print(f"[{n}/{len(todo)}] ({it['beat']}, {it['day']}) {it['headline']}")
            if it["summary"]:
                print(f"    {it['summary']}")
            print(f"    outlets: {', '.join(it['outlets'][:6])}")
            while (k := input("> ").strip().lower()) not in (*keys, "q"):
                pass
            if k == "q":
                return
            f.write(json.dumps({"id": it["id"], "rating": keys[k]}) + "\n")
            f.flush()


def report(pool_path: Path) -> str:
    items = json.loads(pool_path.read_text())
    ratings_path = pool_path.with_suffix(".ratings.jsonl")
    ratings = {}
    if ratings_path.exists():
        for line in ratings_path.read_text().splitlines():
            if line:
                r = json.loads(line)
                ratings[r["id"]] = r["rating"]
    days = sorted({it["day"] for it in items})
    beats = sorted({it["beat"] for it in items})
    arm_runs = defaultdict(list)
    for day in days:
        for beat in beats:
            for path in sorted((ROOT / day / beat).glob("*.json")):
                if path.name != "collect_meta.json":
                    res = read_arm(path)
                    arm_runs[res.arm].append(res)

    must_by_slice = defaultdict(set)
    for it in items:
        if ratings.get(it["id"]) == "must":
            must_by_slice[(it["day"], it["beat"])].add(it["id"])

    lines = [
        "| Arm | Runs ok | Mean time (s) | Peak MB | Cost $ / run | Paywalled outlet share | "
        "Unique stories | Rated P@10 must | Must-know recall | nDCG@10 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm, runs in sorted(arm_runs.items()):
        ok = [r for r in runs if r.ok]
        mean = lambda xs: (sum(xs) / len(xs)) if xs else float("nan")  # noqa: E731
        outlets = [o for r in ok for s in r.stories[:POOL_DEPTH] for o in s.outlets]
        pay = mean([1.0 if is_paywalled(o) else 0.0 for o in outlets])
        mine = [it for it in items if arm in it["found_by"]]
        unique = sum(1 for it in mine if len(it["found_by"]) == 1)
        top10 = [it for it in mine if it["found_by"][arm] <= 10]
        rated10 = [it for it in top10 if it["id"] in ratings]
        p10 = mean([1.0 if ratings[it["id"]] == "must" else 0.0 for it in rated10])
        rec = []
        for key, must in must_by_slice.items():
            got = {it["id"] for it in mine if (it["day"], it["beat"]) == key}
            if must:
                rec.append(len(must & got) / len(must))
        ndcg = []
        for r in ok:
            ranked = sorted(
                (it for it in mine if it["day"] == r.at.date().isoformat() and it["beat"] == r.beat),
                key=lambda it: it["found_by"][arm],
            )[:10]
            gains = [GAIN.get(ratings.get(it["id"], ""), 0) for it in ranked]
            pool_gains = sorted(
                (GAIN.get(ratings.get(it["id"], ""), 0) for it in items if it["beat"] == r.beat),
                reverse=True,
            )[:10]
            dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
            idcg = sum(g / math.log2(i + 2) for i, g in enumerate(pool_gains))
            if idcg:
                ndcg.append(dcg / idcg)
        costs = [r.cost_usd for r in ok if r.cost_usd is not None]
        lines.append(
            f"| {arm} | {len(ok)}/{len(runs)} | {mean([r.wall_s or 0 for r in ok]):.0f} | "
            f"{mean([r.peak_rss_mb or 0 for r in runs]):.0f} | {mean(costs):.3f} | {pay:.0%} | {unique} | "
            f"{p10:.2f} | {mean(rec):.2f} | {mean(ndcg):.2f} |"
        )
    lines.append(f"\n{len(items)} pooled stories, {len(ratings)} rated.")
    return "\n".join(lines)
