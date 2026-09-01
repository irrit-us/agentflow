# method_workflows: method-driven audit workflows (agentflow.lite graph declarations)

Containerized graph declarations (in `agentflow.lite` format) for audit
**methods** rather than paper architectures. Unlike
`examples/paper_architectures/`, which mirrors published systems, each graph
here operationalizes one or more audit methods from the practitioner
methodology, borrowing architecture components from the flow-notes corpus
(SmartAuditFlow's static-structure-first decomposition, Finite Monkey's
business-flow-guided hypothesis generation, FORGE's structured evidence
chains, A1/SmartPoC's Foundry PoC loops).

**Graphs are built, not run** — by default they are only loaded, validated,
and printed. `--run` executes them against a real LLM endpoint plus Docker.

## Directory index

| File | Methods | Architecture |
|---|---|---|
| `constraint_callchain_consistency.yaml` | 11 (constraint consistency) + 12 (call-chain semantics) | Shared static-structure extraction → two discovery branches (business-flow reconstruction → function business abstraction → constraint matrix → cross-entry comparison; call-graph enumeration → node semantic annotation → end-to-end composition → anomalous-chain discovery) → shared confirmation closure (weak-path exploit construction → five-question confirmation → Foundry regression test) |
| `semantic_graph_orchestrator.yaml` | Self-orchestrating audit over a function-level semantic effect graph (Graph-of-Semantic project) | Semantic-graph construction (branches as child nodes, loops collapsed to UNVERIFIED summaries) → constraint seeding (five families) → ledger-driven orchestration (single feedback node, `max_iterations=40`: dual ledgers, one tool action per step, stall-driven stop) → independent verification (separate container: loop-summary induction, SMT/Foundry confirmation) → coverage-and-report (five-question evidence) |

Methods 11 and 12 share one graph because they consume the same static
structure and converge on the same unified confirmation closure (minimal
reproduction → attacker model → reachability/controllability → violated
property → primitives → end-to-end attack chain → impact → regression test,
with final evidence answering Reachability, Controllability, Violation,
Primitive, Impact).

## Usage

```bash
# Build graphs: load/validate all YAMLs and print topology plus the
# node-to-image mapping, without executing anything
python examples/method_workflows/build_all.py

# Execute against a real LLM endpoint plus Docker
LITE_BASE_URL=http://localhost:8000/v1 python examples/method_workflows/build_all.py --run
```

## Conventions

- Same conventions as `examples/paper_architectures/`: feedback loops are
  collapsed into single nodes whose inner iteration is carried by
  `max_iterations`; inputs are bind-mounted read-only; data transfer between
  containers uses a shared named volume mounted rw.
- Prompts are written in English per repo policy; the source methodology text
  is Chinese.
