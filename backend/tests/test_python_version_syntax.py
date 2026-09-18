"""Every module must parse as Python 3.11 — the version CI and the
production image run — not only as whatever a developer has locally.

On 2026-09-18 a backslash inside an f-string expression (legal since 3.12,
a SyntaxError before it) passed the whole suite on a 3.13 laptop, failed to
import in the 3.11 container, and took the live site down until a hot-fix.
This test is the laptop's copy of that container's opinion.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
TARGET = (3, 11)


@pytest.mark.skipif(sys.version_info < TARGET, reason="already running the target version")
@pytest.mark.parametrize("path", sorted(APP.rglob("*.py")), ids=lambda p: str(p.relative_to(APP)))
def test_module_parses_as_python_3_11(path: Path) -> None:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=TARGET)
