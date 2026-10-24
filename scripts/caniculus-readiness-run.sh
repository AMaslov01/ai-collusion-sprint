#!/usr/bin/env bash
# Exact reviewed phase; old runs and the main experiment cannot be resumed here.
set -euo pipefail
CAN_SHA=${1:?Full committed code SHA}
CAN_PHASE=${2:?Reviewed phase name}
[[ "$CAN_SHA" =~ ^[0-9a-f]{40}$ ]]
case "$CAN_PHASE" in development-memory-v1|development-prefix-v1|development-checkpoint-memory-v1) ;; *) exit 2;; esac
test "$(hostname)" = caniculus.appliedai.local
test "$(id -un)" = a.maslov
test "$(id -u)" -ne 0
umask 077
CAN_PROJECT=/mnt/ssd/a.maslov/ai-collusion-sprint
CAN_CODE="$CAN_PROJECT/code/$CAN_SHA"
CAN_ROOT="$CAN_PROJECT/runs/readiness-cycle-v4-20261024"
CAN_PREFLIGHT="$CAN_ROOT/cpu-$CAN_SHA"
if [ "$CAN_PHASE" != development-memory-v1 ]; then CAN_PREFLIGHT="$CAN_ROOT/cpu-$CAN_PHASE-$CAN_SHA"; fi
CAN_REVIEW="$CAN_PROJECT/inputs/readiness-v4-review-$CAN_SHA"
CAN_MODEL="$CAN_PROJECT/inputs/Qwen3.8-27B-FP8/017b9c7af6b5689d5dd426a76e0bc077eb5ca20a"
CAN_IDENTITIES="$CAN_PROJECT/bootstrap/preparation-31f9c028bd8615c4b7648144c278ed0d6ffbe56c"
CAN_VENDOR="$CAN_PROJECT/dependencies/travel-tools-d0bc2174eea3b3737f6922b89897db4f21ea6f835f7354ee702edbbc9880b3bc"
CAN_TASKS="$CAN_PROJECT/bootstrap/preparation-33d29710c408fd9851e9de0db5e436cc623b595c/task-data"
CAN_UPSTREAM=/opt/ai-collusion/sources/MemoryArena
CAN_UUID=GPU-441e6008-cd27-34b5-c81a-17dad9d4a894
for CAN_PATH in "$CAN_PROJECT" "$CAN_CODE" "$CAN_ROOT" "$CAN_PREFLIGHT" "$CAN_REVIEW" "$CAN_MODEL" "$CAN_IDENTITIES" "$CAN_VENDOR" "$CAN_TASKS" "$CAN_PROJECT/cache" "$CAN_PROJECT/inputs/MemoryArena" "$CAN_PROJECT/inputs/travel-database" "$CAN_PROJECT/runs/repair-cycle-v3-20261024"; do
 test "$(realpath "$CAN_PATH")" = "$CAN_PATH"
 test "$(stat -c %u "$CAN_PATH")" = "$(id -u)"
done
exec 9>"$CAN_ROOT/launch.lock"
flock -n 9
test ! -e "$CAN_PROJECT/bootstrap/control/STOP"
test "$(jq -r .status "$CAN_PREFLIGHT/preflight.json")" = passed_no_model_calls
test "$(jq -r .code_commit "$CAN_PREFLIGHT/phase-manifest.json")" = "$CAN_SHA"
test "$(jq -r .status "$CAN_REVIEW/dispatch-$CAN_PHASE.json")" = authorized_bounded_DEV_phase
test "$(jq -r .code_commit "$CAN_REVIEW/dispatch-$CAN_PHASE.json")" = "$CAN_SHA"
test ! -e "$CAN_ROOT/$CAN_PHASE.started"
test -z "$(docker ps --filter label=owner=a.maslov --filter label=scope=readiness-cycle-v4 --filter label=computation=gpu --format '{{.Names}}')"
test -z "$(nvidia-smi --id="$CAN_UUID" --query-compute-apps=pid --format=csv,noheader)"
test -z "$(ss -H -lnt 'sport = :18083')"
mkdir -p "$CAN_ROOT/resource-inspects"
while read -r CAN_NAME; do
 test "$(docker inspect -f '{{index .Config.Labels "owner"}}' "$CAN_NAME")" = a.maslov
 test "$(docker inspect -f '{{.State.Running}}' "$CAN_NAME")" = false
 docker inspect "$CAN_NAME" > "$CAN_ROOT/resource-inspects/$CAN_NAME.json"
 if ! grep -Fxq "$CAN_NAME" "$CAN_ROOT/gpu-phase-names.txt" 2>/dev/null; then printf '%s\n' "$CAN_NAME" >> "$CAN_ROOT/gpu-phase-names.txt"; fi
done < <(docker ps -a --filter label=owner=a.maslov --filter label=scope=readiness-cycle-v4 --filter label=computation=gpu --format '{{.Names}}')
CAN_USED=0
if [ -f "$CAN_ROOT/gpu-phase-names.txt" ]; then
 while read -r CAN_NAME; do
  CAN_INSPECT="$CAN_ROOT/resource-inspects/$CAN_NAME.json"
  test -s "$CAN_INSPECT"
  test "$(jq -r '.[0].State.Running' "$CAN_INSPECT")" = false
  test "$(jq -r '.[0].Config.Labels.owner' "$CAN_INSPECT")" = a.maslov
  CAN_START=$(jq -r '.[0].State.StartedAt' "$CAN_INSPECT")
  CAN_FINISH=$(jq -r '.[0].State.FinishedAt' "$CAN_INSPECT")
  CAN_NS=$(($(date -d "$CAN_FINISH" +%s%N)-$(date -d "$CAN_START" +%s%N)))
  CAN_USED=$((CAN_USED+(CAN_NS+999999999)/1000000000))
 done < "$CAN_ROOT/gpu-phase-names.txt"
