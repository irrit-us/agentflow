# paper_architectures: containerized graph declarations of Agent security architectures from the papers

Containerized graph declarations (in `agentflow.lite` format) for the Agent security architectures of all downloaded papers. **Graphs are built, not run** — by default they are only loaded, validated, and printed. `--run` executes only entries that the fidelity manifest marks `runnable`; it skips `mock-runnable` and `spec-only` entries.

## Fidelity manifest

[`manifest.json`](manifest.json) records every graph's source, domain, fidelity, runtime readiness, required tools/images/licenses/devices, and exact non-runnable reasons. Its capability matrix distinguishes:

- `E` — enforced by a concrete lite runtime adapter;
- `P` — described in a prompt but not enforced;
- `-` — not required by that architecture declaration.

The tracked capabilities are real feedback, deterministic oracle, human approval, failure policy, stateful target, evidence contract, and sandboxing. `E` for sandbox means that the declared command runs through the existing container executor; it does not claim the non-bypassable policy ceiling planned in Phase 3. Manifest validation fails when the YAML corpus and manifest differ, declared images drift, or an entry claims an enforced capability for which lite has no adapter.

No current graph is marked fully runnable. The manifest identifies 45 as `spec-only` and 2 as `mock-runnable`; this is intentional architecture-fidelity reporting, not a graph build failure.

## Directory index (47 graphs, grouped by source document)

### 01-binary (binary / firmware / CTF, 12 graphs)

| File | System | Architecture |
|---|---|---|
| `cve_genie.yaml` | cve_genie | End-to-end vulnerability reproduction: CVE description → planner agents → environment setup → PoC reproduction → reproduction check |
| `cybench.yaml` | cybench | Benchmark three-stage: CTF task environment → agent solve → grader |
| `emba_pipeline.yaml` | emba_pipeline | Firmware audit chain: firmware extraction (firmware-qemu) → Ghidra decompile → LLM analysis → report |
| `firmagent.yaml` | firmagent | Firmware rehosting → directed fuzzing → taint-propagation agent → PoC generation agent → PoC validation |
| `firmhive.yaml` | firmhive | Hierarchical firmware analysis: root agent dispatch → parallel child agents → result aggregation |
| `forge.yaml` | forge | Binary ingest → root agent → delegated children (ghidra/radare2) → evidence aggregation → validation replay |
| `hptsa.yaml` | hptsa | Web pentest hierarchy: hierarchical planner → team manager → HTML simplification (playwright) → expert agents |
| `nyu_ctf_bench.yaml` | nyu_ctf_bench | Challenge container → agent solve loop → flag checker |
| `pentestgpt_vulnbot.yaml` | pentestgpt_vulnbot | Four-phase pentest: recon → planning → exploitation → reporting |
| `pwngpt.yaml` | pwngpt | Binary analysis (re-ghidra) → exploit-verify loop |
| `vexaiot.yaml` | vexaiot | IoT attack-defense chain: detection agent → attack-execution agent → exploit validation |
| `vulnhuntr.yaml` | vulnhuntr | Static scan (semgrep) → LLM call-chain analysis → report |

### 02-smart-contract (10 graphs)

| File | System | Architecture |
|---|---|---|
| `a1.yaml` | a1 | Source fetch → strategy generation → blockchain-state tool → forge verify loop |
| `actor.yaml` | actor | slither scan → multi-agent audit → human review gate → report |
| `agent4vul.yaml` | agent4vul | Commentator + bytecode CFG extractor + source input → multimodal fusion → classifier |
| `evmbench.yaml` | evmbench | Task setup → agent solve → anvil node → Rust re-execution grader |
| `llm_smartaudit.yaml` | llm_smartaudit | Broad analysis → targeted analysis → role chat → consensus vote |
| `scone_bench.yaml` | scone_bench | MEV benchmark: agent exploit → fork simulation → profit measurement |
| `smartauditflow.yaml` | smartauditflow | Static structure analysis (slither) → audit plan generation → subtask execution → RAG calibration |
| `smartify.yaml` | smartify | Language RAG → auditor → architect → code generator → refiner-validator loop |
| `smartpoc.yaml` | smartpoc | Bug report → PoC verify loop (foundry) |
| `v2e.yaml` | v2e | Contract analysis → exploit-profit loop (foundry fork) |

### 03-open-source-code (8 graphs)

| File | System | Architecture |
|---|---|---|
| `deepaudit.yaml` | deepaudit | Multi-agent audit → sandbox PoC validation → report generation |
| `dynamictester.yaml` | dynamictester | SAST aggregation (semgrep) → LLM agent → playwright dynamic verification |
| `iris.yaml` | iris | Taint-spec extraction → CodeQL analysis → LLM context filtering → findings |
| `openant.yaml` | openant | Full-pipeline audit: reachability filter → unit generation → exposure classification → vulnerability detection → adversarial verification → dynamic testing |
| `qasecclaw.yaml` | qasecclaw | SAST scan (semgrep) → verdict cache → LLM review → fail-open gate → output |
| `qrs.yaml` | qrs | Three parallel lanes — rule-synthesis agent, neural agent, symbolic agent → vulnerability discovery |
| `raptor.yaml` | raptor | Claude Code-style agent loop (afl++ assisted) → findings |
| `repoaudit.yaml` | repoaudit | Source locator → explorer → validator → report |

