#!/usr/bin/env bash
# New bounded block; old deadlines/runs are neither resumed nor modified.
set -euo pipefail
CAN_CODE_SHA=${1:?Full committed code SHA}
CAN_PHASE=${2:?regression-v1, regression-v2 or heldout}
CAN_ACTION=${3:?checks, run or audit}
[[ "$CAN_CODE_SHA" =~ ^[0-9a-f]{40}$ ]]
case "$CAN_PHASE" in regression-v1|regression-v2|heldout) ;; *) exit 2 ;; esac
case "$CAN_ACTION" in checks|run|audit) ;; *) exit 2 ;; esac
test "$(hostname)" = caniculus.appliedai.local
test "$(id -un)" = a.maslov
test "$(id -u)" -ne 0
umask 077
CAN_PROJECT=/mnt/ssd/a.maslov/ai-collusion-sprint
CAN_CODE="$CAN_PROJECT/code/$CAN_CODE_SHA"
CAN_BASE="$CAN_PROJECT/code/99c5118bdfcfd7c8e986ce4d5fc2f3ac4491faa7"
CAN_ROOT="$CAN_PROJECT/runs/repair-cycle-v3-20261024"
CAN_CONFIG="$CAN_CODE/configs/repair-cycle-v3.json"
CAN_REVIEW="$CAN_PROJECT/inputs/repair-v3-review-8e8b760"
CAN_MODEL="$CAN_PROJECT/inputs/Qwen3.8-27B-FP8/017b9c7af6b5689d5dd426a76e0bc077eb5ca20a"
CAN_IDENTITIES="$CAN_PROJECT/bootstrap/preparation-31f9c028bd8615c4b7648144c278ed0d6ffbe56c"
CAN_TASKS="$CAN_PROJECT/bootstrap/preparation-33d29710c408fd9851e9de0db5e436cc623b595c/task-data"
CAN_NATIVE_OLD="$CAN_PROJECT/runs/native-dev-921d20611324ab0429f119380e12389f44223e61"
CAN_REG_SOURCE="$CAN_PROJECT/runs/memory-group1-cp2-v1"
CAN_LOCK_SHA=$(sha256sum "$CAN_BASE/configs/travel-tools.lock" | cut -d ' ' -f 1)
CAN_VENDOR="$CAN_PROJECT/dependencies/travel-tools-$CAN_LOCK_SHA"
CAN_UPSTREAM=/opt/ai-collusion/sources/MemoryArena
CAN_UUID=GPU-441e6008-cd27-34b5-c81a-17dad9d4a894
for CAN_PATH in "$CAN_PROJECT" "$CAN_CODE" "$CAN_BASE" "$CAN_REVIEW" "$CAN_MODEL" "$CAN_IDENTITIES" "$CAN_TASKS" "$CAN_NATIVE_OLD" "$CAN_REG_SOURCE" "$CAN_VENDOR" "$CAN_PROJECT/cache" "$CAN_PROJECT/inputs/MemoryArena" "$CAN_PROJECT/inputs/travel-database"; do
 test "$(realpath "$CAN_PATH")" = "$CAN_PATH"
 test "$(stat -c %u "$CAN_PATH")" = "$(id -u)"
done
mkdir -p "$CAN_ROOT"
test "$(realpath "$CAN_ROOT")" = "$CAN_ROOT"
test "$(stat -c %u "$CAN_ROOT")" = "$(id -u)"
exec 9>"$CAN_ROOT/launch.lock"
flock -n 9
test ! -e "$CAN_PROJECT/bootstrap/control/STOP"
test "$(jq -r '.status' "$CAN_CONFIG")" = reviewed
CAN_CUTOFF=$(date -u -d "$(jq -r '.limits.absolute_cutoff_utc' "$CAN_CONFIG")" +%s)
CAN_END=$CAN_CUTOFF
CAN_CHECKS="$CAN_ROOT/checks-$CAN_PHASE-$CAN_CODE_SHA"
CAN_COMPLETED="$CAN_ROOT/$CAN_PHASE"
CAN_WORK="$CAN_CHECKS"
CAN_NAME="a-maslov-ai-collusion-v3-$CAN_ACTION-$CAN_PHASE-${CAN_CODE_SHA:0:7}"
CAN_DEVICE=(--env NVIDIA_VISIBLE_DEVICES=void --env CUDA_VISIBLE_DEVICES=)
CAN_TYPE=cpu
CAN_CYCLE_MODE=rw
CAN_EXTRA=()
if [ "$CAN_ACTION" = run ]; then
 test "$(jq -r '.status' "$CAN_CHECKS/preflight.json")" = passed_no_model_calls
 test "$(jq -r '.code_commit' "$CAN_CHECKS/phase-manifest.json")" = "$CAN_CODE_SHA"
 test ! -e "$CAN_COMPLETED.started"
 test "$(docker ps --filter label=owner=a.maslov --filter label=scope=repair-cycle-v3 --filter label=computation=gpu --format '{{.Names}}' | wc -l)" -eq 0
 test "$(nvidia-smi --id="$CAN_UUID" --query-compute-apps=pid --format=csv,noheader | wc -l)" -eq 0
 test -z "$(ss -H -lnt 'sport = :18083')"
 test "$(jq -r '.operations.gpu_reservation.channel_notified' "$CAN_BASE/configs/dev-package.draft.json")" = true
 if [ ! -e "$CAN_ROOT/gpu-first-start.unix" ]; then (set -o noclobber; date +%s > "$CAN_ROOT/gpu-first-start.unix"); fi
 CAN_SERVICE_END=$(($(cat "$CAN_ROOT/gpu-first-start.unix")+7200))
 if [ "$CAN_SERVICE_END" -lt "$CAN_END" ]; then CAN_END=$CAN_SERVICE_END; fi
 CAN_WORK="$CAN_COMPLETED"
 CAN_DEVICE=(--gpus "device=$CAN_UUID")
 CAN_TYPE=gpu