fi
CAN_CONFIG="$CAN_CODE/configs/readiness-cycle-v4.json"
CAN_GRACE=$(jq -r .limits.shutdown_reserve_seconds "$CAN_CONFIG")
CAN_REMAINING=$(($(jq -r .limits.gpu_service_wall_seconds "$CAN_CONFIG")-CAN_USED-CAN_GRACE))
CAN_CUTOFF=$(($(date -u -d "$(jq -r .limits.absolute_cutoff_utc "$CAN_CONFIG")" +%s)-CAN_GRACE))
CAN_END=$(($(date +%s)+CAN_REMAINING))
if [ "$CAN_CUTOFF" -lt "$CAN_END" ]; then CAN_END=$CAN_CUTOFF; fi
CAN_REMAINING=$((CAN_END-$(date +%s)))
test "$CAN_REMAINING" -gt 0
printf '%s\n' "$CAN_END" > "$CAN_ROOT/gpu-service-deadline.unix"
jq -n --argjson used "$CAN_USED" --argjson deadline "$CAN_END" --argjson grace "$CAN_GRACE" '{previous_allocated_gpu_container_seconds:$used,phase_admission_deadline_unix:$deadline,shutdown_reserve_seconds:$grace,method:"Sum prior allocated container lifetimes rounded up; CPU gaps do not reset or spend service allowance"}' > "$CAN_ROOT/service-admission.json"
sha256sum "$0" > "$CAN_ROOT/launcher-$CAN_PHASE.sha256"
CAN_WORK="$CAN_ROOT/$CAN_PHASE"
test ! -e "$CAN_WORK"
mkdir -p "$CAN_WORK/home" "$CAN_WORK/tmp"
(set -o noclobber; printf '%s\n' "$CAN_SHA" > "$CAN_ROOT/$CAN_PHASE.started")
CAN_NAME="a-maslov-ai-collusion-v4-$CAN_PHASE-${CAN_SHA:0:7}"
nvidia-smi --id="$CAN_UUID" --query-gpu=index,uuid,name,memory.used,memory.free,utilization.gpu --format=csv,noheader > "$CAN_WORK/gpu-before.csv"
docker run -d --name "$CAN_NAME" --label owner=a.maslov --label scope=readiness-cycle-v4 --label computation=gpu --label "adapter_commit=$CAN_SHA" \
 --user "$(id -u):$(id -g)" --restart=no --network=host --cpus 8 --memory 64g --pids-limit 1024 --shm-size 8g \
 --cap-drop=ALL --security-opt=no-new-privileges --gpus "device=$CAN_UUID" \
 --env HOME=/work/home --env TMPDIR=/work/tmp --env PYTHONDONTWRITEBYTECODE=1 --env "ADAPTER_COMMIT=$CAN_SHA" --env "CYCLE_PHASE=$CAN_PHASE" --env "PACKAGE_DEADLINE_UNIX=$CAN_END" \
 --env HF_HOME=/cache/hf --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1 --env XDG_CACHE_HOME=/cache/xdg \
 --env TORCH_HOME=/cache/torch --env TORCHINDUCTOR_CACHE_DIR=/cache/torchinductor --env TRITON_CACHE_DIR=/cache/triton --env TORCH_EXTENSIONS_DIR=/cache/torch-extensions \
 --env VLLM_CACHE_ROOT=/cache/vllm --env CUDA_CACHE_PATH=/cache/cuda --env NUMBA_CACHE_DIR=/cache/numba \
 --env "PYTHONPATH=/vendor:/code/src:$CAN_UPSTREAM" --env MEMORYARENA_ROOT="$CAN_UPSTREAM" --workdir "$CAN_UPSTREAM" \
 --mount "type=bind,src=$CAN_CODE,dst=/code,readonly" --mount "type=bind,src=$CAN_MODEL,dst=/model,readonly" \
 --mount "type=bind,src=$CAN_IDENTITIES,dst=/identities,readonly" --mount "type=bind,src=$CAN_PREFLIGHT,dst=/preflight,readonly" --mount "type=bind,src=$CAN_REVIEW,dst=/review,readonly" \
 --mount "type=bind,src=$CAN_PROJECT/inputs/MemoryArena,dst=$CAN_UPSTREAM,readonly" --mount "type=bind,src=$CAN_PROJECT/inputs/travel-database,dst=/database,readonly" \
 --mount "type=bind,src=$CAN_VENDOR,dst=/vendor,readonly" --mount "type=bind,src=$CAN_PROJECT/runs/repair-cycle-v3-20261024,dst=/previous,readonly" \
 --mount "type=bind,src=$CAN_TASKS,dst=/tasks,readonly" \
 --mount "type=bind,src=$CAN_ROOT,dst=/cycle" --mount "type=bind,src=$CAN_WORK,dst=/work" --mount "type=bind,src=$CAN_PROJECT/cache,dst=/cache" \
 --mount "type=bind,src=$CAN_PROJECT/bootstrap/control,dst=/control,readonly" --entrypoint /usr/bin/timeout "$(cat "$CAN_PROJECT/bootstrap/environment/image-id.txt")" \
 --signal=TERM --kill-after="${CAN_GRACE}s" "${CAN_REMAINING}s" /bin/bash -c 'umask 077; exec python3 -m collusion_handoff.readiness_cycle > /work/supervisor.log 2>&1'
printf '%s\n' "$CAN_NAME" >> "$CAN_ROOT/gpu-phase-names.txt"
docker inspect "$CAN_NAME" > "$CAN_WORK/docker-inspect.json"
