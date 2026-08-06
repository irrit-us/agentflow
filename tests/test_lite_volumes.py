from __future__ import annotations

import subprocess

import pytest
from pydantic import ValidationError

from agentflow.lite import ContainerConfig, ContainerError, DockerExecutor, Mount, ensure_volume


class TestToDockerMount:
    def test_bind_readonly(self):
        mount = Mount(type="bind", source="/host/kb", target="/kb", read_only=True)

        assert mount.to_docker_mount() == "type=bind,src=/host/kb,dst=/kb,readonly"

    def test_bind_with_propagation(self):
        mount = Mount(
            type="bind", source="/host", target="/host", bind_propagation="rprivate"
        )

        assert (
            mount.to_docker_mount()
            == "type=bind,src=/host,dst=/host,bind-propagation=rprivate"
        )

    def test_volume_default_rw_and_nocopy(self):
        plain = Mount(type="volume", source="shared", target="/shared")
        nocopy = Mount(type="volume", source="shared", target="/shared", volume_nocopy=True)

        assert plain.to_docker_mount() == "type=volume,src=shared,dst=/shared"
        assert nocopy.to_docker_mount() == "type=volume,src=shared,dst=/shared,volume-nocopy"

    def test_tmpfs_with_size_and_mode(self):
        with pytest.warns(UserWarning, match="tmpfs"):
            mount = Mount(type="tmpfs", target="/scratch", tmpfs_size="64m", tmpfs_mode="1770")

        assert (
            mount.to_docker_mount()
            == "type=tmpfs,dst=/scratch,tmpfs-size=64m,tmpfs-mode=1770"
        )

    def test_npipe(self):
        mount = Mount(type="npipe", source=r"\\.\pipe\docker_engine", target=r"\\.\pipe\docker_engine")

        assert (
            mount.to_docker_mount()
            == r"type=npipe,src=\\.\pipe\docker_engine,dst=\\.\pipe\docker_engine"
        )


class TestMountValidation:
    def test_tmpfs_warns_about_ephemerality(self):
        with pytest.warns(UserWarning, match="tmpfs"):
            Mount(type="tmpfs", target="/tmp")

    def test_tmpfs_with_source_rejected(self):
        with pytest.raises(ValidationError, match="tmpfs mounts must not have a source"):
            Mount(type="tmpfs", source="/x", target="/tmp")

    def test_bind_without_source_rejected(self):
        with pytest.raises(ValidationError, match="bind mounts require a source"):
            Mount(type="bind", target="/kb")

    def test_volume_and_npipe_require_source(self):
        with pytest.raises(ValidationError, match="volume mounts require a source"):
            Mount(type="volume", target="/v")
        with pytest.raises(ValidationError, match="npipe mounts require a source"):
            Mount(type="npipe", target="/p")

    def test_type_specific_options_rejected_on_wrong_type(self):
        with pytest.raises(ValidationError, match="tmpfs_size is only valid for tmpfs mounts"):
            Mount(type="bind", source="/h", target="/t", tmpfs_size="64m")
        with pytest.raises(ValidationError, match="tmpfs_mode is only valid for tmpfs mounts"):
            Mount(type="volume", source="v", target="/t", tmpfs_mode="1770")
        with pytest.raises(ValidationError, match="bind_propagation is only valid for bind mounts"):
            Mount(type="volume", source="v", target="/t", bind_propagation="rprivate")
        with pytest.raises(ValidationError, match="volume_nocopy is only valid for volume mounts"):
            Mount(type="bind", source="/h", target="/t", volume_nocopy=True)


class TestEnsureVolume:
    def test_existing_volume_returns_false_without_create(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")

        monkeypatch.setattr("agentflow.lite.volumes.subprocess.run", fake_run)

        assert ensure_volume("pipeline-shared") is False
        assert calls == [["docker", "volume", "inspect", "pipeline-shared"]]

    def test_missing_volume_is_created(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            rc = 1 if argv[1:2] == ["volume"] and argv[2] == "inspect" else 0
            return subprocess.CompletedProcess(argv, rc, stdout="", stderr="")

        monkeypatch.setattr("agentflow.lite.volumes.subprocess.run", fake_run)

        assert ensure_volume("pipeline-shared") is True
        assert calls == [
            ["docker", "volume", "inspect", "pipeline-shared"],
            ["docker", "volume", "create", "pipeline-shared"],
        ]

    def test_create_failure_raises_container_error(self, monkeypatch: pytest.MonkeyPatch):
        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="permission denied")

        monkeypatch.setattr("agentflow.lite.volumes.subprocess.run", fake_run)

        with pytest.raises(ContainerError, match="permission denied"):
            ensure_volume("pipeline-shared")

    def test_missing_docker_binary_raises_container_error(self, monkeypatch: pytest.MonkeyPatch):
        def fake_run(argv, **kwargs):
            raise FileNotFoundError("docker")

        monkeypatch.setattr("agentflow.lite.volumes.subprocess.run", fake_run)

        with pytest.raises(ContainerError):
            ensure_volume("pipeline-shared")


class TestBuildArgvWithMounts:
    def test_mounts_appear_after_resources_before_workdir(self):
        executor = DockerExecutor(
            ContainerConfig(
                image="python:3.12-slim",
                mounts=[
                    Mount(type="bind", source="/host/kb", target="/kb", read_only=True),
                    Mount(type="volume", source="shared", target="/shared"),
                ],
            )
        )

        argv = executor.build_argv("ls")

        mount_args = [argv[i + 1] for i, item in enumerate(argv) if item == "--mount"]
        assert mount_args == [
            "type=bind,src=/host/kb,dst=/kb,readonly",
            "type=volume,src=shared,dst=/shared",
        ]
        assert argv.index("--cpus") < argv.index("--mount") < argv.index("-w")

    def test_workspace_and_mounts_coexist(self):
        executor = DockerExecutor(
            ContainerConfig(
                image="python:3.12-slim",
                workspace="C:/repo",
                mounts=[Mount(type="volume", source="shared", target="/shared")],
            )
        )

        argv = executor.build_argv("ls")

        assert argv[argv.index("-v") + 1] == "C:/repo:/workspace:ro"
        assert argv[argv.index("--mount") + 1] == "type=volume,src=shared,dst=/shared"

    def test_legacy_workspace_behavior_unchanged(self):
        argv = DockerExecutor(
            ContainerConfig(image="python:3.12-slim", workspace="C:/repo", read_only=False)
        ).build_argv("ls")

        assert "--mount" not in argv
        assert argv[argv.index("-v") + 1] == "C:/repo:/workspace:rw"
