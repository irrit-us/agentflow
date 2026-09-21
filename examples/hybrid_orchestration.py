from __future__ import annotations

from agentflow import Graph, codex, command, fanout, python_node

# This capacity and the authorization rule belong to this example application.
CAPACITY = 3

with Graph("hybrid-orchestration", use_worktree=False) as graph:
    propose = codex(
        task_id="propose",
        prompt='Suggest a useful parallelism count. Return only JSON: {"count": integer}.',
    )
    authorize = python_node(
        task_id="authorize",
        code='''import json
proposal = json.loads({{ nodes.propose.output | tojson }})
count = proposal.get("count")
if type(count) is not int or count < 0:
    raise ValueError("count must be a non-negative integer")
print(json.dumps({"count": min(count, __CAPACITY__)}))
'''.replace('__CAPACITY__', str(CAPACITY)),
    )
    propose >> authorize
    workers = fanout(command(
        task_id="worker",
        argv=["python3", "-c", "print('application work')"],
        activation={"source": "authorize", "path": ["count"]},
    ), CAPACITY)
    join = python_node(task_id="join", code="print('selected work settled')", trigger_rule="all_done")
    workers >> join

if __name__ == "__main__":
    print(graph.to_json())
