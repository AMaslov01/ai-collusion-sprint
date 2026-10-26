# Reproduction guide

There are three distinct operations. Only the first two are available from a
public clone. All project execution by the authors takes place in nonroot
Docker on caniculus. Neither public command below starts a model server,
performs an API call, or acquires third-party data.

## 1. Public tests and artifact integrity

On caniculus, with the recorded OCI image already present:

```bash
bash scripts/cpu-public.sh test /mnt/ssd/a.maslov/ai-collusion-sprint/public-test-new
```

`public-test-new` must not exist; its parent must exist. This runs the data-free
suite in a nonroot, network-isolated CPU container. The native OpenAI-client
integration file is excluded because it requires the separately acquired
MemoryArena checkout. Three pinned-data integration tests explicitly skip.
Mock model transports are fixtures, not real inference. See the saved
[validation receipt](../../release/validation/README.md).

Byte checks need only the Python standard library and do not run analysis:

```bash
python3 scripts/verify-release.py
```

The original scientific source, prompts, schemas and configurations are bound
to `a0548b748c7c749ae8c75baa86aec4f207f86375` in
`release/frozen-source-sha256.json`. Packaging helpers are later additions.
The original Git revision remains reachable in the published history.

## 2. Public numerical reproduction

```bash
bash scripts/cpu-public.sh analysis /mnt/ssd/a.maslov/ai-collusion-sprint/public-analysis-new
```

This consumes the public numeric projection and executes the unchanged
`main_analysis.analyze` / `paired_analysis.summarize` implementation. All
analysis-generated fields must equal the corresponding frozen-report fields.
It writes `recomputed-analysis.json` and `run.log` in the new directory. The
recomputed JSON deliberately omits full-run provenance fields appended by the
production driver; its whole-file hash is therefore not the frozen report's
hash. NumPy 2.2.6, PCG64 seed 20261026, 10000 paired group draws and linear
quantiles are fixed. Unknown tasks remain unknown and keep their assigned
denominators.

For readers with another Docker host, the equivalent public arithmetic command
is below. The tested platform is Linux amd64; the full inference image is large.
It is pinned for environment fidelity, not advertised as a small CPU image.
Acquire it separately under the applicable network/resource rules.

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

Replace the last line with `-B /code/scripts/test-public.py` for the public test
suite. The host UID must be nonzero. `pyproject.toml` and
`requirements-analysis.txt` describe minimal arithmetic dependencies; only the
recorded OCI environment is claimed as validated. `PYTHONPATH=src` loads the
source tree without installation; data/prompt/schema assets remain in the clone.

This operation establishes arithmetic reproducibility conditional on the
published numeric measurements and provisional labels. It cannot establish
that a source agreement was real, that P preserved the right claims, or that
the canonical evaluator was semantically correct.

## 3. Full production cache-only replay

This requires the private verified run/review export and external model,
tokenizer, dataset, database, dependency vendor and identity manifests listed
in [DATA.md](../../DATA.md). They are not all available from a public clone.
The following command is for the existing author-side caniculus layout:

```bash
bash scripts/replay-recorded-main.sh \
  /mnt/ssd/a.maslov/ai-collusion-sprint \
  /mnt/ssd/a.maslov/ai-collusion-sprint/final/replay-new
```

The script requires a new output outside the recorded run, frozen code at
`code/a0548b748c7c749ae8c75baa86aec4f207f86375`, the original external review
packet, and all identity-bound inputs. It mounts scientific inputs read-only
and runs the original commands inside nonroot CPU Docker, with no GPU devices
and `--network=none`:

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

Expected: `passed_cache_only`, 4909 calls consumed exactly once, 160 checks,
zero errors, zero new model calls; the original three memory failures and 12
not-run tasks are preserved. Expected audit SHA256:
`aa5405d3c3b04d00316299a7695b2a6e946963eb13d8783df4b8774b2aabca34`.
Expected full analysis SHA256:
`5bbbb4cb2a1aac9f37059b1db1e246e8673388938b475953c34cbc95cd1ceaa9`.
The launcher saves logs and fails closed; it never fabricates missing responses.

Recorded-call replay reconstructs requests and reuses saved responses. A fresh
stochastic model run would produce new responses, costs and uncertainty. Seed
42 and a pinned runtime do not promise identical generations across hardware
or kernels. No new collection was run for this release. Existing historical
`caniculus-main-*.sh` collection scripts retain expired deadlines and approval
requirements; they are not a fresh-run quickstart. A new experiment needs a new
prospective manifest and resource authorization, preserving this frozen run.
