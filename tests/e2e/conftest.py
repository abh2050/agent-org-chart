from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv

from hierarchy_company.factory import Organization, build_company

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "reports" / "e2e_results.json"


@pytest.fixture(scope="session")
def org() -> Organization:
    load_dotenv(ROOT / ".env")
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    return build_company()


@pytest.fixture(scope="session")
def e2e_results() -> Iterator[list[dict[str, Any]]]:
    """Collects one record per live run; written for gate G4 even when assertions fail."""
    runs: list[dict[str, Any]] = []
    yield runs
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps({"runs": runs}, indent=2, ensure_ascii=False))
