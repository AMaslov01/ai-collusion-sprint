#!/usr/bin/env bash
# Shared main admission; the public GPU wrapper selects the final internal mode.
# No sample, budget, data path or GPU is supplied by a default.
set -euo pipefail
CAN_SHA=${1:?Full committed main pipeline SHA}
CAN_REVIEW=${2:?Absolute directory containing the reviewed main packet}
CAN_PHASE=${3:?prepare, prefixes or continuations}
CAN_MODE=${4:-cpu}
[[ "$CAN_SHA" =~ ^[0-9a-f]{40}$ ]]
case "$CAN_PHASE" in prepare|prefixes|continuations) ;; *) exit 2;; esac
case "$CAN_MODE" in cpu|gpu) ;; *) exit 2;; esac
test "$CAN_MODE:$CAN_PHASE" != gpu:prepare
test "$(hostname)" = caniculus.appliedai.local
test "$(id -un)" = a.maslov
test "$(id -u)" -ne 0
umask 077
CAN_PROJECT=/mnt/ssd/a.maslov/ai-collusion-sprint
CAN_CODE="$CAN_PROJECT/code/$CAN_SHA"
CAN_MANIFEST="$CAN_REVIEW/main-manifest.json"
test -f "$CAN_MANIFEST"
test "$(jq -r .pipeline.code_commit "$CAN_MANIFEST")" = "$CAN_SHA"
CAN_RUN=$(jq -r .run_id "$CAN_MANIFEST")
[[ "$CAN_RUN" =~ ^[a-z0-9][a-z0-9-]{1,63}$ ]]
CAN_ROOT=$(jq -r .artifacts.run_root "$CAN_MANIFEST")
test "$CAN_ROOT" = "$CAN_PROJECT/runs/$CAN_RUN"
case "$CAN_REVIEW" in "$CAN_ROOT"|"$CAN_ROOT/"*) exit 2;; esac
CAN_CONFIG_PATH=$(jq -r .pipeline.config_path "$CAN_MANIFEST")
[[ "$CAN_CONFIG_PATH" != /* && "/$CAN_CONFIG_PATH/" != */../* ]]
CAN_CONFIG="$CAN_CODE/$CAN_CONFIG_PATH"
test "$(realpath "$CAN_CONFIG")" = "$CAN_CONFIG"
test "$(sha256sum "$CAN_CONFIG" | cut -d ' ' -f1)" = "$(jq -r .pipeline.config_sha256 "$CAN_MANIFEST")"
test "$(jq -r .scope "$CAN_CONFIG")" = main_pipeline_configuration_v1
CAN_DATA=$(jq -r .main_data_source.host_directory "$CAN_CONFIG")
CAN_MODEL="$CAN_PROJECT/inputs/Qwen3.8-27B-FP8/017b9c7af6b5689d5dd426a76e0bc077eb5ca20a"
CAN_VENDOR="$CAN_PROJECT/dependencies/travel-tools-d0bc2174eea3b3737f6922b89897db4f21ea6f835f7354ee702edbbc9880b3bc"
CAN_IDENTITIES="$CAN_PROJECT/bootstrap/preparation-31f9c028bd8615c4b7648144c278ed0d6ffbe56c"
CAN_UPSTREAM=/opt/ai-collusion/sources/MemoryArena
for CAN_PATH in "$CAN_PROJECT" "$CAN_CODE" "$CAN_REVIEW" "$CAN_DATA" "$CAN_MODEL" "$CAN_VENDOR" "$CAN_IDENTITIES" "$CAN_PROJECT/cache" "$CAN_PROJECT/inputs/MemoryArena" "$CAN_PROJECT/inputs/travel-database"; do
 test "$(realpath "$CAN_PATH")" = "$CAN_PATH"
 test "$(stat -c %u "$CAN_PATH")" = "$(id -u)"
done
case "$CAN_DATA" in "$CAN_PROJECT/inputs/"*) ;; *) exit 2;; esac
test ! -e "$CAN_PROJECT/bootstrap/control/STOP"
# A main preflight must not expand the agreed CPU/RAM allowance during DEV.
test -z "$(docker ps --filter label=owner=a.maslov --filter label=computation=gpu --format '{{.Names}}')"
mkdir -p "$CAN_ROOT"
test "$(realpath "$CAN_ROOT")" = "$CAN_ROOT"
test "$(stat -c %u "$CAN_ROOT")" = "$(id -u)"
exec 9>"$CAN_ROOT/launch.lock"
flock -n 9
CAN_CPU=$(jq -r .hardware.cpu_limit "$CAN_MANIFEST")
CAN_RAM=$(jq -r .hardware.RAM_limit_GiB "$CAN_MANIFEST")
CAN_SHM=$(jq -r .hardware.shm_GiB "$CAN_MANIFEST")
CAN_PIDS=$(jq -r .operations.pids_limit "$CAN_CONFIG")
[[ "$CAN_CPU" =~ ^[1-8]$ && "$CAN_RAM" =~ ^[1-9][0-9]?$ && "$CAN_SHM" =~ ^[1-9][0-9]?$ ]]
test "$CAN_RAM" -le 64
test "$CAN_SHM" -le "$CAN_RAM"
test "$CAN_PIDS" = 1024
CAN_IMAGE=$(cat "$CAN_PROJECT/bootstrap/environment/image-id.txt")
CAN_DIGEST=$(jq -r .pipeline.image_digest "$CAN_MANIFEST")
docker image inspect "$CAN_IMAGE" --format '{{json .RepoDigests}}' | jq -e --arg digest "$CAN_DIGEST" 'any(.[]; endswith("@"+$digest))' >/dev/null
CAN_END=$(date -u -d "$(jq -r .budget.absolute_cutoff_utc "$CAN_MANIFEST")" +%s)
CAN_EXTRA=()
CAN_MOUNT_SUFFIX=,readonly
CAN_WORK="$CAN_ROOT/cpu-$CAN_PHASE"
CAN_COMMAND='exec python3 -m collusion_handoff.main_cycle --preflight > /work/preflight.log 2>&1'
CAN_NAME="a-maslov-ai-collusion-main-$CAN_RUN-cpu-$CAN_PHASE-${CAN_SHA:0:7}"
if [ "$CAN_MODE" = gpu ]; then
 CAN_PREFLIGHT="$CAN_ROOT/cpu-$CAN_PHASE"
 CAN_RECEIPT="$CAN_PREFLIGHT/preflight.json"
 test "$(jq -r .status "$CAN_RECEIPT")" = passed_no_model_calls
 test "$(jq -r .code_commit "$CAN_RECEIPT")" = "$CAN_SHA"
 test "$(jq -r .phase "$CAN_RECEIPT")" = "$CAN_PHASE"
 while IFS=$'\t' read -r CAN_FILE CAN_HASH; do
  test "$(basename "$CAN_FILE")" = "$CAN_FILE"
  test "$(sha256sum "$CAN_REVIEW/$CAN_FILE" | cut -d ' ' -f1)" = "$CAN_HASH"
 done < <(jq -r '.review_files_sha256|to_entries[]|[.key,.value]|@tsv' "$CAN_RECEIPT")
 test "$(jq -r '.hardware.tensor_parallel_size' "$CAN_MANIFEST")" = 1
 test "$(jq -r '.hardware.endpoint_count' "$CAN_MANIFEST")" = 1
 CAN_UUID=$(jq -r '.hardware.gpu_uuids[0]' "$CAN_MANIFEST")
 test "$(nvidia-smi --id="$CAN_UUID" --query-gpu=name --format=csv,noheader)" = "$(jq -r '.hardware.gpu_names[0]' "$CAN_MANIFEST")"
 test -z "$(nvidia-smi --id="$CAN_UUID" --query-compute-apps=pid --format=csv,noheader)"
 test -z "$(ss -H -lnt 'sport = :18083')"
 CAN_USED=0
 mkdir -p "$CAN_ROOT/resource-inspects"
 if [ -f "$CAN_ROOT/gpu-phase-names.txt" ]; then
  while read -r CAN_PREVIOUS; do
   test "$(docker inspect -f '{{index .Config.Labels "owner"}}' "$CAN_PREVIOUS")" = a.maslov
   test "$(docker inspect -f '{{index .Config.Labels "main_run"}}' "$CAN_PREVIOUS")" = "$CAN_RUN"
   test "$(docker inspect -f '{{.State.Running}}' "$CAN_PREVIOUS")" = false
   CAN_INSPECT="$CAN_ROOT/resource-inspects/$CAN_PREVIOUS.json"
   docker inspect "$CAN_PREVIOUS" > "$CAN_INSPECT"
   CAN_NS=$(($(date -d "$(jq -r '.[0].State.FinishedAt' "$CAN_INSPECT")" +%s%N)-$(date -d "$(jq -r '.[0].State.StartedAt' "$CAN_INSPECT")" +%s%N)))
   CAN_USED=$((CAN_USED+(CAN_NS+999999999)/1000000000))
  done < "$CAN_ROOT/gpu-phase-names.txt"
 fi
 CAN_GRACE=$(jq -r .limits.shutdown_reserve_seconds "$CAN_CONFIG")
 test "$CAN_GRACE" = 30
 CAN_REMAINING=$(($(jq -r .budget.gpu_container_seconds "$CAN_MANIFEST")-CAN_USED-CAN_GRACE))
 CAN_SERVICE_END=$(($(date +%s)+CAN_REMAINING))
 CAN_END=$((CAN_END-CAN_GRACE))
 if [ "$CAN_SERVICE_END" -lt "$CAN_END" ]; then CAN_END=$CAN_SERVICE_END; fi
 printf '%s\n' "$CAN_END" > "$CAN_ROOT/gpu-service-deadline.unix"
 CAN_WORK="$CAN_ROOT/$CAN_PHASE"
 CAN_NAME="a-maslov-ai-collusion-main-$CAN_RUN-$CAN_PHASE-${CAN_SHA:0:7}"
 CAN_MOUNT_SUFFIX=
 CAN_EXTRA+=(--gpus "device=$CAN_UUID" --mount "type=bind,src=$CAN_PREFLIGHT,dst=/preflight,readonly")
 # Seal historical inputs also through their /cycle aliases, not only /preflight.
 CAN_EXTRA+=(--mount "type=bind,src=$CAN_ROOT/cpu-prepare,dst=/cycle/cpu-prepare,readonly"
             --mount "type=bind,src=$CAN_PREFLIGHT,dst=/cycle/cpu-$CAN_PHASE,readonly")
 if [ "$CAN_PHASE" = continuations ]; then
  CAN_EXTRA+=(--mount "type=bind,src=$CAN_ROOT/prefixes,dst=/cycle/prefixes,readonly")
 fi
 CAN_COMMAND='exec python3 -m collusion_handoff.main_cycle > /work/supervisor.log 2>&1'
else
 CAN_CPU_END=$(($(date +%s)+900))
 if [ "$CAN_CPU_END" -lt "$CAN_END" ]; then CAN_END=$CAN_CPU_END; fi
 CAN_EXTRA+=(--env NVIDIA_VISIBLE_DEVICES=void --env CUDA_VISIBLE_DEVICES=)
fi
CAN_SECONDS=$((CAN_END-$(date +%s)))
test "$CAN_SECONDS" -gt 0
test ! -e "$CAN_WORK"
mkdir -p "$CAN_WORK/home" "$CAN_WORK/tmp"
sha256sum "$0" > "$CAN_WORK/launcher.sha256"
CAN_NETWORK=none
if [ "$CAN_MODE" = gpu ]; then CAN_NETWORK=host; fi
docker run -d --name "$CAN_NAME" --label owner=a.maslov --label "main_run=$CAN_RUN" --label "computation=$CAN_MODE" --label "adapter_commit=$CAN_SHA" \
 --user "$(id -u):$(id -g)" --restart=no --network="$CAN_NETWORK" --cpus "$CAN_CPU" --memory "${CAN_RAM}g" --pids-limit "$CAN_PIDS" --shm-size "${CAN_SHM}g" \
 --cap-drop=ALL --security-opt=no-new-privileges "${CAN_EXTRA[@]}" \
 --env "ADAPTER_COMMIT=$CAN_SHA" --env "MAIN_PHASE=$CAN_PHASE" --env "CYCLE_PHASE=$CAN_PHASE" --env "PACKAGE_DEADLINE_UNIX=$CAN_END" \
 --env HOME=/work/home --env TMPDIR=/work/tmp --env PYTHONDONTWRITEBYTECODE=1 \
 --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1 --env HF_HOME=/cache/hf --env XDG_CACHE_HOME=/cache/xdg \
 --env TORCH_HOME=/cache/torch --env TORCHINDUCTOR_CACHE_DIR=/cache/torchinductor --env TRITON_CACHE_DIR=/cache/triton --env TORCH_EXTENSIONS_DIR=/cache/torch-extensions \
 --env VLLM_CACHE_ROOT=/cache/vllm --env CUDA_CACHE_PATH=/cache/cuda --env NUMBA_CACHE_DIR=/cache/numba \
 --env "PYTHONPATH=/vendor:/code/src:$CAN_UPSTREAM" --env MEMORYARENA_ROOT="$CAN_UPSTREAM" \
 --mount "type=bind,src=$CAN_CODE,dst=/code,readonly" --mount "type=bind,src=$CAN_REVIEW,dst=/review,readonly" \
 --mount "type=bind,src=$CAN_DATA,dst=/main-data,readonly" --mount "type=bind,src=$CAN_MODEL,dst=/model,readonly" \
 --mount "type=bind,src=$CAN_IDENTITIES,dst=/identities,readonly" --mount "type=bind,src=$CAN_VENDOR,dst=/vendor,readonly" \
 --mount "type=bind,src=$CAN_PROJECT/inputs/MemoryArena,dst=$CAN_UPSTREAM,readonly" --mount "type=bind,src=$CAN_PROJECT/inputs/travel-database,dst=/database,readonly" \
 --mount "type=bind,src=$CAN_ROOT,dst=/cycle$CAN_MOUNT_SUFFIX" --mount "type=bind,src=$CAN_WORK,dst=/work" \
 --mount "type=bind,src=$CAN_PROJECT/cache,dst=/cache" --mount "type=bind,src=$CAN_PROJECT/bootstrap/control,dst=/control,readonly" \
 --workdir "$CAN_UPSTREAM" --entrypoint /usr/bin/timeout "$CAN_IMAGE" --signal=TERM --kill-after=30s "${CAN_SECONDS}s" /bin/bash -c "$CAN_COMMAND"
if [ "$CAN_MODE" = gpu ]; then printf '%s\n' "$CAN_NAME" >> "$CAN_ROOT/gpu-phase-names.txt"; fi
docker inspect "$CAN_NAME" > "$CAN_WORK/docker-inspect.json"
