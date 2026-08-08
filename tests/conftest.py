from __future__ import annotations

import os
from pathlib import Path

import pytest

# Real provider CLIs that developer and CI machines commonly install on PATH.
# Local-shell tests probe a real `bash -lc` login shell, so an ambient `kimi`
# binary would satisfy `command -v kimi` and suppress the warning those tests
# expect. The `hermetic_kimi_env` fixture drops PATH entries that resolve the
# real `kimi` and points HOME at a clean dir so a login shell cannot re-add it
# from the user's startup files. The clean home mirrors a standard Linux home
# (`.profile` bridges `.bashrc`) without any kimi setup so login-startup and
# kimi-shell warnings behave like a pristine machine. The fixture is scoped to
# kimi shell-init warning tests only; other tests keep the ambient PATH (e.g.
# doctor tests exercise real `claude`/`codex` readiness probes).
_REAL_PROVIDER_CLI_NAMES = ("kimi",)


def _strip_real_provider_clis(path: str) -> str:
    entries: list[str] = []
    for entry in path.split(os.pathsep):
        if not entry:
            continue
        entry_path = Path(entry)
        if any(
            (entry_path / name).is_file() and os.access(entry_path / name, os.X_OK)
            for name in _REAL_PROVIDER_CLI_NAMES
        ):
            continue
        entries.append(entry)
    return os.pathsep.join(entries)


@pytest.fixture
def hermetic_kimi_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate kimi shell probes from a host-installed real `kimi` binary."""
    home = tmp_path / "hermetic-home"
    home.mkdir()
    (home / ".bashrc").write_text("# hermetic test home\n", encoding="utf-8")
    (home / ".profile").write_text('if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n', encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", _strip_real_provider_clis(os.environ.get("PATH", "")))
