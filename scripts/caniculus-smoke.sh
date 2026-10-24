#!/usr/bin/env bash
# One bounded technical smoke; this script never starts either DEV block.
set -euo pipefail
CAN_CODE_SHA=${1:?Pass the full committed adapter SHA}
CAN_STAGE=${2:?Choose validate, run or audit}
[[ "$CAN_CODE_SHA" =~ ^[0-9a-f]{40}$ ]]
case "$CAN_STAGE" in validate|run|audit) ;; *) exit 2 ;; esac
test "$(hostname)" = caniculus.appliedai.local
test "$(id -un)" = a.maslov
test "$(id -u)" -ne 0
umask 077
CAN_PROJECT=/mnt/ssd/a.maslov/ai-collusion-sprint
CAN_CODE="$CAN_PROJECT/code/$CAN_CODE_SHA"
CAN_CONFIG="$CAN_CODE/configs/dev-package.draft.json"
CAN_MODEL="$CAN_PROJECT/inputs/Qwen3.8-27B-FP8/017b9c7af6b5689d5dd426a76e0bc077eb5ca20a"
CAN_IDENTITIES="$CAN_PROJECT/bootstrap/preparation-31f9c028bd8615c4b7648144c278ed0d6ffbe56c"
CAN_LOCK_SHA=$(sha256sum "$CAN_CODE/configs/travel-tools.lock" | cut -d ' ' -f 1)
CAN_VENDOR="$CAN_PROJECT/dependencies/travel-tools-$CAN_LOCK_SHA"
CAN_WORK="$CAN_PROJECT/bootstrap/smoke-$CAN_STAGE-$CAN_CODE_SHA"
CAN_UPSTREAM=/opt/ai-collusion/sources/MemoryArena
CAN_UUID=GPU-441e6008-cd27-34b5-c81a-17dad9d4a894
for CAN_PATH in "$CAN_PROJECT" "$CAN_CODE" "$CAN_MODEL" "$CAN_VENDOR" "$CAN_IDENTITIES" "$CAN_PROJECT/inputs/MemoryArena" "$CAN_PROJECT/inputs/travel-database" "$CAN_PROJECT/cache"; do
  test "$(realpath -- "$CAN_PATH")" = "$CAN_PATH"
  test "$(stat -c %u -- "$CAN_PATH")" = "$(id -u)"
done
test ! -e "$CAN_WORK"
mkdir -p "$CAN_WORK/home" "$CAN_WORK/tmp"
test ! -e "$CAN_PROJECT/bootstrap/control/STOP"
test "$(jq -r '.runtime.travel_tool_dependencies.status' "$CAN_CONFIG")" = confirmed
CAN_STARTED=$(cat "$CAN_PROJECT/bootstrap/environment/compute-start.unix")
CAN_LIMIT=$(jq -r '.operations.wall_time_seconds' "$CAN_CONFIG")
CAN_DEADLINE=$((CAN_STARTED+CAN_LIMIT))
CAN_REMAINING=$((CAN_DEADLINE-$(date +%s)))
test "$CAN_REMAINING" -gt 0
CAN_DEVICE=(--env NVIDIA_VISIBLE_DEVICES=void --env CUDA_VISIBLE_DEVICES=)
CAN_ACTION=(--validate-only)
CAN_EXTRA_MOUNTS=()
if [ "$CAN_STAGE" = audit ]; then
  CAN_SMOKE_SHA=${3:?Pass the completed smoke source SHA}
  [[ "$CAN_SMOKE_SHA" =~ ^[0-9a-f]{40}$ ]]
  CAN_COMPLETED="$CAN_PROJECT/bootstrap/smoke-run-$CAN_SMOKE_SHA"
  CAN_DEPENDENCY_CHECK=$(dirname "$(jq -r '.runtime.travel_tool_dependencies.identity.path' "$CAN_CONFIG")")
  for CAN_PATH in "$CAN_COMPLETED" "$CAN_DEPENDENCY_CHECK"; do
    test "$(realpath -- "$CAN_PATH")" = "$CAN_PATH"
    test "$(stat -c %u -- "$CAN_PATH")" = "$(id -u)"
  done
  CAN_EXTRA_MOUNTS=(--mount "type=bind,src=$CAN_COMPLETED,dst=/smoke,readonly" --mount "type=bind,src=$CAN_DEPENDENCY_CHECK,dst=/dependency-check,readonly")
  CAN_ACTION=(--audit)
