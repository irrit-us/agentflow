"""Lite 流水线演示：YAML 描述 DAG + 后台执行 + 内置 monitor UI。

用法：
    export OPENAI_API_KEY=sk-...
    python examples/lite_pipeline_demo.py

然后访问 http://127.0.0.1:8600/ 查看实时监控界面（节点状态、阻塞列表、
点击节点查看完整对话）。可拖动节点位置，布局存在浏览器 localStorage。

环境变量：
    OPENAI_API_KEY    API 密钥（必填，除非用本地端点）
    OPENAI_BASE_URL   覆盖默认 https://api.openai.com/v1（可指向本地 vLLM/Ollama）
    LITE_MODEL        模型名，默认 gpt-4o-mini
    LITE_GRAPH        图定义文件路径，默认 examples/lite_pipeline.yaml
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from agentflow.lite import (
    GraphRunner,
    LiteLLMClient,
    create_app,
    load_graph,
    make_agent_factory,
    make_llm_health_probe,
)

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    client = LiteLLMClient(
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        api_key=os.environ.get("OPENAI_API_KEY"),
        api_key_env="LITE_API_KEY",
    )
    graph = load_graph(os.environ.get("LITE_GRAPH", str(ROOT / "examples" / "lite_pipeline.yaml")))
    factory = make_agent_factory(
        client=client,
        default_model=os.environ.get("LITE_MODEL", "gpt-4o-mini"),
    )
    runner = GraphRunner(graph, factory)
    runner.run_in_background()  # 流水线在 daemon 线程中执行，HTTP 服务正常响应

    app = create_app(runner, health_probe=make_llm_health_probe(client))
    print("monitor UI: http://127.0.0.1:8600/")
    uvicorn.run(app, host="127.0.0.1", port=8600, log_level="warning")


if __name__ == "__main__":
    main()
