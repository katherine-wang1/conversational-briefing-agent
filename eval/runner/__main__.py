"""CLI for the replay runner.

python -m eval.runner validate --manifest m.yaml [--manifest m2.yaml ...]
    Load manifests, experiments and panels; check cache namespaces. No model calls.

python -m eval.runner run --manifest m.yaml --agent pkg.mod:factory --sim-client pkg.mod:factory
    Run for real. `--agent` names a zero-argument factory returning an AgentEntryPoints adapter over
    the real newsroom / chief of staff / voice / memory / follow-up components; `--sim-client` a
    factory returning the ModelClient used for `sim.user` / `sim.rate` (W-B's client).

python -m eval.runner run --manifest m.yaml --fake --runs-dir /tmp/x
    Smoke-run the whole loop with fakes only (synthetic stories, scripted simulator). No network.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from eval.runner.manifest import check_namespaces, load_manifest
from eval.runner.traces import RUNS_DIR


def _factory(spec: str) -> Any:
    mod, _, name = spec.partition(":")
    if not name:
        raise SystemExit(f"factory must look like module:callable, got {spec!r}")
    return getattr(importlib.import_module(mod), name)()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.runner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--manifest", action="append", required=True, type=Path)
    r = sub.add_parser("run")
    r.add_argument("--manifest", required=True, type=Path)
    r.add_argument("--agent")
    r.add_argument("--sim-client")
    r.add_argument("--fake", action="store_true", help="fakes only (no model calls)")
    r.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    a = ap.parse_args(argv)

    if a.cmd == "validate":
        ms = [load_manifest(p) for p in a.manifest]
        for m in ms:
            ps = m.profiles()
            print(
                f"{m.run_id}: experiment={m.experiment} seed={m.seed} "
                f"window={m.window.start}+{m.window.days}d users={len(ps)} namespace={m.namespace} "
                f"digests={m.digests_run_id} reuse={m.reuse.model_dump()}"
            )
        problems = check_namespaces(ms)
        for pr in problems:
            print("PROBLEM:", pr)
        return 1 if problems else 0

    from eval.runner.runner import run_manifest

    m = load_manifest(a.manifest)
    if a.fake:
        from eval.runner.fakes import FakeAgent, scripted_sim_client

        agent, sim_client = FakeAgent(), scripted_sim_client()
    else:
        if not (a.agent and a.sim_client):
            print(
                "run needs --agent and --sim-client (or --fake). No real agent adapter is wired yet.",
                file=sys.stderr,
            )
            return 2
        agent, sim_client = _factory(a.agent), _factory(a.sim_client)
    summary = asyncio.run(run_manifest(m, agent, sim_client, a.runs_dir))
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary["days_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
