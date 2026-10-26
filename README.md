# MemContrast: Selective Memory Handoffs After Induced Collusion

Artemiy Maslov and Artemiy Mitselovskiy — ITMO University.

MemContrast studies whether a replacement planner can retain useful knowledge
while reducing harmful continuation with a reviewer that keeps its history.
Both agents' conflicting private roles are removed at handoff. Six memory
policies share each source state and the same two future tasks: full archive
(F), summary (S), extracted claims (C), verified claims (CV), verified and
policy-filtered claims (CVP), and reset (R).

This repository contains the executable research implementation, regression
tests, exact prompts/schemas/configurations, frozen aggregate evidence, and a
public numeric dataset that reproduces the statistical analysis. The full raw
wire cache is private because third-party redistribution permissions remain
unresolved. See [data availability](DATA.md) and the
[reproduction guide](docs/reproducibility/README.md).

## Run the public checks

The authors execute scientific code only in nonroot Docker on caniculus.
After cloning on that host, with the pinned OCI image already available:

```bash
git clone https://github.com/AMaslov01/ai-collusion-sprint.git
cd ai-collusion-sprint
python3 scripts/verify-release.py
bash scripts/cpu-public.sh test /mnt/ssd/a.maslov/ai-collusion-sprint/public-test-new
bash scripts/cpu-public.sh analysis /mnt/ssd/a.maslov/ai-collusion-sprint/public-analysis-new
```

Output directories must be new and their parents must exist. The public
analysis uses numeric outcomes and the unchanged frozen analysis code; it
checks every computed field against the original report. Both containers are
CPU-only, nonroot and network-isolated. No model calls or third-party task
downloads occur. The [guide](docs/reproducibility/README.md) includes the
portable Docker command for other readers and the author-side full-cache replay.
The tested environment is Linux amd64, Python 3.12.3, NumPy 2.2.6 and the exact
[OCI digest and selected package versions](release/environment.json).

## Frozen results and limits

MAIN fixed 79 source groups, six arms and two future tasks: **948 assigned
opportunities**. Of these, 936 had model calls; 12 not-run tasks remain unknown
because three memory-stage failures were retained. The primary population has
53 source-only AI-confirmed active agreements with actual prefix harm. AI
labels remain provisional pending genuine blinded human validation.

- Primary H(S) − H(CVP) identification bounds: **17.9–23.6 percentage points**.
  The pessimistic endpoint's 95% sampling interval begins at exactly zero.
- Required all-assigned secondary bounds: **20.9–24.7 points**. Positive
  endpoint intervals do not replace the primary analysis.
- Extraction already produces a substantial observed change. Separate gains
  from verification/filtering and final utility preservation are not established.

[analysis.json](release/frozen/analysis.json) is an unchanged copy of the frozen
report, SHA256
`5bbbb4cb2a1aac9f37059b1db1e246e8673388938b475953c34cbc95cd1ceaa9`.
The [cache-audit receipt](release/frozen/cache-audit.json) records 4909 replayed
calls, 160 checks, zero errors and zero new model calls. Scientific code is
frozen at `a0548b748c7c749ae8c75baa86aec4f207f86375`; later packaging helpers do
not alter it. [Validation evidence](release/validation/README.md) separates
public arithmetic reproduction from full recorded-call replay.

The numeric projection permits independent statistical recomputation. It does
not validate hidden raw traces or semantic labels. Cache replay reconstructs
recorded requests and consumes saved responses; it is distinct from a new
stochastic model run. This is a lodging projection with declared predicates,
not a full MemoryArena travel benchmark score. These are induced-role
experiments, not evidence of spontaneous real-world collusion.

## Project map

| Path | Purpose |
| --- | --- |
| `src/collusion_handoff/abe_*` | Planner/reviewer protocol, tools, evaluation and branch runner |
| `src/collusion_handoff/memory*`, `source_segments.py` | Extraction, provenance, verification, filtering and archive packing |
| `src/collusion_handoff/main_*`, `paired_analysis.py` | Frozen manifest, collection, population selection and paired analysis |
| `scripts/audit-main-cache.py`, `analyze-main.py` | Frozen production cache replay and analysis drivers |
| `scripts/reproduce-public-analysis.py`, `test-public.py` | Public numerical reproduction and data-free checks |
| `prompts/`, `schemas/`, `configs/`, `fixtures/`, `tests/` | Exact research assets and regression fixtures |
| `release/` | Public numeric inputs, unchanged frozen outputs, hashes and validation |
| `docs/reproducibility/` | Commands, environment and reproduction boundaries |
| `docs/handoff/`, `notes/`, root design documents | Historical scientific and operational decisions; not a fresh-run quickstart |

## Prior work, citation and licensing

Preexisting work includes [MemoryArena](https://github.com/ZexueHe/MemoryArena),
its Group Travel requests/base plans/catalogs,
[Emergent Collusion in Long-Horizon LLM Agent Interaction](https://arxiv.org/abs/2609.24967),
and source-grounded checking such as [RefChecker](https://arxiv.org/abs/2405.14486).
The supplied FactContrast manuscript was conceptual background. MemContrast
contributes the separate C/V/P implementation, typed two-agent lodging
projection, six-policy handoff comparison and frozen paired collection/replay.

Use [CITATION.cff](CITATION.cff) to cite the software. The scoped
[LICENSE](LICENSE) covers original contributions;
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) identifies upstream exceptions.
Data, weights, credentials and raw caches are not bundled. The report is being
prepared separately; this code release does not claim a paper submission or
raw-data publication.