fi
if [ "$CAN_STAGE" = run ]; then
  test "$(jq -r '.operations.gpu_uuid' "$CAN_CONFIG")" = "$CAN_UUID"
  test "$(jq -r '.operations.gpu_reservation.channel_notified' "$CAN_CONFIG")" = true
  nvidia-smi --id="$CAN_UUID" --query-gpu=index,uuid,name,memory.used,memory.free,utilization.gpu --format=csv,noheader > "$CAN_WORK/gpu-before.csv"
  test "$(nvidia-smi --id="$CAN_UUID" --query-compute-apps=pid --format=csv,noheader | wc -l)" -eq 0
  test -z "$(ss -H -lnt 'sport = :18083')"
  CAN_DEVICE=(--gpus "device=$CAN_UUID")
  CAN_ACTION=()
fi
CAN_NAME="a-maslov-ai-collusion-smoke-$CAN_STAGE-${CAN_CODE_SHA:0:7}"
docker run -d --name "$CAN_NAME" --label owner=a.maslov --label "adapter_commit=$CAN_CODE_SHA" \
  --user "$(id -u):$(id -g)" --restart=no --network=host \
  --cpus 8 --memory 64g --pids-limit 1024 --shm-size 8g \
  --security-opt=no-new-privileges --cap-drop=ALL "${CAN_DEVICE[@]}" \
  --env "ADAPTER_COMMIT=$CAN_CODE_SHA" --env "PACKAGE_DEADLINE_UNIX=$CAN_DEADLINE" \
  --env HOME=/work/home --env TMPDIR=/work/tmp --env PYTHONDONTWRITEBYTECODE=1 \
  --env HF_HOME=/cache/hf --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1 \
  --env XDG_CACHE_HOME=/cache/xdg --env TORCH_HOME=/cache/torch \
  --env TORCHINDUCTOR_CACHE_DIR=/cache/torchinductor --env TRITON_CACHE_DIR=/cache/triton \
  --env TORCH_EXTENSIONS_DIR=/cache/torch-extensions --env VLLM_CACHE_ROOT=/cache/vllm \
  --env CUDA_CACHE_PATH=/cache/cuda --env NUMBA_CACHE_DIR=/cache/numba \
  --env MEMORYARENA_ROOT="$CAN_UPSTREAM" --env "PYTHONPATH=/vendor:/code/src:$CAN_UPSTREAM" \
  --workdir "$CAN_UPSTREAM" \
  --mount "type=bind,src=$CAN_CODE,dst=/code,readonly" \
  --mount "type=bind,src=$CAN_PROJECT/inputs/MemoryArena,dst=$CAN_UPSTREAM,readonly" \
  --mount "type=bind,src=$CAN_MODEL,dst=/model,readonly" \
  --mount "type=bind,src=$CAN_PROJECT/inputs/travel-database,dst=/database,readonly" \
  --mount "type=bind,src=$CAN_IDENTITIES,dst=/identities,readonly" \
  --mount "type=bind,src=$CAN_VENDOR,dst=/vendor,readonly" \
  --mount "type=bind,src=$CAN_PROJECT/cache,dst=/cache" \
  --mount "type=bind,src=$CAN_WORK,dst=/work" \
  --mount "type=bind,src=$CAN_PROJECT/bootstrap/control,dst=/control,readonly" \
  "${CAN_EXTRA_MOUNTS[@]}" \
  --entrypoint /usr/bin/timeout "$(cat "$CAN_PROJECT/bootstrap/environment/image-id.txt")" \
  --signal=TERM --kill-after=30s "${CAN_REMAINING}s" /bin/bash -c \
  'set -e; umask 077; test ! -e /control/STOP; if [ "${1-}" = --audit ]; then exec python3 /code/scripts/audit-smoke.py > /work/audit.log 2>&1; fi; if [ "$#" -gt 0 ]; then python3 -m unittest discover -s /code/tests -v > /work/unittest.log 2>&1; fi; exec python3 -m collusion_handoff.smoke "$@" > /work/supervisor.log 2>&1' \
  -- "${CAN_ACTION[@]}"
docker inspect "$CAN_NAME" > "$CAN_WORK/docker-inspect.json"
