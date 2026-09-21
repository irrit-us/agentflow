from __future__ import annotations

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agentflow.lifecycle import session_id, tree_digest, tree_files, work_item_id


def test_identity_survives_retries_and_changes_after_archive(tmp_path):
    state = tmp_path / "state"
    with ThreadPoolExecutor(max_workers=8) as pool:
        identities = list(pool.map(lambda _: work_item_id(state), range(32)))
    assert len(set(identities)) == 1
    first = session_id(identities[0], "designer")
    assert str(uuid.UUID(first)) == first
    assert first == session_id(work_item_id(state), "designer")
    assert first != session_id(identities[0], "reviewer")
    assert first != session_id(identities[0], "designer", namespace="another")
    state.rename(tmp_path / "archived")
    assert first != session_id(work_item_id(state), "designer")


def test_invalid_identity_is_not_silently_replaced(tmp_path):
    (tmp_path / "work_item_id").write_text("broken")
    with pytest.raises(ValueError):
        work_item_id(tmp_path)
    assert (tmp_path / "work_item_id").read_text() == "broken"


def test_digest_compatible_and_prunes_runtime(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "task").write_bytes(b"task")
    (tmp_path / "jobs.123").mkdir()
    (tmp_path / "jobs.123" / "private").write_bytes(b"runtime")
    (tmp_path / "src" / "__pycache__").mkdir()
    (tmp_path / "src" / "__pycache__" / "cache").write_bytes(b"cache")
    (tmp_path / "src" / "cache.pyc").write_bytes(b"cache")
    import agentflow.lifecycle as lifecycle
    original_scandir = lifecycle.os.scandir

    def guarded_scandir(path):
        assert Path(path).name not in {"jobs.123", "__pycache__"}
        return original_scandir(path)

    monkeypatch.setattr(lifecycle.os, "scandir", guarded_scandir)
    files = tree_files(tmp_path, exclude_root_prefixes=("jobs.",),
                       exclude_dirs=("__pycache__",), exclude_suffixes=(".pyc",))
    assert files == [tmp_path / "src" / "task"]
    expected = hashlib.sha256(f"src/task\0{hashlib.sha256(b'task').hexdigest()}\n".encode()).hexdigest()
    assert tree_digest(tmp_path, files) == expected
    (tmp_path / "src" / "task").write_bytes(b"changed")
    assert tree_digest(tmp_path, files) != expected


def test_digest_rejects_escape_and_missing_tree(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside").write_text("outside")
    with pytest.raises(ValueError):
        tree_digest(root, [root / ".." / "outside"])
    with pytest.raises(NotADirectoryError):
        tree_files(root / "missing")


def test_digest_is_order_independent(tmp_path):
    files = [tmp_path / name for name in ("b", "a")]
    for file in files:
        file.write_text(file.name)
    assert tree_digest(tmp_path, files) == tree_digest(tmp_path, files[::-1])
