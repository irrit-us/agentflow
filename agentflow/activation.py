from __future__ import annotations

import json

from agentflow.specs import NodeSpec, PipelineSpec, RunRecord


def resolve_activation(node: NodeSpec, pipeline: PipelineSpec, record: RunRecord) -> tuple[bool, bool | int]:
    """Interpret a declared gate as data, never as executable model output."""
    activation = node.activation
    if activation is None:
        return True, True
    source = record.nodes[activation.source]
    value = json.loads(source.output or "")
    for key in activation.path:
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"activation path is missing: {'.'.join(activation.path)}")
        value = value[key]
    if type(value) is bool:
        return value, value
    if type(value) is not int or value < 0:
        raise ValueError("activation must resolve to a boolean or non-negative integer")
    members = next((ids for ids in pipeline.fanouts.values() if node.id in ids), [node.id])
    if value > len(members):
        raise ValueError(f"activation count {value} exceeds declared capacity {len(members)}")
    return members.index(node.id) < value, value
