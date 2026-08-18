from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LITE_DIR = REPO_ROOT / "agentflow" / "lite"


def test_lite_python_modules_start_with_future_annotations():
    missing = [
        path.name
        for path in sorted(LITE_DIR.glob("*.py"))
        if path.read_text(encoding="utf-8").splitlines()[0]
        != "from __future__ import annotations"
    ]

    assert missing == []
