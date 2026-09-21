from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path


def work_item_id(state_dir: Path) -> str:
    """Persist an identity until the caller archives or removes the state directory.

    Concurrent callers publish a complete file using an atomic hard link. An
    invalid existing identity fails closed instead of silently changing session.
    """
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / "work_item_id"
    if not target.exists():
        fd, name = tempfile.mkstemp(prefix=".work-item-", dir=state_dir)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(str(uuid.uuid4()) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    return str(uuid.UUID(target.read_text(encoding="utf-8").strip()))


def session_id(work_item: str, role: str, *, namespace: str = "agentflow") -> str:
    """Return a stable UUID scoped to a work item, role and application."""
    if not all(isinstance(value, str) and value.strip() for value in (work_item, role, namespace)):
        raise ValueError("work_item, role and namespace must be nonempty strings")
    identity = json.dumps([namespace, work_item, role], ensure_ascii=False)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))


def tree_files(
    root: Path,
    *,
    exclude_root_dirs: tuple[str, ...] = (),
    exclude_root_prefixes: tuple[str, ...] = (),
    exclude_dirs: tuple[str, ...] = (),
    exclude_suffixes: tuple[str, ...] = (),
) -> list[Path]:
    """List regular files deterministically, pruning excluded and symlink dirs.

    Exclusion policy belongs to the application. Traversal failures propagate;
    an unreadable tree must never produce a partial successful fingerprint.
    """
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(root)
    files: list[Path] = []

    def fail(error: OSError) -> None:
        raise error

    for directory, dirs, names in os.walk(root, followlinks=False, onerror=fail):
        parent = Path(directory)
        dirs[:] = [
            name for name in dirs
            if name not in exclude_dirs
            and not (parent / name).is_symlink()
            and not (parent == root and (
                name in exclude_root_dirs or name.startswith(exclude_root_prefixes)
            ))
        ]
        for name in names:
            path = parent / name
            if path.is_symlink() or not path.is_file():
                continue
            if parent == root and (name in exclude_root_dirs or name.startswith(exclude_root_prefixes)):
                continue
            if path.suffix not in exclude_suffixes:
                files.append(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def tree_digest(root: Path, files: list[Path]) -> str:
    """Hash sorted relative paths and file contents, independent of metadata.

    Matches the authoring workflow's existing frozen-tree digest format.
    Callers must quiesce writers while collecting and hashing the file list.
    """
    root = Path(root)
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if ".." in relative.parts or path.is_symlink():
            raise ValueError(f"Invalid artifact path: {relative}")
        path.resolve().relative_to(root.resolve())
        content = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                content.update(chunk)
        digest.update(f"{relative.as_posix()}\0{content.hexdigest()}\n".encode("utf-8"))
    return digest.hexdigest()
