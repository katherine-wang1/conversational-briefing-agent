"""Side-by-side comparison of newsroom options (docs/architecture-revisit.md § Comparison arms).

    python -m eval.newsroom_compare collect --beat ai           # headlines, stored for later reruns
    python -m eval.newsroom_compare run --beat ai --arms all    # each arm in its own process
    python -m eval.newsroom_compare pool --days 2026-11-26      # blind rating sheet
    python -m eval.newsroom_compare rate                        # rate in the terminal
    python -m eval.newsroom_compare report                      # per-arm metrics

Data lives under eval/data/newsroom_compare/<day>/<beat>/ (gitignored).
"""
