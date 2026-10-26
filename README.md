# MemContrast

**Selective Memory Handoffs After Induced Collusion**

Artemiy Maslov and Artemiy Mitselovskiy · ITMO University

Can a replacement planner retain useful knowledge while reducing harmful
continuation with a reviewer that keeps its history? MemContrast compares six
handoff policies on the same source states and future tasks: full archive (F),
summary (S), extracted claims (C), verified claims (CV), verified and
policy-filtered claims (CVP), and reset (R). Both agents' private roles are
removed at handoff.

## Reproduce

The public numeric dataset reproduces the frozen statistical analysis without
model calls. The authors run scientific code only in nonroot Docker on caniculus:

```bash
python3 scripts/verify-release.py
bash scripts/cpu-public.sh test /mnt/ssd/a.maslov/ai-collusion-sprint/test-new
bash scripts/cpu-public.sh analysis /mnt/ssd/a.maslov/ai-collusion-sprint/analysis-new
```

Use new output directories. The pinned image must already be available.
[Reproduction instructions](docs/reproducibility.md) include a portable Docker
command, dependencies, expected outputs and the author-side full-cache replay.

## Results

The frozen MAIN contains **79 source groups × 6 arms × 2 tasks = 948 assigned
opportunities**. Twelve not-run tasks retain unknown outcomes. In the primary
53-group population, H(S) − H(CVP) identification bounds are **17.9–23.6
percentage points**; the pessimistic endpoint's 95% sampling interval starts at
zero. Source labels are provisional AI annotations, pending blinded human audit.
Separate verification/filtering gains and utility preservation are not established.

- [Frozen analysis](release/frozen/analysis.json) and [numeric inputs](release/frozen/analysis-inputs.json).
- [Recorded-call audit](release/frozen/cache-audit.json): 4909 calls, 160 checks, zero errors, no new model calls.
- [Environment](release/environment.json), [provenance](release/provenance.json), and [validation logs](release/validation/).

Public numerical reproduction verifies arithmetic from released measurements.
Full cache replay also needs withheld raw traces and external inputs; it is
not fully available from a public clone. Reusing recorded responses is distinct
from a fresh stochastic model run. This is a declared lodging projection,
not a full MemoryArena benchmark score or evidence of spontaneous collusion.

## Code

| Directory | Contents |
| --- | --- |
| `src/collusion_handoff/` | Agent protocol, memory policies, evaluator, collection and paired analysis |
| `tests/` | Regression and synthetic transport checks |
| `scripts/` | Reproduction, export and validation entrypoints |
| `configs/`, `prompts/`, `schemas/`, `fixtures/` | Frozen research assets and test inputs |
| `release/` | Public numerical data, unchanged frozen outputs and verification evidence |

Scientific code is frozen at `a0548b748c7c749ae8c75baa86aec4f207f86375`.
The full original snapshot and development history remain reachable in Git;
operational notes and obsolete launch scripts are omitted from the current tree.

## Attribution

The MemContrast implementation was developed during the sprint (first source
commit: 24 October 2026); collection and analysis finished on 26 October.
Release packaging and repository cleanup were added on 26 October. Preexisting
work includes [MemoryArena](https://github.com/ZexueHe/MemoryArena) and its travel
data, [Emergent Collusion](https://arxiv.org/abs/2609.24967), and source-grounded
checking such as [RefChecker](https://arxiv.org/abs/2405.14486). The supplied
FactContrast manuscript informed the approach. These are not new sprint work.
MemContrast contributes its typed two-agent lodging projection, separate C/V/P
implementation, six-policy experiment, recorded-call replay and paired analysis.

[CITATION.cff](CITATION.cff) identifies the authors and software.
[LICENSE](LICENSE) covers original contributions; third-party exceptions and
data availability are documented in the [reproduction guide](docs/reproducibility.md).
Raw task text, catalogs, weights, credentials and conversations are not bundled.
