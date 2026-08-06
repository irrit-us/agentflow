"""Lite volumes 演示：RAG 知识库只读挂载 + 命名卷容器间数据传输。

四种 ``--mount`` 方式：
    bind    宿主机目录挂进容器（RAG/知识库用 read_only=True 只读挂载）
    volume  Docker 命名卷（容器间共享数据：各容器 rw 挂载同一个卷）
    tmpfs   内存临时盘——注意：内容随容器销毁丢失，不能用于 RAG/KB 持久化，
            也不能用于容器间数据传输（构造 Mount 时会发出 UserWarning）
    npipe   Windows 命名管道（Windows 容器场景）

本示例：reader / writer 两个 DockerExecutor 共享命名卷 "pipeline-shared"
演示容器间数据传输；知识库目录 ./knowledge_base 以只读 bind 挂载进容器。

前置条件：Docker Desktop（docker CLI 在 PATH 上）。
"""

from __future__ import annotations

from pathlib import Path

from agentflow.lite import (
    ContainerConfig,
    DockerExecutor,
    Mount,
    ensure_volume,
)

ROOT = Path(__file__).resolve().parent.parent
SHARED_VOLUME = "pipeline-shared"


def make_executor(*, role: str) -> DockerExecutor:
    return DockerExecutor(
        ContainerConfig(
            image="python:3.12-slim",
            network="none",
            mounts=[
                # RAG：知识库只读挂载，容器无法篡改宿主机数据。
                Mount(
                    type="bind",
                    source=str(ROOT / "knowledge_base"),
                    target="/kb",
                    read_only=True,
                ),
                # 容器间数据传输：reader/writer 挂载同一个命名卷（rw）。
                Mount(type="volume", source=SHARED_VOLUME, target="/shared"),
            ],
        )
    )


def main() -> None:
    ensure_volume(SHARED_VOLUME)  # 幂等：已存在则跳过创建
    writer = make_executor(role="writer")
    reader = make_executor(role="reader")
    for executor in (writer, reader):
        if not executor.available():
            raise SystemExit("docker CLI not found on PATH; install Docker Desktop first.")

    # writer 容器写入共享卷。
    out = writer.run("echo 'findings: 3 issues' > /shared/result.txt && echo written")
    print("[writer]", out.stdout.strip() or out.stderr.strip())

    # reader 容器从另一个容器读回同一份数据，并读取只读知识库。
    out = reader.run("cat /shared/result.txt; ls /kb 2>/dev/null || echo 'kb empty (demo)'")
    print("[reader]", out.stdout.strip())


if __name__ == "__main__":
    main()
