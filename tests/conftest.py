"""Test environment, set before any test module is imported.

Several settings in backend.app.config are read once, at import. Whichever
test module happened to be imported first therefore decided them for the whole
run — so a suite could pass alone and fail together, or behave differently on a
machine that had a real API key in .env. pytest loads conftest.py before it
imports any test module, which is the only place these can be set reliably.
"""

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Never call a real model from the suite. The copilot tests assert the
# no-model behaviour, and a developer's key in .env silently changed the
# answer — tests must not depend on the machine they run on.
os.environ["LLM_PROVIDER"] = "offline"

# No artificial pacing, and never touch the developer's real database.
os.environ.setdefault("CHECK_DELAY_MS", "0")
os.environ.setdefault(
    "VO_DB_PATH", str(pathlib.Path(tempfile.gettempdir()) / "vo_tests.db"))
