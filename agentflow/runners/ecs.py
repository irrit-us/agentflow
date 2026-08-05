"""ECS Fargate runner for AgentFlow nodes."""

from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import time

from agentflow.prepared import ExecutionPaths, PreparedExecution
from agentflow.runners.base import (
    CancelCallback,
    LaunchPlan,
    RawExecutionResult,
    Runner,
    StreamCallback,
)
from agentflow.specs import NodeSpec, normalize_agent_name


class ECSRunner(Runner):
    """Execute agent nodes as ECS Fargate tasks."""

    def _build_and_push_image(self, region: str, agents: list[str], base_image: str | None, on_status) -> str:
        """Build a Docker image with agents and push to ECR. Returns image URI."""
        import boto3

        from agentflow.cloud.installer import agent_dockerfile

        account = boto3.client("sts").get_caller_identity()["Account"]
        repo_name = "agentflow-agents"
        ecr = boto3.client("ecr", region_name=region)

        # Ensure ECR repo
        try:
            ecr.create_repository(repositoryName=repo_name)
        except ecr.exceptions.RepositoryAlreadyExistsException:
            pass

        image_uri = f"{account}.dkr.ecr.{region}.amazonaws.com/{repo_name}:latest"

        # Docker login to ECR
        token = ecr.get_authorization_token()
        auth = token["authorizationData"][0]
        registry = auth["proxyEndpoint"]
        username, password = base64.b64decode(auth["authorizationToken"]).decode().split(":")
        subprocess.run(
            ["docker", "login", "--username", username, "--password-stdin", registry],
            input=password, text=True, capture_output=True, check=True,
        )

        # Build image
        dockerfile_content = agent_dockerfile(agents, base_image=base_image or "ubuntu:24.04")
        on_status(f"Building Docker image with agents: {agents}")
        result = subprocess.run(
            ["docker", "build", "-t", image_uri, "-"],
            input=dockerfile_content, text=True, capture_output=True, timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Docker build failed:\n{result.stderr}")

        # Push
        on_status(f"Pushing image to {image_uri}")
        result = subprocess.run(
            ["docker", "push", image_uri],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Docker push failed:\n{result.stderr}")

        return image_uri

    def _ensure_cluster(self, region: str, cluster_name: str) -> None:
        import boto3

        ecs = boto3.client("ecs", region_name=region)
        try:
            resp = ecs.describe_clusters(clusters=[cluster_name])
            if resp["clusters"] and resp["clusters"][0]["status"] == "ACTIVE":
                return
        except Exception:
            pass
        ecs.create_cluster(clusterName=cluster_name)

    def _ensure_log_group(self, region: str, log_group: str) -> None:
        import boto3

        logs = boto3.client("logs", region_name=region)
        try:
            logs.create_log_group(logGroupName=log_group)
        except logs.exceptions.ResourceAlreadyExistsException:
            pass

    def _ensure_execution_role(self, region: str) -> str:
        import boto3
        import json

        iam = boto3.client("iam")
        role_name = "agentflow-ecs-execution"
        try:
            resp = iam.get_role(RoleName=role_name)
            return resp["Role"]["Arn"]
        except iam.exceptions.NoSuchEntityException:
            pass

        trust = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
        )
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
        )
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/CloudWatchLogsFullAccess",
        )
        # Wait for IAM propagation
        time.sleep(10)
        return resp["Role"]["Arn"]

    def _register_task_def(self, node: NodeSpec, prepared: PreparedExecution, execution_role_arn: str, image: str) -> str:
        import boto3
        import shlex

        from agentflow.cloud.installer import agent_auth_setup

        target = node.target
        ecs = boto3.client("ecs", region_name=target.region)
        log_group = f"/agentflow/{node.id}"
        env_list = [{"name": k, "value": v} for k, v in prepared.env.items()]

        # Build the command with auth setup prepended
        agent_cmd = " ".join(shlex.quote(part) for part in prepared.command)
        auth_setup = agent_auth_setup(normalize_agent_name(node.agent), prepared.env)
        cmd_str = f"{auth_setup} && {agent_cmd}" if auth_setup else agent_cmd

        resp = ecs.register_task_definition(
            family=f"agentflow-{node.id}",
            networkMode="awsvpc",
            requiresCompatibilities=["FARGATE"],
            cpu=target.cpu,
            memory=target.memory,
            executionRoleArn=execution_role_arn,
            containerDefinitions=[
                {
                    "name": "agent",
                    "image": image,
                    "entryPoint": ["bash", "-c"],
                    "command": [cmd_str],
                    "environment": env_list,
                    "essential": True,
                    "logConfiguration": {
                        "logDriver": "awslogs",
                        "options": {
                            "awslogs-group": log_group,
                            "awslogs-region": target.region,
                            "awslogs-stream-prefix": "agent",
                        },
                    },
                }
            ],
        )
        return resp["taskDefinition"]["taskDefinitionArn"]

    def _run_task(self, node: NodeSpec, task_def_arn: str) -> str:
        import boto3

        target = node.target
        ecs = boto3.client("ecs", region_name=target.region)
        resp = ecs.run_task(
            cluster=target.cluster,
            taskDefinition=task_def_arn,
            launchType="FARGATE",
            count=1,
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": target.subnets,
                    "securityGroups": target.security_groups,
                    "assignPublicIp": "ENABLED" if target.assign_public_ip else "DISABLED",
                }
            },
        )
        if resp.get("failures"):
            raise RuntimeError(f"ECS run_task failed: {resp['failures']}")
        return resp["tasks"][0]["taskArn"]

    async def _stream_task_logs(
        self,
        logs_client: object,
        log_group: str,
        seen_tokens: set[str],
        stdout_lines: list[str],
        on_output: StreamCallback,
    ) -> None:
        """Forward newly arrived CloudWatch log events to the output callback."""
        try:
            streams = await asyncio.to_thread(
                logs_client.describe_log_streams,
                logGroupName=log_group,
                orderBy="LastEventTime",
                limit=10,
            )
            for stream in streams.get("logStreams", []):
                events = await asyncio.to_thread(
                    logs_client.get_log_events,
                    logGroupName=log_group,
                    logStreamName=stream["logStreamName"],
                    startFromHead=True,
                )
                for event in events.get("events", []):
                    token = f"{stream['logStreamName']}:{event['timestamp']}:{event['message']}"
                    if token in seen_tokens:
                        continue
                    seen_tokens.add(token)
                    line = event["message"].rstrip()
                    stdout_lines.append(line)
                    await on_output("stdout", line)
        except Exception:
            pass

    async def _wait_for_task(
        self,
        node: NodeSpec,
        task_arn: str,
        on_output: StreamCallback,
        should_cancel: CancelCallback,
    ) -> tuple[int, list[str], list[str], bool, bool]:
        """Poll the task, streaming logs incrementally.

        Honors cancellation (stops the task, exit 130) and the node timeout
        (stops the task, exit 124). Returns
        `(exit_code, stdout_lines, stderr_lines, timed_out, cancelled)`.
        """
        import boto3

        target = node.target
        ecs = await asyncio.to_thread(boto3.client, "ecs", region_name=target.region)
        logs_client = await asyncio.to_thread(boto3.client, "logs", region_name=target.region)
        log_group = f"/agentflow/{node.id}"

        stdout_lines: list[str] = []
        seen_tokens: set[str] = set()
        deadline = time.monotonic() + node.timeout_seconds if node.timeout_seconds else None

        while True:
            resp = await asyncio.to_thread(ecs.describe_tasks, cluster=target.cluster, tasks=[task_arn])
            task = resp["tasks"][0]
            status = task["lastStatus"]

            await self._stream_task_logs(logs_client, log_group, seen_tokens, stdout_lines, on_output)

            if status == "STOPPED":
                # Wait for CloudWatch log propagation, then fetch remaining logs.
                await asyncio.sleep(5)
                await self._stream_task_logs(logs_client, log_group, seen_tokens, stdout_lines, on_output)
                container = task.get("containers", [{}])[0]
                exit_code = container.get("exitCode", 1)
                reason = container.get("reason", "")
                stderr_lines = [reason] if reason else []
                for line in stderr_lines:
                    await on_output("stderr", line)
                return exit_code, stdout_lines, stderr_lines, False, False

            if should_cancel():
                await asyncio.to_thread(
                    ecs.stop_task, cluster=target.cluster, task=task_arn, reason="cancelled by agentflow"
                )
                await on_output("stderr", "Cancelled")
                return 130, stdout_lines, ["Cancelled"], False, True

            if deadline is not None and time.monotonic() >= deadline:
                await asyncio.to_thread(
                    ecs.stop_task, cluster=target.cluster, task=task_arn, reason="agentflow node timeout"
                )
                message = f"Timed out after {node.timeout_seconds}s"
                await on_output("stderr", message)
                return 124, stdout_lines, [message], True, False

            await asyncio.sleep(5)

    def plan_execution(
        self,
        node: NodeSpec,
        prepared: PreparedExecution,
        paths: ExecutionPaths,
    ) -> LaunchPlan:
        target = node.target
        return LaunchPlan(
            kind="ecs",
            command=prepared.command,
            env=prepared.env,
            cwd=None,
            payload={
                "cluster": target.cluster,
                "image": target.image,
                "region": target.region,
                "cpu": target.cpu,
                "memory": target.memory,
            },
        )

    async def execute(
        self,
        node: NodeSpec,
        prepared: PreparedExecution,
        paths: ExecutionPaths,
        on_output: StreamCallback,
        should_cancel: CancelCallback,
    ) -> RawExecutionResult:
        target = node.target
        if should_cancel():
            return RawExecutionResult(
                exit_code=130, stdout_lines=[], stderr_lines=["Cancelled"],
                timed_out=False, cancelled=True,
            )

        try:
            # Auto-discover networking if not specified
            if not target.subnets or not target.security_groups:
                from agentflow.cloud.aws import discover_networking
                await on_output("stderr", "Auto-discovering VPC networking...")
                net = await asyncio.to_thread(discover_networking, target.region)
                subnets = target.subnets or net["subnets"]
                security_groups = target.security_groups or net["security_groups"]
                await on_output("stderr", f"Using subnets={subnets}, sg={security_groups}")
                # Patch target for downstream use
                from types import SimpleNamespace
                target = SimpleNamespace(**{k: getattr(target, k) for k in target.model_fields})
                target.subnets = subnets
                target.security_groups = security_groups

            # Auto-forward local credentials if not in env
            from agentflow.cloud.aws import collect_local_credentials
            local_creds = collect_local_credentials(normalize_agent_name(node.agent))
            merged_env = {**local_creds, **prepared.env}
            prepared = PreparedExecution(
                command=prepared.command,
                env=merged_env,
                cwd=prepared.cwd,
                trace_kind=prepared.trace_kind,
                runtime_files=prepared.runtime_files,
                stdin=prepared.stdin,
            )

            # Build agent image if needed
            image = target.image
            if not image and target.install_agents:
                def _status(msg):
                    pass  # sync callback for build progress
                base = target.image or None
                await on_output("stderr", f"Building agent image with {target.install_agents} (base: {base or 'ubuntu:24.04'})...")
                image = await asyncio.to_thread(
                    self._build_and_push_image, target.region, target.install_agents, base, _status,
                )
                await on_output("stderr", f"Image ready: {image}")
            elif not image:
                image = "ubuntu:24.04"

            await on_output("stderr", f"Ensuring ECS cluster {target.cluster}...")
            await asyncio.to_thread(self._ensure_cluster, target.region, target.cluster)

            log_group = f"/agentflow/{node.id}"
            await asyncio.to_thread(self._ensure_log_group, target.region, log_group)

            await on_output("stderr", "Ensuring ECS execution role...")
            role_arn = await asyncio.to_thread(self._ensure_execution_role, target.region)

            # Create a node-like object with potentially patched target
            from types import SimpleNamespace as _NS
            effective_node = _NS(id=node.id, agent=node.agent, target=target, timeout_seconds=node.timeout_seconds)

            await on_output("stderr", f"Registering task definition (image: {image})...")
            task_def_arn = await asyncio.to_thread(self._register_task_def, effective_node, prepared, role_arn, image)

            await on_output("stderr", "Running Fargate task...")
            task_arn = await asyncio.to_thread(self._run_task, effective_node, task_def_arn)
            await on_output("stderr", f"Task {task_arn} started...")

            exit_code, stdout_lines, stderr_lines, timed_out, cancelled = await self._wait_for_task(
                node, task_arn, on_output, should_cancel,
            )

            return RawExecutionResult(
                exit_code=exit_code,
                stdout_lines=stdout_lines,
                stderr_lines=stderr_lines,
                timed_out=timed_out,
                cancelled=cancelled,
            )
        except Exception as exc:
            return RawExecutionResult(
                exit_code=1, stdout_lines=[],
                stderr_lines=[f"ECS execution failed: {exc}"],
                timed_out=False, cancelled=False,
            )
