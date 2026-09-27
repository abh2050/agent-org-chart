"""AT-18 no secrets in tracked source, AT-20 static quality."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECRET = re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{30,}|AKIA[0-9A-Z]{16}")


def test_at18_no_secret_like_strings_in_source() -> None:
    offenders = [
        str(f.relative_to(ROOT))
        for folder in ("src", "tests", "gates", "docs")
        for f in (ROOT / folder).rglob("*")
        if f.is_file() and f.suffix in {".py", ".md", ".toml"} and SECRET.search(f.read_text(errors="ignore"))
    ]
    assert offenders == []


def test_at18_env_file_is_git_ignored() -> None:
    assert ".env" in (ROOT / ".gitignore").read_text().split()


def test_at20_ruff_clean_on_src() -> None:
    proc = subprocess.run([sys.executable, "-m", "ruff", "check", "src"], cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout
