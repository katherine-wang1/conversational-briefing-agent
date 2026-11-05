"""Build labelling items from real inputs (no model calls, nothing synthetic).

  python -m eval.labeling.build L1 --stories eval/data/labels.jsonl
  python -m eval.labeling.build L2 --stories eval/data/labels.jsonl
  python -m eval.labeling.build L3 --claims eval/data/judge_claims.jsonl --answers eval/data/qa_turns.jsonl

`--stories` is the hindsight-label output (eval/tasks/hindsight.py; JSONL or parquet) joined with
titles. For L1 it also writes the model-side labels the human labels are compared against:
`judge_topic.jsonl` (the LLM topic label's primary subtopic) and `judge_importance.jsonl` (the
coverage label as yes/no).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from eval.labeling.store import LABELS_DIR, build_l1, build_l2, build_l3, session_paths, write_items
from eval.sim.profile import load_panel


def read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".parquet":
        import pandas as pd  # optional dependency (extra: eval)

        return pd.read_parquet(path).to_dict("records")
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def write_judge(rows: list[dict[str, Any]], path: Path) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.labeling.build")
    ap.add_argument("session", choices=["L1", "L2", "L3"])
    ap.add_argument("--stories", type=Path)
    ap.add_argument("--claims", type=Path)
    ap.add_argument("--answers", type=Path)
    ap.add_argument("--out", type=Path, default=LABELS_DIR)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    items_path, _ = session_paths(a.session, a.out)
    if a.session in ("L1", "L2"):
        if not a.stories:
            ap.error("--stories is required for L1/L2")
        stories = read_rows(a.stories)
        if a.session == "L1":
            items = build_l1(stories, seed=a.seed)
            by_id = {str(s["story_id"]): s for s in stories}
            write_judge(
                [
                    {
                        "item_id": i.item_id,
                        "label": (list(by_id[str(i.meta["story_id"])].get("subtopics") or []) or ["other"])[
                            0
                        ],
                    }
                    for i in items
                    if i.kind == "topic"
                ],
                a.out / "judge_topic.jsonl",
            )
            write_judge(
                [
                    {"item_id": i.item_id, "label": "yes" if i.meta["stratum"] else "no"}
                    for i in items
                    if i.kind == "importance"
                ],
                a.out / "judge_importance.jsonl",
            )
        else:
            items = build_l2(stories, [p for p in load_panel() if p.split in ("test", "dev")], seed=a.seed)
    else:
        if not (a.claims and a.answers):
            ap.error("--claims and --answers are required for L3")
        items = build_l3(read_rows(a.claims), read_rows(a.answers), seed=a.seed)
    write_items(items, items_path)
    print(f"wrote {len(items)} items to {items_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
