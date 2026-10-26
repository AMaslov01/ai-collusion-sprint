# Data availability

## Included

`release/frozen/analysis-inputs.json` contains a projection of the recorded MAIN
run: all 948 assigned opportunity identities, categorical terminal statuses,
binary/null H/U/Q measurements, initial-proposal Q and measurement flags,
pre-treatment AI agreement/checkpoint categories, prefix H, and the frozen
analysis/sample specification. It contains no traveler queries, names, gold
answers, catalog rows, model messages, or free-text source annotations.
The exporter verifies that these fields reproduce every field produced by
`main_analysis.analyze` in the frozen report.

The original analysis, cache-audit receipt and main manifest are included
unchanged, with SHA256 in `release/SHA256SUMS`. The audit contains hashes and
check outcomes, not cached model responses. Numeric outputs and synthetic
fixtures are author-produced release material under the scoped [LICENSE](LICENSE).

## External inputs for full production replay

| Input | Source / version | SHA256 or identity |
| --- | --- | --- |
| Group Travel task JSONL | [ZexueHe/memoryarena](https://huggingface.co/datasets/ZexueHe/memoryarena), revision `da1a37c8b19280e18627ca01cf368195a5e1d92e`, `group_travel_planner/data.jsonl` | `2f955d444f6f3ad3c5da2064359ab19f8fc1f90621ff9d00723a450a009c3732` |
| Accommodation CSV | Pinned MemoryArena travel database | `7e70cc8cb573e824267be47153a3cf7934c839f01c955fb523d18be405921d86` |
| City/state mapping | `background/citySet_with_states.txt` | `a3d18b5c692857cd561cbb4ad8d221ac3eb6c47af7c787a3489e3ffa184ca4d6` |
| Upstream code | [MemoryArena](https://github.com/ZexueHe/MemoryArena/tree/6cd9de14b71915e39ac742a20dc33785e14b6aab) | Git `6cd9de14b71915e39ac742a20dc33785e14b6aab` |
| Model and tokenizer | [Qwen/Qwen3.8-27B-FP8](https://huggingface.co/Qwen/Qwen3.8-27B-FP8) | HF `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a` |
| Raw requests/responses, snapshots, reviews and CPU preflights | Private verified MAIN export | Per-file bindings in `release/frozen/cache-audit.json`; **not publicly downloadable** |

The original [travel setup](https://github.com/ZexueHe/MemoryArena/blob/6cd9de14b71915e39ac742a20dc33785e14b6aab/setup_travel.md)
explains acquisition and database layout. It does not settle redistribution
rights. Check the sources' current terms before acquiring or sharing them.
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Raw traces embed third-party task/catalog material. Until those permissions
are resolved, the full wire cache and source-review packet stay private.
There is no public raw-data release or guaranteed access-on-request service.
The public numeric projection supports independent arithmetic reproduction;
it cannot independently validate semantic labels, canonical-E construction,
or full request reconstruction. Genuine blinded human source validation
remains pending. Twelve not-run tasks retain unknown outcomes.
