#!/usr/bin/env bash
# Fixed logical scope: one group1/cp2 example, up to four new attempts, no retries.
set -euo pipefail
CAN_CODE_SHA=${1:?Pass full committed code SHA}
CAN_STAGE=${2:?Choose checks or run}
[[ "$CAN_CODE_SHA" =~ ^[0-9a-f]{40}$ ]]
case "$CAN_STAGE" in checks|run) ;; *) exit 2 ;; esac
test "$(hostname)" = caniculus.appliedai.local
test "$(id -un)" = a.maslov
test "$(id -u)" -ne 0
umask 077
CAN_PROJECT=/mnt/ssd/a.maslov/ai-collusion-sprint
CAN_CODE="$CAN_PROJECT/code/$CAN_CODE_SHA"
CAN_CONFIG="$CAN_CODE/configs/dev-package.draft.json"
CAN_CONTRACT="$CAN_CODE/configs/memory-group1.json"
CAN_MODEL="$CAN_PROJECT/inputs/Qwen3.8-27B-FP8/017b9c7af6b5689d5dd426a76e0bc077eb5ca20a"
CAN_IDENTITIES="$CAN_PROJECT/bootstrap/preparation-31f9c028bd8615c4b7648144c278ed0d6ffbe56c"
CAN_NATIVE="$CAN_PROJECT/runs/native-dev-921d20611324ab0429f119380e12389f44223e61"
CAN_TASKS="$CAN_PROJECT/bootstrap/preparation-33d29710c408fd9851e9de0db5e436cc623b595c/task-data"
CAN_LOCK_SHA=$(sha256sum "$CAN_CODE/configs/travel-tools.lock" | cut -d ' ' -f 1)
CAN_VENDOR="$CAN_PROJECT/dependencies/travel-tools-$CAN_LOCK_SHA"
CAN_CHECKS="$CAN_PROJECT/bootstrap/memory-checks-$CAN_CODE_SHA"
CAN_WORK="$CAN_CHECKS"
CAN_NAME="a-maslov-ai-collusion-memory-checks-${CAN_CODE_SHA:0:7}"
CAN_UPSTREAM=/opt/ai-collusion/sources/MemoryArena
CAN_UUID=GPU-441e6008-cd27-34b5-c81a-17dad9d4a894
CAN_STARTED=$(cat "$CAN_PROJECT/bootstrap/environment/compute-start.unix")
CAN_PACKAGE_DEADLINE=$((CAN_STARTED+43200))
test "$CAN_PACKAGE_DEADLINE" -eq 1792840396
test "$(jq -r '.limits.task_deadline_utc' "$CAN_CONTRACT")" = 2026-10-24T11:13:00Z
test "$(jq -r '.status' "$CAN_CONTRACT")" = human_approved_group1_deadline_exception
test "$(jq -r '.deadline_approval_evidence.human_message_id' "$CAN_CONTRACT")" = 01a16d43-ab1f-79c1-935e-db5bdd1935ed
CAN_TASK_DEADLINE=1792840380
CAN_REMAINING=$((CAN_TASK_DEADLINE-$(date +%s)))
test "$CAN_REMAINING" -gt 0
test ! -e "$CAN_PROJECT/bootstrap/control/STOP"
for CAN_PATH in "$CAN_PROJECT" "$CAN_CODE" "$CAN_MODEL" "$CAN_VENDOR" "$CAN_IDENTITIES" "$CAN_NATIVE" "$CAN_TASKS" "$CAN_PROJECT/inputs/MemoryArena" "$CAN_PROJECT/inputs/travel-database" "$CAN_PROJECT/cache"; do
  test "$(realpath -- "$CAN_PATH")" = "$CAN_PATH"
  test "$(stat -c %u -- "$CAN_PATH")" = "$(id -u)"
