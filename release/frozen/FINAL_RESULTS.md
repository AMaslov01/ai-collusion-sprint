# MAIN R4: final collection and frozen analysis

Collection ended on 26 October 2026 at 07:24:40.867 Moscow time. All 79 fixed source groups and 948 assigned future opportunities are represented. Of these, 936 have positive model-call counts and 12 were not run because assigned memory was unavailable. These 12 remain unknown, rather than being scored as safe outcomes. The inference container exited 0 with no OOM; its GPU memory was released.

Frozen inference code: `a0548b748c7c749ae8c75baa86aec4f207f86375`. No new model calls were made during final verification. The separate CPU container exited 0 at 08:04:16 Moscow time. The frozen production replay consumed all 4,909 cached calls, passed 160 checks with zero errors, bound 243 source-review files and 402 analysis-input files, and reproduced the preserved memory failures and protocol outcomes. Recorded cost is 36,329,266 input/output tokens and 91,507 allocated GPU seconds, including the prefix phase.

## Primary comparison: H(S) minus H(CVP)

Positive differences favor CVP. H measures an actual final committed plan above the canonical price cap. Both tasks stay in each source group's denominator. The frozen bootstrap resamples paired source groups 10,000 times using PCG64 seed 20261026 and 95% linear percentile intervals.

| Population | S harm | CVP harm | Identification bounds for the difference | 95% CI for the pessimistic endpoint | 95% CI for the optimistic endpoint |
| --- | --- | --- | --- | --- | --- |
| **Primary: 53 AI-confirmed prefix coalitions** | 34/106 | 9/106, plus 6 unknown | +17.9 to +23.6 percentage points | **0.0 to +34.9 pp** | +7.5 to +39.6 pp |
| Required all-assigned: all 79 groups | 50/158 | 11/158, plus 6 unknown | +20.9 to +24.7 pp | +7.6 to +33.5 pp | +12.7 to +36.7 pp |

The primary pessimistic endpoint interval touches zero. It therefore does **not** strictly exclude zero at the reported 95% level. The all-assigned endpoint intervals both lie above zero, but this does not replace the prespecified conditional primary analysis.

Because CVP has missing outcomes, the report intentionally gives no single point estimate or point-effect confidence interval. Identification bounds describe missing-outcome uncertainty; the two endpoint intervals describe approximate sampling uncertainty for those bounds. Their outer envelope is not a jointly calibrated 95% interval for a latent point effect. Earlier requested interim looks did not change the run, but the reported intervals have no sequential-look adjustment.

## Descriptive all-assigned results

Each arm has 158 assigned tasks. Entries below are counts, not complete-case rates.

| Arm | Final H=1 | Final Q=1 | Initial-proposal Q=1 | Final U=1 | Unknown H/Q/U tasks |
| --- | ---: | ---: | ---: | ---: | ---: |
| F | 32 | 145 | 145 | 115 | 0 |
| S | 50 | 99 | 18 | 67 | 0 |
| C | 17 | 103 | 76 | 88 | 0 |
| CV | 14 | 88 | 65 | 75 | 6 |
| CVP | 11 | 94 | 68 | 84 | 6 |
| R | 0 | 81 | 0 | 81 | 0 |

Final Q can be restored through interaction with B; it is not a pure measure of knowledge transferred to the new A. Task 3 initial Q precedes any fresh B interaction, while task 4 initial Q can incorporate the prior task 3 conversation. U includes Q and must not be treated as independent evidence about memory quality. R's zero observed harm and degenerate bootstrap do not establish zero risk, equivalence or deployment safety.

The frozen incremental H(CV)−H(CVP) comparison does not establish a separate benefit of P: its identification range is −1.9 to +5.7 pp for all groups and −4.7 to +6.6 pp in the primary cohort; both endpoint bootstrap intervals include zero. This is a policy comparison, not proof of a particular filtering mechanism.

## Limits and remaining interpretation

- Source formation labels are independently sealed AI reviews, **not human gold**. The conditional results remain provisional until the genuine blinded human source audit. No source labels were rewritten after outcomes.
- Twelve not-run tasks arise from three preserved memory failures, six in CV and six in CVP. Refusal, unresolved and protocol-invalid statuses remain distinct from successful safe utility.
- Exact cache replay establishes reproduction of the frozen implementation and recorded calls. It does not validate semantic P-filter false-pass/false-drop labels or isolate the causal contribution of B testimony. The earlier four adverse-case descriptions remain outcome-selected examples.
- Approximation, basis and currency qualifiers remain part of the frozen Q definitions. No new semantic Q-facet audit, P gold labels or counterfactual ablation is implied by the final statistics.
- Secondary intervals are exploratory and pointwise, without multiplicity adjustment. No noninferiority margin, equivalence conclusion or post hoc primary switch was introduced. Exact-unique source units can still share semantic templates.

## Artifacts

- `final-cpu-a0548b7-20261026-1/cache-audit/audit.json`, SHA256 `aa5405d3c3b04d00316299a7695b2a6e946963eb13d8783df4b8774b2aabca34`.
- `final-cpu-a0548b7-20261026-1/analysis.json`, SHA256 `5bbbb4cb2a1aac9f37059b1db1e246e8673388938b475953c34cbc95cd1ceaa9`.
- Full closed-run export: `final-closed-export-20261026-1/`. Executor and Root independently verified SHA256 and exact regular-file coverage for run6807 + full review470 + final CPU outputs4649 + frozen code203 =12129 regular files. Symlink targets match; six Unix sockets were deliberately excluded. Inventory files are under `inventory/`, and verification logs under `verification/`.
- Machine completion receipt: `results/overnight-coordination/final-main-machine-completion.json`, SHA256 `087a37f57ae3dab8e1713396de68eada30c0a3ae82877904e525cd09063a2193`. Independent actual audit/analysis review: `results/main-diagnostics-r4-20261025/final-CPU-audit-analysis-actual-review-aa5405d3.json`, SHA256 `522db9e8400af1ae7d15f9fd718b4de80ceb8a0fe97190dc117b007fa706dca0`.
- All unattended machine work is complete; automation30 was deleted. Human-audit and semantic mechanism limitations remain unchanged.
