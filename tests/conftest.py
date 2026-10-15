"""Test configuration: isolated SQLite database and snapshot directory per session."""

import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="newsintel-test-"))
os.environ["NEWSINTEL_DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["NEWSINTEL_SNAPSHOT_DIR"] = str(_tmp / "snapshots")
os.environ["NEWSINTEL_RECORD_FIXTURES"] = "false"

import pytest  # noqa: E402

from newsintel.store.db import init_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _db():
    init_db(drop=True)
    yield
