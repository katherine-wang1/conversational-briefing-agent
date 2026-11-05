"""Coverage matrix for the simulated-user panel (PRD §9, SIM-2).

Each profile is placed into a category on every PRD §9 dimension by explicit rules over its
parameters (no hand tags that could drift from the data). `check_coverage` verifies that the test
panel covers every required category and that every profile is valid against the taxonomy.

Run: `python -m eval.sim.coverage` prints the matrix and exits non-zero on a gap.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable

from eval.sim.profile import SimProfile, load_panel, validate_taxonomy

# dimension -> categories that the test panel must cover
REQUIRED: dict[str, set[str]] = {
    "role": {"researcher", "builder", "pm", "investor", "policy_safety", "journalist", "student", "generalist"},
    "breadth": {"specialist", "medium", "generalist"},
    "drift": {"stable", "periodic", "sudden", "occasional"},
    "length": {"short", "long"},
    "depth": {"headline", "deep"},
    "technicality": {"plain", "high"},
    "style": {"numbers", "narrative"},
    "explicitness": {"explicit", "implicit"},
    "followups": {"many", "few"},
    "time": {"rushed", "relaxed"},
    "openness": {"curious", "dismissive"},
    "consistency": {"clean", "noisy"},
}


def _band(x: float, lo: float, hi: float, low: str, mid: str, high: str) -> str:
    return low if x <= lo else high if x >= hi else mid


def categorize(p: SimProfile) -> dict[str, str]:
    """Rules (documented here so the datasheet can quote them)."""
    n = len(p.hidden_interests)
    adds = sum(1 for e in p.drift if e.op == "add")
    if any(e.op == "shift" for e in p.drift):
        drift = "sudden"
    elif adds >= 2:
        drift = "periodic"  # e.g. a new interest every week
    elif p.drift:
        drift = "occasional"
    else:
        drift = "stable"
    tb = p.behavior.time_budget_s
    if tb.dist == "fixed":
        rushed_share = 1.0 if (tb.fixed_s or 0) <= 150 else 0.0
    elif tb.dist == "uniform":
        rushed_share = 1.0 if max(tb.rushed[1], tb.relaxed[1]) <= 150 else 0.0
    else:
        rushed_share = tb.rushed_p
    b = p.behavior
    return {
        "role": p.role,
        "breadth": "specialist" if n <= 2 else "generalist" if n >= 6 else "medium",
        "drift": drift,
        "length": _band(p.delivery.length_s, 150, 300, "short", "medium", "long"),
        "depth": p.delivery.depth,
        "technicality": p.delivery.technicality,
        "style": p.delivery.style,
        "explicitness": _band(b.explicitness, 0.3, 0.6, "implicit", "mixed", "explicit"),
        "followups": _band(b.followup_rate, 0.2, 0.6, "few", "some", "many"),
        "time": _band(rushed_share, 0.2, 0.6, "relaxed", "mixed", "rushed"),
        "openness": _band(b.exploration_openness, 0.2, 0.7, "dismissive", "moderate", "curious"),
        "consistency": _band(b.noise, 0.05, 0.2, "clean", "moderate", "noisy"),
    }


def matrix(profiles: Iterable[SimProfile]) -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {d: {} for d in REQUIRED}
    for p in profiles:
        for dim, cat in categorize(p).items():
            out[dim].setdefault(cat, []).append(p.id)
    return out


def latent_problems(p: SimProfile) -> list[str]:
    """A latent interest must not be something the profile already states or holds as hidden."""
    errs = []
    stated = {t.lower() for i in p.hidden_interests for t in (*i.entities, *i.keywords)}
    hidden_subs = {i.subtopic for i in p.hidden_interests}
    for li in p.latent_interests:
        terms = {t.lower() for t in li.terms}
        if terms & stated:
            errs.append(f"{p.id}: latent terms {sorted(terms & stated)} are already hidden interests")
        if li.subtopic in hidden_subs and not li.terms:
            errs.append(f"{p.id}: latent interest on hidden subtopic {li.subtopic} needs specific terms")
        for t in terms:
            if t in p.onboarding.lower():
                errs.append(f"{p.id}: latent term {t!r} appears in onboarding text")
    return errs


def check_coverage(panel: str = "panel_v1", window_days: dict[str, int] | None = None) -> list[str]:
    """Return a list of problems (empty = OK)."""
    window_days = window_days or {"test": 30, "dev": 10, "scenario": 30, "kate": 30}
    profiles = load_panel(panel)
    problems: list[str] = []
    counts = {s: sum(1 for p in profiles if p.split == s) for s in ("test", "dev", "scenario", "kate")}
    expected = {"test": 12, "dev": 6, "scenario": 4, "kate": 1}
    for s, n in expected.items():
        if counts[s] != n:
            problems.append(f"expected {n} {s} profiles, found {counts[s]}")
    for p in profiles:
        problems += validate_taxonomy(p)
        problems += latent_problems(p)
        for ev in p.drift:
            if ev.day >= window_days[p.split]:
                problems.append(f"{p.id}: drift day {ev.day} outside the {p.split} window")
        if p.split == "kate" and not p.placeholder and p.calibrated_to != "kate":
            problems.append(f"{p.id}: kate profile must set calibrated_to: kate")
    test = [p for p in profiles if p.split == "test" and not p.placeholder]
    m = matrix(test)
    for dim, cats in REQUIRED.items():
        missing = cats - set(m[dim])
        if missing:
            problems.append(f"test panel misses {dim} categories: {sorted(missing)}")
    scen = {categorize(p)["drift"] for p in profiles if p.split == "scenario"}
    if "sudden" not in scen:
        problems.append("scenario users need a sudden-drift user")
    scen_time = {categorize(p)["time"] for p in profiles if p.split == "scenario"}
    if "rushed" not in scen_time:
        problems.append("scenario users need an always-rushed user")
    return problems


def render_matrix(panel: str = "panel_v1", split: str = "test") -> str:
    profiles = [p for p in load_panel(panel) if p.split == split]
    dims = list(REQUIRED)
    lines = ["| profile | " + " | ".join(dims) + " |", "|" + "---|" * (len(dims) + 1)]
    for p in profiles:
        c = categorize(p)
        lines.append(f"| {p.id} | " + " | ".join(c[d] for d in dims) + " |")
    return "\n".join(lines)


def main() -> int:
    print(render_matrix())
    problems = check_coverage()
    for pr in problems:
        print("PROBLEM:", pr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
