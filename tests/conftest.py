import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.crm import CRM  # noqa: E402


@pytest.fixture()
def crm(tmp_path) -> CRM:
    """A CRM backed by a throwaway store, so tests never touch data/store.json."""
    return CRM(tmp_path / "store.json")
