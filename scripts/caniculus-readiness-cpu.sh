#!/usr/bin/env bash
# Read-only, CPU-only tests/probes. This command cannot start a model service.
set -euo pipefail
CAN_SHA=${1:?Full committed code SHA}
CAN_PHASE=${2:-development-memory-v1}
case "$CAN_PHASE" in development-memory-v1|development-prefix-v1|development-checkpoint-memory-v1|validation-prefix-v1|validation-continuations-v1) ;; *) exit 2;; esac
[[ "$CAN_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$(hostname)" = caniculus.appliedai.local
test "$(id -un)" = a.maslov
test "$(id -u)" -ne 0
umask 077
CAN_PROJECT=/mnt/ssd/a.maslov/ai-collusion-sprint
CAN_CODE="$CAN_PROJECT/code/$CAN_SHA"
CAN_BASE="$CAN_PROJECT/code/99c5118bdfcfd7c8e986ce4d5fc2f3ac4491faa7"
CAN_UPSTREAM=/opt/ai-collusion/sources/MemoryArena
CAN_MODEL="$CAN_PROJECT/inputs/Qwen3.8-27B-FP8/017b9c7af6b5689d5dd426a76e0bc077eb5ca20a"
CAN_VENDOR="$CAN_PROJECT/dependencies/travel-tools-d0bc2174eea3b3737f6922b89897db4f21ea6f835f7354ee702edbbc9880b3bc"
CAN_TASKS="$CAN_PROJECT/bootstrap/preparation-33d29710c408fd9851e9de0db5e436cc623b595c/task-data"
CAN_ROOT="$CAN_PROJECT/runs/readiness-cycle-v4-20261024"
CAN_FIXTURES="$CAN_PROJECT/inputs/readiness-v4-independent-a56492c"
CAN_REVIEW="$CAN_FIXTURES"
CAN_FRESH="$CAN_PROJECT/inputs/readiness-v4-fresh-b0e42d7d"
CAN_EXTRA_MOUNTS=()
if [ -d "$CAN_FRESH" ]; then
 test "$(realpath "$CAN_FRESH")" = "$CAN_FRESH"
 test "$(stat -c %u "$CAN_FRESH")" = "$(id -u)"
 CAN_EXTRA_MOUNTS+=(--mount "type=bind,src=$CAN_FRESH,dst=/fresh-fixtures,readonly")
fi
if [[ "$CAN_PHASE" == validation-* ]]; then
 CAN_REVIEW="$CAN_PROJECT/inputs/readiness-v4-review-$CAN_SHA"
 test -f "$CAN_REVIEW/final-DEV-validation-freeze.json"
 test -d "$CAN_FRESH"
fi
test "$(realpath "$CAN_REVIEW")" = "$CAN_REVIEW"
test "$(stat -c %u "$CAN_REVIEW")" = "$(id -u)"
for CAN_PATH in "$CAN_PROJECT" "$CAN_CODE" "$CAN_BASE" "$CAN_MODEL" "$CAN_VENDOR" "$CAN_TASKS" "$CAN_FIXTURES" "$CAN_PROJECT/cache" "$CAN_PROJECT/inputs/MemoryArena" "$CAN_PROJECT/inputs/travel-database"; do
 test "$(realpath "$CAN_PATH")" = "$CAN_PATH"
 test "$(stat -c %u "$CAN_PATH")" = "$(id -u)"
done
mkdir -p "$CAN_ROOT"
test "$(realpath "$CAN_ROOT")" = "$CAN_ROOT"
test "$(stat -c %u "$CAN_ROOT")" = "$(id -u)"
CAN_WORK="$CAN_ROOT/cpu-$CAN_SHA"
if [ "$CAN_PHASE" != development-memory-v1 ]; then CAN_WORK="$CAN_ROOT/cpu-$CAN_PHASE-$CAN_SHA"; fi
test ! -e "$CAN_WORK"
test ! -e "$CAN_PROJECT/bootstrap/control/STOP"
test "$(sha256sum "$CAN_FIXTURES/independent-fixtures.json" | cut -d ' ' -f1)" = a56492c265be060c40e6ddcce6f16add13601c12408746748219d712de384e67
CAN_REMAINING=$(($(date -u -d 2026-10-24T20:00:00Z +%s)-$(date +%s)))
test "$CAN_REMAINING" -gt 0
if [ "$CAN_REMAINING" -gt 900 ]; then CAN_REMAINING=900; fi
mkdir -p "$CAN_WORK/home" "$CAN_WORK/tmp"
jq '.groups[] | select(.upstream_id >= 4 and .upstream_id <= 7)' "$CAN_TASKS/registry.json" > "$CAN_WORK/reserved-registry.jsonl"
CAN_NAME="a-maslov-ai-collusion-v4-cpu-$CAN_PHASE-${CAN_SHA:0:7}"
docker run -d --name "$CAN_NAME" --label owner=a.maslov --label scope=readiness-cycle-v4 --label computation=cpu --label "adapter_commit=$CAN_SHA" \
 --user "$(id -u):$(id -g)" --restart=no --network=none --cpus 8 --memory 64g --pids-limit 1024 --shm-size 8g \
 --cap-drop=ALL --security-opt=no-new-privileges --env NVIDIA_VISIBLE_DEVICES=void --env CUDA_VISIBLE_DEVICES= \
 --env HOME=/work/home --env TMPDIR=/work/tmp --env PYTHONDONTWRITEBYTECODE=1 \
 --env HF_HOME=/cache/hf --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1 --env XDG_CACHE_HOME=/cache/xdg \
 --env "PYTHONPATH=/vendor:/code/src:$CAN_UPSTREAM" --env MEMORYARENA_ROOT="$CAN_UPSTREAM" --env "ADAPTER_COMMIT=$CAN_SHA" \
 --env "CYCLE_PHASE=$CAN_PHASE" \
 --mount "type=bind,src=$CAN_CODE,dst=/code,readonly" --mount "type=bind,src=$CAN_BASE,dst=/basecode,readonly" \
 --mount "type=bind,src=$CAN_PROJECT/runs/repair-cycle-v3-20261024,dst=/previous,readonly" \
 --mount "type=bind,src=$CAN_ROOT,dst=/cycle,readonly" \
 --mount "type=bind,src=$CAN_PROJECT/runs/memory-group1-cp2-format-v2,dst=/old,readonly" \
 --mount "type=bind,src=$CAN_PROJECT/inputs/MemoryArena,dst=$CAN_UPSTREAM,readonly" \
 --mount "type=bind,src=$CAN_PROJECT/inputs/travel-database,dst=/database,readonly" \
 --mount "type=bind,src=$CAN_VENDOR,dst=/vendor,readonly" --mount "type=bind,src=$CAN_TASKS,dst=/tasks,readonly" \
 --mount "type=bind,src=$CAN_MODEL,dst=/model,readonly" --mount "type=bind,src=$CAN_WORK,dst=/work" \
 --mount "type=bind,src=$CAN_FIXTURES,dst=/review-v4,readonly" \
 --mount "type=bind,src=$CAN_REVIEW,dst=/review,readonly" "${CAN_EXTRA_MOUNTS[@]}" \
 --mount "type=bind,src=$CAN_PROJECT/cache,dst=/cache" --mount "type=bind,src=$CAN_PROJECT/bootstrap/control,dst=/control,readonly" \
 --workdir "$CAN_UPSTREAM" --entrypoint /usr/bin/timeout "$(cat "$CAN_PROJECT/bootstrap/environment/image-id.txt")" \
 --signal=TERM --kill-after=30s "${CAN_REMAINING}s" /bin/bash -c \
 'set -e; umask 077; test ! -e /control/STOP; python3 -m unittest discover -s /code/tests -v > /work/unittest.log 2>&1; python3 /code/scripts/probe-structured-output.py > /work/probe.log 2>&1; python3 /code/scripts/measure-memory-codec.py > /work/codec.log 2>&1; python3 /code/scripts/prepare-abe-dev.py > /work/abe-preparation.log 2>&1; python3 -m collusion_handoff.readiness_cycle --preflight > /work/component-preflight.log 2>&1; python3 /code/scripts/run-abe-independent-fixtures.py --fixtures /review-v4/independent-fixtures.json --output /work/independent --adapter-commit "$ADAPTER_COMMIT" --execution-environment "caniculus non-root CPU Docker immutable vLLM0.32.0 image bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d" > /work/independent.log 2>&1; if test -d /fresh-fixtures; then python3 /code/scripts/probe-fresh-memory-wire.py > /work/fresh-wire.log 2>&1; fi'
docker inspect "$CAN_NAME" > "$CAN_WORK/docker-inspect.json"
printf '%s\n' "$CAN_WORK"