done
CAN_DEVICE=(--env NVIDIA_VISIBLE_DEVICES=void --env CUDA_VISIBLE_DEVICES=)
if [ "$CAN_STAGE" = run ]; then
  CAN_WORK="$CAN_PROJECT/runs/memory-group1-cp2-v1"
  CAN_NAME=a-maslov-ai-collusion-memory-group1-cp2-v1
  test "$(jq -r '.status' "$CAN_CHECKS/preflight.json")" = passed_no_model_calls
  test "$(jq -r '.plan.adapter_commit' "$CAN_CHECKS/manifest.json")" = "$CAN_CODE_SHA"
  test "$(jq -r '.operations.gpu_uuid' "$CAN_CONFIG")" = "$CAN_UUID"
  test "$(jq -r '.operations.gpu_reservation.channel_notified' "$CAN_CONFIG")" = true
  test ! -e "$CAN_WORK"
  test ! -e "$CAN_PROJECT/runs/memory-group1-cp2-v1.started"
  test "$(nvidia-smi --id="$CAN_UUID" --query-compute-apps=pid --format=csv,noheader | wc -l)" -eq 0
  test -z "$(ss -H -lnt 'sport = :18083')"
  CAN_DEVICE=(--gpus "device=$CAN_UUID")
fi
test ! -e "$CAN_WORK"
mkdir -p "$CAN_WORK/home" "$CAN_WORK/tmp"
if [ "$CAN_STAGE" = run ]; then
  # Separate persistent sentinel prevents a new code SHA or deleted work dir resetting spend.
  (set -o noclobber; printf '%s\n' "$CAN_CODE_SHA" > "$CAN_PROJECT/runs/memory-group1-cp2-v1.started")
  cp "$CAN_CHECKS/manifest.json" "$CAN_CHECKS/source.json" "$CAN_CHECKS/preflight.json" "$CAN_WORK/"
  cp -R "$CAN_CHECKS/human-source-first" "$CAN_WORK/"
  nvidia-smi --id="$CAN_UUID" --query-gpu=index,uuid,name,memory.used,memory.free,utilization.gpu --format=csv,noheader > "$CAN_WORK/gpu-before.csv"
fi
docker run -d --name "$CAN_NAME" --label owner=a.maslov --label "adapter_commit=$CAN_CODE_SHA" \
  --label scope=memory-group1-cp2-v1 --user "$(id -u):$(id -g)" --restart=no --network=host \
  --cpus 8 --memory 64g --pids-limit 1024 --shm-size 8g --cap-drop=ALL --security-opt=no-new-privileges \
  "${CAN_DEVICE[@]}" --env "ADAPTER_COMMIT=$CAN_CODE_SHA" --env "PACKAGE_DEADLINE_UNIX=$CAN_PACKAGE_DEADLINE" \
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
  --mount "type=bind,src=$CAN_NATIVE,dst=/native,readonly" \
  --mount "type=bind,src=$CAN_TASKS,dst=/tasks,readonly" \
  --mount "type=bind,src=$CAN_VENDOR,dst=/vendor,readonly" \
  --mount "type=bind,src=$CAN_PROJECT/cache,dst=/cache" \
  --mount "type=bind,src=$CAN_WORK,dst=/work" \
  --mount "type=bind,src=$CAN_PROJECT/bootstrap/control,dst=/control,readonly" \
  --entrypoint /usr/bin/timeout "$(cat "$CAN_PROJECT/bootstrap/environment/image-id.txt")" \
  --signal=TERM --kill-after=30s "${CAN_REMAINING}s" /bin/bash -c \
  'set -e; umask 077; test ! -e /control/STOP; if [ "$1" = checks ]; then python3 -m unittest discover -s /code/tests -v > /work/unittest.log 2>&1; exec python3 -m collusion_handoff.memory_runner --preflight > /work/preflight.log 2>&1; fi; exec python3 -m collusion_handoff.memory_runner > /work/supervisor.log 2>&1' -- "$CAN_STAGE"
docker inspect "$CAN_NAME" > "$CAN_WORK/docker-inspect.json"
