# Third-party provenance and rights

The repository's license covers original MemContrast contributions. It does
not relicense the resources below or imply that a public download grants
redistribution rights. Checked 26 October 2026.

| Resource | Frozen identity | Rights / release treatment |
| --- | --- | --- |
| [MemoryArena](https://github.com/ZexueHe/MemoryArena/tree/6cd9de14b71915e39ac742a20dc33785e14b6aab) | `6cd9de14b71915e39ac742a20dc33785e14b6aab` | No root LICENSE appeared in the pinned repository listing. Acquire separately; permissions remain unresolved. |
| [MemoryArena dataset card](https://huggingface.co/datasets/ZexueHe/memoryarena/blob/da1a37c8b19280e18627ca01cf368195a5e1d92e/README.md) | `da1a37c8b19280e18627ca01cf368195a5e1d92e` | The pinned card has no `license` field. No task text or gold answers are redistributed here. |
| Travel database | Hashes in [DATA.md](DATA.md) | Separate upstream database; redistribution terms have not been established. CSV/catalog rows are excluded. |
| [Qwen3.8-27B-FP8](https://huggingface.co/Qwen/Qwen3.8-27B-FP8) | `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a` | Model card declares Apache-2.0; weights/tokenizer are acquired separately and not included. This is not a review of every underlying training source. |
| vLLM and dependencies | OCI digest in [environment.json](release/environment.json) | External runtime, governed by its own component licenses; no binaries are vendored. |

`src/collusion_handoff/native_data.py` preserves the upstream row-conversion
interface and logic in `_extract_name` / `_convert_row`. These portions were
already in the published development branch and are excluded from our MIT
grant pending upstream clarification. The frozen source is preserved rather
than silently relicensed. The public arithmetic path imports neither function;
no upstream checkout or data is required for it.

MemoryArena provides the preexisting travel environment, requests, base plans
and catalogs. The typed two-agent lodging projection, memory policies, safety
protocol, collection, replay and paired analysis are MemContrast work.
RefChecker and the supplied FactContrast manuscript informed source checking;
this repository does not vendor their code or unpublished manuscript.
