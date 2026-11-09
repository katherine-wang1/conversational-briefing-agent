"""Load `.env` into the process environment at program start.

`Settings` (pydantic-settings) reads `.env` itself, but parts of the model and speech layers read
`os.environ` directly (`OPENROUTER_API_KEY`, `TINKER_API_KEY`, `LLM_SPEND_CAP_USD`,
`LLM_LEDGER_PATH`, `FASTEMBED_THREADS`, ...). Entry points (API server, worker, migrations, eval CLIs)
call `load_env_file()` first so a key that is only in `.env` reaches every layer. Variables already
set in the environment win (`override=False`). Library code and tests never call it, so a
developer's `.env` can't leak into the test suite.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def load_env_file(path: str | Path = ".env") -> Path | None:
    """Load `path` (relative to the working directory, like `Settings`) if it exists."""
    from dotenv import load_dotenv

    p = Path(path)
    if not p.is_file():
        return None
    load_dotenv(p, override=False)
    return p