elif [ "$CAN_ACTION" = audit ]; then
 CAN_WORK="$CAN_ROOT/audit-$CAN_PHASE-$CAN_CODE_SHA"
 CAN_CYCLE_MODE=ro
 test -d "$CAN_COMPLETED"
 CAN_EXTRA=(--mount "type=bind,src=$CAN_COMPLETED,dst=/completed,readonly")
fi
CAN_REMAINING=$((CAN_END-$(date +%s)))
test "$CAN_REMAINING" -gt 0
test ! -e "$CAN_WORK"
mkdir -p "$CAN_WORK/home" "$CAN_WORK/tmp"
if [ "$CAN_ACTION" = run ]; then
 (set -o noclobber; printf '%s\n' "$CAN_CODE_SHA" > "$CAN_COMPLETED.started")
 cp "$CAN_CHECKS/phase-manifest.json" "$CAN_CHECKS/assets.json" "$CAN_CHECKS/preflight.json" "$CAN_WORK/"
 nvidia-smi --id="$CAN_UUID" --query-gpu=index,uuid,name,memory.used,memory.free,utilization.gpu --format=csv,noheader > "$CAN_WORK/gpu-before.csv"
fi
CAN_CYCLE_MOUNT="type=bind,src=$CAN_ROOT,dst=/cycle"
if [ "$CAN_CYCLE_MODE" = ro ]; then CAN_CYCLE_MOUNT="$CAN_CYCLE_MOUNT,readonly"; fi
docker run -d --name "$CAN_NAME" --label owner=a.maslov --label scope=repair-cycle-v3 --label "computation=$CAN_TYPE" --label "adapter_commit=$CAN_CODE_SHA" \
 --user "$(id -u):$(id -g)" --restart=no --network=host --cpus 8 --memory 64g --pids-limit 1024 --shm-size 8g \
 --cap-drop=ALL --security-opt=no-new-privileges "${CAN_DEVICE[@]}" \
 --env "ADAPTER_COMMIT=$CAN_CODE_SHA" --env "CYCLE_PHASE=$CAN_PHASE" --env "PACKAGE_DEADLINE_UNIX=$CAN_END" \
 --env HOME=/work/home --env TMPDIR=/work/tmp --env PYTHONDONTWRITEBYTECODE=1 \
 --env HF_HOME=/cache/hf --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1 \
 --env XDG_CACHE_HOME=/cache/xdg --env TORCH_HOME=/cache/torch --env TORCHINDUCTOR_CACHE_DIR=/cache/torchinductor \
 --env TRITON_CACHE_DIR=/cache/triton --env TORCH_EXTENSIONS_DIR=/cache/torch-extensions --env VLLM_CACHE_ROOT=/cache/vllm \
 --env CUDA_CACHE_PATH=/cache/cuda --env NUMBA_CACHE_DIR=/cache/numba \
 --env MEMORYARENA_ROOT="$CAN_UPSTREAM" --env "PYTHONPATH=/vendor:/code/src:$CAN_UPSTREAM" --workdir "$CAN_UPSTREAM" \
 --mount "type=bind,src=$CAN_CODE,dst=/code,readonly" --mount "type=bind,src=$CAN_BASE,dst=/basecode,readonly" \
 --mount "$CAN_CYCLE_MOUNT" --mount "type=bind,src=$CAN_WORK,dst=/work" \
 --mount "type=bind,src=$CAN_REVIEW,dst=/review,readonly" --mount "type=bind,src=$CAN_REG_SOURCE,dst=/regression-source,readonly" \
 --mount "type=bind,src=$CAN_NATIVE_OLD,dst=/native-old,readonly" \
 --mount "type=bind,src=$CAN_PROJECT/inputs/MemoryArena,dst=$CAN_UPSTREAM,readonly" \
 --mount "type=bind,src=$CAN_MODEL,dst=/model,readonly" --mount "type=bind,src=$CAN_IDENTITIES,dst=/identities,readonly" \
 --mount "type=bind,src=$CAN_PROJECT/inputs/travel-database,dst=/database,readonly" \
 --mount "type=bind,src=$CAN_TASKS,dst=/tasks,readonly" --mount "type=bind,src=$CAN_VENDOR,dst=/vendor,readonly" \
 --mount "type=bind,src=$CAN_PROJECT/cache,dst=/cache" --mount "type=bind,src=$CAN_PROJECT/bootstrap/control,dst=/control,readonly" "${CAN_EXTRA[@]}" \
 --entrypoint /usr/bin/timeout "$(cat "$CAN_PROJECT/bootstrap/environment/image-id.txt")" \
 --signal=TERM --kill-after=30s "${CAN_REMAINING}s" /bin/bash -c \
 'set -e; umask 077; test ! -e /control/STOP; if [ "$1" = checks ]; then python3 -m unittest discover -s /code/tests -v > /work/unittest.log 2>&1; exec python3 -m collusion_handoff.repair_cycle --checks > /work/preflight.log 2>&1; fi; if [ "$1" = audit ]; then exec python3 -m collusion_handoff.repair_audit > /work/audit.log 2>&1; fi; exec python3 -m collusion_handoff.repair_cycle > /work/supervisor.log 2>&1' -- "$CAN_ACTION"
docker inspect "$CAN_NAME" > "$CAN_WORK/docker-inspect.json"