### 04-protocol (10 graphs)

| File | System | Architecture |
|---|---|---|
| `aflnet.yaml` | aflnet | Seed corpus → fuzz-learn loop (afl++) → crash report |
| `aip.yaml` | aip | Agent identity protocol: identity issuance → capability token chain → verifier → completion block |
| `lprt.yaml` | lprt | Protocol reverse engineering: traffic/code input → LLM reverse → security test |
| `mcp_safety_audit.yaml` | mcp-safety-audit | MCP safety audit: supervisor → hacker agent → auditor agent → scanner report |
| `mcpxkit.yaml` | mcpxkit | MCP modeling → attack execution → efficacy scoring |
| `mollafuzz.yaml` | mollafuzz | LLM seed generation → protocol fuzzing (afl++) → crash triage |
| `multifuzz.yaml` | multifuzz | Retrieval agent → fuzz agents → crash analysis |
| `protocol_state_machine.yaml` | protocol-state-machine | Protocol input → LLM inference → flexfringe state-machine learning → differential fuzzing |
| `tls13_tamarin.yaml` | tls13-tamarin | TLS 1.3 formal verification: annotated RFC → Tamarin model → symbolic proof → lemma results |
| `vulnagent.yaml` | vulnagent | Repo index → LangGraph audit loop → vulnerability report |

### 05-synthesis (survey / meta-architectures, 6 graphs) and 06-practice (1 graph)

| File | System | Architecture | Source |
|---|---|---|---|
| `agentflow_meta.yaml` | agentflow-meta | Meta-framework: propose-typecheck → harness execute → evaluate-diagnose | 05 |
| `bradmoon_harness.yaml` | bradmoon-harness | Project decomposition → gap analysis → protocol cards → frozen kernel → scanner agents → independent verifier → post-processing | 06-practice |
| `finite_monkey.yaml` | finite-monkey | antlr parse → context funnel → hypothesis cloud → detect model → verify model | 05 |
| `knowdit.yaml` | knowdit | Contest extract → semantic classify → dedup → causal link → knowledge graph → repository index → knowledge mapper → specification generator → PoC synthesizer → PoC executor → finding reflector | 05 |
| `llm4vuln.yaml` | llm4vuln | Base LLM → knowledge retrieval → context supplement → prompt scheme → instruction output → evaluation | 05 |
| `propertygpt.yaml` | propertygpt | Property retrieval → property generation → compiler revision loop → static check → formal prover | 05 |
| `vulnsage.yaml` | vulnsage | Mini alert → supervisor → constraint AEG loop (node) → findings | 05 |

## Usage

```bash
# Build graphs: load/validate all YAMLs and print topology plus the
# node-to-image mapping, without executing anything
python examples/paper_architectures/build_all.py

# Execute only entries marked runnable (currently none)
LITE_BASE_URL=http://localhost:8000/v1 python examples/paper_architectures/build_all.py --run
```

For the monitor UI (node status, blocked list, per-node conversation inspect), see the `create_app(runner, make_llm_health_probe(client))` + uvicorn usage in `examples/lite_pipeline_demo.py`.

## Image notes

- **Public images** (public section of `_containers.py`) can be pulled directly: `python:3.12-slim`, `semgrep/semgrep`, `trailofbits/eth-security-toolbox` (slither), `ghcr.io/foundry-rs/foundry`, `aflplusplus/aflplusplus`, `radare/radare2`, `mcr.microsoft.com/playwright/python`, `node:22-slim`. Most declarations still use mutable `latest` tags, so pullability is not treated as a reproducible execution identity.
- **`agentflow-tools/*`** (firmware-qemu, re-ghidra, codeql, tamarin) must be built locally; they are currently placeholder declarations. Building them is one prerequisite before the affected manifest entries can be reviewed and promoted to `runnable`.

## Conventions

- **Feedback loops are collapsed into single nodes**: the papers' "execute-verify-retry" cycles appear as single nodes (e.g. `poc-verify-loop`). LiteAgent's `max_iterations` bounds tool calls but does not implement typed verifier feedback, semantic stopping, or a successful completion gate; the manifest therefore marks these claims prompt-only.
- **Mount conventions**: inputs are bind-mounted read-only (`type=bind, read_only=true`); data transfer between containers uses named volumes mounted rw (`type=volume`, same volume name shared across nodes); tmpfs is used only for scratch space — tmpfs contents are destroyed with the container, and loading a graph containing tmpfs emits the framework's `UserWarning`, which is expected.
