# Reproducibility

## Public numerical reproduction

A public clone includes numeric outcomes for all 948 assigned opportunities,
categorical source labels, prefix harm, the analysis specification, and the
unchanged frozen report. It excludes task text, traveler names, gold answers,
catalog rows, model messages and free-text source annotations.

On caniculus, in the checkout and with the recorded OCI image already present:

```bash
python3 scripts/verify-release.py
bash scripts/cpu-public.sh test /mnt/ssd/a.maslov/ai-collusion-sprint/test-new
bash scripts/cpu-public.sh analysis /mnt/ssd/a.maslov/ai-collusion-sprint/analysis-new
```

Output directories must be new; parents must exist. Tests and analysis run in
nonroot CPU Docker with `--network=none`, no GPU, read-only source, 4 CPU and
16 GiB. The analysis writes `recomputed-analysis.json` and `run.log`. Every field
computed by the unchanged `main_analysis.analyze` must match the frozen report.
The public suite reports 175 tests, OK with 3 pinned-data skips. The upstream
native-client integration file is explicitly excluded. Saved evidence is in
[`release/validation/`](../release/validation/).

For other readers, the equivalent Docker command on a Linux amd64 host is:

```bash
mkdir /absolute/new-output
docker run --rm --user "$(id -u):$(id -g)" --network=none \
  --cpus=4 --memory=16g --pids-limit=512 --read-only \
  --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,nosuid,size=2g \
  -e NVIDIA_VISIBLE_DEVICES=void -e CUDA_VISIBLE_DEVICES= \
  -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/code/src \
  -e OMP_NUM_THREADS=1 -e OPENBLAS_NUM_THREADS=1 \
  --mount "type=bind,src=$PWD,dst=/code,readonly" \
  --mount type=bind,src=/absolute/new-output,dst=/work \
  --workdir /code --entrypoint python3 \
  vllm/vllm-openai@sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9 \
  -B /code/scripts/reproduce-public-analysis.py --output /work/recomputed-analysis.json
```

Replace the last line with `-B /code/scripts/test-public.py` for tests. The host
UID must be nonzero. The full image is large; acquire it separately under the
applicable resource rules. All author-side scientific execution is on caniculus.
Python 3.12.3, NumPy 2.2.6 and the full OCI digest are recorded in
[environment.json](../release/environment.json). `pyproject.toml` and
`requirements-analysis.txt` describe core dependencies; the recorded image is
the validated full environment. The wheel also builds offline with pinned
setuptools 80.10.2; reproduction entrypoints use the source checkout for assets.

The bootstrap uses PCG64, seed 20261026, 10000 paired source-group draws and
linear quantiles. Unknown outcomes retain their denominators. The recomputed
JSON omits full-run provenance fields appended by the production CLI, so its
whole-file SHA is not the original report's SHA. This checks arithmetic from
the released measurements, not the semantic validity of withheld source labels.

## Full recorded-call replay

The full raw run, external review packet, tokenizer/model, upstream checkout,
dataset/database, dependency vendor and identity manifests remain necessary.
They are not all publicly downloadable from this repository. For the existing
author-side caniculus layout:

```bash
bash scripts/replay-recorded-main.sh \
  /mnt/ssd/a.maslov/ai-collusion-sprint \
  /mnt/ssd/a.maslov/ai-collusion-sprint/final/replay-new
```

This mounts frozen code at `code/a0548b748c7c749ae8c75baa86aec4f207f86375`, the
original run and external inputs read-only. A separate nonroot CPU container
has no network or GPU and runs:

```bash
python3 -B /code/scripts/audit-main-cache.py \
  --run /cycle --review /review --inference-codepath /code \
  --prefix-preflight /cycle/cpu-prefixes \
  --continuation-preflight /cycle/cpu-continuations \
  --model /model --output /work/cache-audit
python3 -B /code/scripts/analyze-main.py \
  --run /cycle --review /review \
  --cache-audit /work/cache-audit/audit.json --output /work/analysis.json
```

Expected audit: `passed_cache_only`, 4909 consumed calls, 160 checks, zero errors
and zero new model calls. The three original memory failures and 12 unknown
not-run tasks remain preserved. Expected byte hashes:

| Output | SHA256 |
| --- | --- |
| Full analysis | `5bbbb4cb2a1aac9f37059b1db1e246e8673388938b475953c34cbc95cd1ceaa9` |
| Cache audit | `aa5405d3c3b04d00316299a7695b2a6e946963eb13d8783df4b8774b2aabca34` |

Replay reconstructs requests and reuses recorded responses. A new stochastic
model run produces new responses and costs; seed 42 does not guarantee identical
generations across hardware or kernels. No new collection was run for this
release. A fresh experiment requires a new prospective manifest and resource
authorization. Historical collection scripts remain available at the original
Git revision, with their expired deadlines and original approval gates.

## External inputs and rights

| Input | Pinned identity |
| --- | --- |
| [MemoryArena code](https://github.com/ZexueHe/MemoryArena/tree/6cd9de14b71915e39ac742a20dc33785e14b6aab) | Git `6cd9de14b71915e39ac742a20dc33785e14b6aab` |
| [Group Travel dataset](https://huggingface.co/datasets/ZexueHe/memoryarena/blob/da1a37c8b19280e18627ca01cf368195a5e1d92e/README.md) | HF `da1a37c8b19280e18627ca01cf368195a5e1d92e`; `group_travel_planner/data.jsonl` SHA `2f955d444f6f3ad3c5da2064359ab19f8fc1f90621ff9d00723a450a009c3732` |
| Accommodation CSV | SHA `7e70cc8cb573e824267be47153a3cf7934c839f01c955fb523d18be405921d86` |
| City/state mapping | SHA `a3d18b5c692857cd561cbb4ad8d221ac3eb6c47af7c787a3489e3ffa184ca4d6` |
| [Qwen3.8-27B-FP8](https://huggingface.co/Qwen/Qwen3.8-27B-FP8) | HF `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a` |
| Private wire cache and reviews | Per-file bindings in `release/frozen/cache-audit.json`; no public download |

The [upstream travel setup](https://github.com/ZexueHe/MemoryArena/blob/6cd9de14b71915e39ac742a20dc33785e14b6aab/setup_travel.md)
describes acquisition, not redistribution rights. At the pinned revision we
found no root MemoryArena LICENSE and no license field in the dataset card.
Database redistribution terms remain unresolved. Raw traces embed that
third-party material and remain private; there is no guaranteed access-on-request
service. The model card declares Apache-2.0; no model binaries are vendored.

The scoped [LICENSE](../LICENSE) covers original MemContrast code, prompts,
schemas, synthetic fixtures and author-produced numeric outputs. It does not
relicense external data/models or upstream code. In particular,
`native_data.py` preserves upstream logic in `_extract_name` / `_convert_row`;
these already-public portions are excluded from our MIT grant pending
clarification. The public arithmetic path imports neither function.

The public numeric projection is produced by `export-public-analysis.py`,
which checks all computed fields against the frozen analysis inside isolated
Docker. It supports statistical recomputation, but cannot independently
validate raw request reconstruction, canonical-E construction or semantic
source labels. Genuine blinded human validation remains pending.
