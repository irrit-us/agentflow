from __future__ import annotations

from pathlib import Path

from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.runners.base import LaunchPlan
from agentflow.runners.local import LocalRunner
from agentflow.specs import ContainerTarget, NodeSpec


class ContainerRunner(LocalRunner):
    def _container_prepared(self, node: NodeSpec, prepared: PreparedExecution, paths: ExecutionPaths) -> PreparedExecution:
        target = node.target
        if not isinstance(target, ContainerTarget):
            raise TypeError("ContainerRunner requires a ContainerTarget")

        app_mount = target.app_mount
        command = [target.engine, "run", "--rm"]
        if prepared.stdin is not None:
            command.append("-i")
        command.extend(
            [
                "-v",
                f"{paths.host_workdir}:{target.workdir_mount}",
                "-v",
                f"{paths.host_runtime_dir}:{target.runtime_mount}",
                "-v",
                f"{paths.app_root}:{app_mount}",
                "-w",
                prepared.cwd,
            ]
        )
        for key, value in prepared.env.items():
            command.extend(["-e", f"{key}={value}"])
        if app_mount:
            command.extend(["-e", f"PYTHONPATH={app_mount}"])
        command.extend(target.extra_args)
        if target.entrypoint:
            command.extend(["--entrypoint", target.entrypoint])
        command.append(target.image)
        command.extend(prepared.command)
        container_prepared = PreparedExecution(
            command=command,
            env={},
            cwd=str(paths.host_workdir),
            trace_kind=prepared.trace_kind,
            runtime_files={},
            stdin=prepared.stdin,
        )
        return container_prepared

    def plan_execution(
        self,
        node: NodeSpec,
        prepared: PreparedExecution,
        paths: ExecutionPaths,
    ) -> LaunchPlan:
        target = node.target
        if not isinstance(target, ContainerTarget):
            raise TypeError("ContainerRunner requires a ContainerTarget")
        container_prepared = self._container_prepared(node, prepared, paths)
        return LaunchPlan(
            kind="container",
            command=list(container_prepared.command),
            env={},
            cwd=container_prepared.cwd,
            stdin=container_prepared.stdin,
            runtime_files=sorted(prepared.runtime_files),
            payload={
                "image": target.image,
                "engine": target.engine,
                "workdir": prepared.cwd,
                "env": dict(prepared.env),
            },
        )

    async def execute(self, node: NodeSpec, prepared: PreparedExecution, paths: ExecutionPaths, on_output, should_cancel):
        self.materialize_runtime_files(paths.host_runtime_dir, prepared.runtime_files)
        container_prepared = self._container_prepared(node, prepared, paths)
        return await super().execute(node, container_prepared, paths, on_output, should_cancel)
