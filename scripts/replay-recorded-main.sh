#!/usr/bin/env bash
# Operational launcher only; all scientific entrypoints remain frozen at a054.
set -euo pipefail
umask 077
test "$(hostname)" = caniculus.appliedai.local
test "$(id -u):$(id -g)" = 4200509:4200509
FINAL_PROJECT=${1:?Existing personal project directory}
test "$(realpath "$FINAL_PROJECT")" = "$FINAL_PROJECT"
case "$FINAL_PROJECT" in /mnt/ssd/a.maslov/*) ;; *) exit 2;; esac
FINAL_SHA=a0548b748c7c749ae8c75baa86aec4f207f86375
FINAL_RUN="$FINAL_PROJECT/runs/main-r4-20261025"
FINAL_CODE="$FINAL_PROJECT/code/$FINAL_SHA"
FINAL_REVIEW="$FINAL_PROJECT/inputs/main-review-$FINAL_SHA"
FINAL_WORK=${2:?New output directory under the project final directory}
test "$(dirname "$FINAL_WORK")" = "$FINAL_PROJECT/final"
[[ "$(basename "$FINAL_WORK")" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]+$ ]]
FINAL_NAME="a-maslov-memcontrast-recorded-replay-$(date -u +%Y%m%dT%H%M%S)"
FINAL_IMAGE=sha256:bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d
FINAL_DIGEST=sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9
FINAL_MODEL="$FINAL_PROJECT/inputs/Qwen3.8-27B-FP8/017b9c7af6b5689d5dd426a76e0bc077eb5ca20a"
FINAL_VENDOR="$FINAL_PROJECT/dependencies/travel-tools-d0bc2174eea3b3737f6922b89897db4f21ea6f835f7354ee702edbbc9880b3bc"
FINAL_IDENTITIES="$FINAL_PROJECT/bootstrap/preparation-31f9c028bd8615c4b7648144c278ed0d6ffbe56c"
FINAL_UPSTREAM=/opt/ai-collusion/sources/MemoryArena
FINAL_DATA=$(jq -r .main_data_source.host_directory "$FINAL_CODE/configs/main-pipeline.json")
test ! -e "$FINAL_PROJECT/bootstrap/control/STOP"
test ! -e "$FINAL_PROJECT/STOP"
test ! -e "$FINAL_RUN/STOP"
test -z "$(docker ps --filter label=owner=a.maslov --filter label=main_run=main-r4-20261025 --format '{{.ID}}')"
test ! -e "$FINAL_WORK"
! docker container inspect "$FINAL_NAME" >/dev/null 2>&1
test "$(nproc)" -ge 8
test "$(awk '$1=="MemAvailable:" {print $2}' /proc/meminfo)" -ge 67108864
test "$(df --output=avail -B1 "$FINAL_PROJECT" | tail -1)" -ge 21474836480
for FINAL_PATH in "$FINAL_PROJECT" "$FINAL_RUN" "$FINAL_CODE" "$FINAL_REVIEW" "$FINAL_MODEL" "$FINAL_VENDOR" "$FINAL_IDENTITIES" "$FINAL_DATA" "$FINAL_PROJECT/inputs/MemoryArena" "$FINAL_PROJECT/inputs/travel-database"; do
 test "$(realpath "$FINAL_PATH")" = "$FINAL_PATH"
 test "$(stat -c %u "$FINAL_PATH")" = 4200509
done
test "$(sha256sum "$FINAL_CODE/scripts/audit-main-cache.py" | cut -d ' ' -f1)" = 0ad4a9aeacff40e554c8a908480d5a26c23fd7abb32e274b1528f95acd338f73
test "$(sha256sum "$FINAL_CODE/scripts/analyze-main.py" | cut -d ' ' -f1)" = 66d099b198d84378fc6cee001590efdae6eaad8c3c84596d0bbdf4d29290512f
docker image inspect "$FINAL_IMAGE" --format '{{json .RepoDigests}}' | jq -e --arg digest "$FINAL_DIGEST" 'any(.[]; endswith("@"+$digest))' >/dev/null
mkdir -p "$FINAL_PROJECT/final"
test "$(realpath "$FINAL_PROJECT/final")" = "$FINAL_PROJECT/final"
test "$(stat -c %u "$FINAL_PROJECT/final")" = 4200509
exec 9>"$FINAL_PROJECT/final/launch.lock"
flock -n 9
test ! -e "$FINAL_WORK"
mkdir -p "$FINAL_WORK/home" "$FINAL_WORK/tmp" "$FINAL_WORK/cache"
cp -- "$0" "$FINAL_WORK/launcher.sh"
sha256sum "$FINAL_WORK/launcher.sh" > "$FINAL_WORK/launcher.sha256"
FINAL_CID=$(docker create --name "$FINAL_NAME" --label owner=a.maslov --label main_run=main-r4-20261025 --label computation=cpu --label "adapter_commit=$FINAL_SHA" \
 --user 4200509:4200509 --restart=no --network=none --cpus 8 --memory 64g --memory-swap 64g --pids-limit 1024 --shm-size 8g \
 --cap-drop=ALL --security-opt=no-new-privileges \
 --env NVIDIA_VISIBLE_DEVICES=void --env CUDA_VISIBLE_DEVICES= --env "ADAPTER_COMMIT=$FINAL_SHA" \
 --env MAIN_PHASE=continuations --env CYCLE_PHASE=continuations --env HOME=/work/home --env TMPDIR=/work/tmp \
 --env PYTHONDONTWRITEBYTECODE=1 --env PYTHONOPTIMIZE=0 --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1 --env HF_HOME=/cache/hf --env XDG_CACHE_HOME=/cache/xdg \
 --env TORCH_HOME=/cache/torch --env TORCHINDUCTOR_CACHE_DIR=/cache/torchinductor --env TRITON_CACHE_DIR=/cache/triton --env TORCH_EXTENSIONS_DIR=/cache/torch-extensions \
 --env VLLM_CACHE_ROOT=/cache/vllm --env CUDA_CACHE_PATH=/cache/cuda --env NUMBA_CACHE_DIR=/cache/numba \
 --env OMP_NUM_THREADS=1 --env OPENBLAS_NUM_THREADS=1 --env MKL_NUM_THREADS=1 --env NUMEXPR_NUM_THREADS=1 --env TOKENIZERS_PARALLELISM=false \
 --env "PYTHONPATH=/vendor:/code/src:$FINAL_UPSTREAM" --env "MEMORYARENA_ROOT=$FINAL_UPSTREAM" \
 --mount "type=bind,src=$FINAL_CODE,dst=/code,readonly" --mount "type=bind,src=$FINAL_REVIEW,dst=/review,readonly" \
 --mount "type=bind,src=$FINAL_RUN,dst=/cycle,readonly" --mount "type=bind,src=$FINAL_MODEL,dst=/model,readonly" \
 --mount "type=bind,src=$FINAL_DATA,dst=/main-data,readonly" --mount "type=bind,src=$FINAL_VENDOR,dst=/vendor,readonly" \
 --mount "type=bind,src=$FINAL_IDENTITIES,dst=/identities,readonly" --mount "type=bind,src=$FINAL_PROJECT/inputs/MemoryArena,dst=$FINAL_UPSTREAM,readonly" \
 --mount "type=bind,src=$FINAL_PROJECT/inputs/travel-database,dst=/database,readonly" --mount "type=bind,src=$FINAL_PROJECT/bootstrap/control,dst=/control,readonly" \
 --mount "type=bind,src=$FINAL_WORK,dst=/work" --mount "type=bind,src=$FINAL_WORK/cache,dst=/cache" \
 --workdir "$FINAL_UPSTREAM" --entrypoint /usr/bin/timeout "$FINAL_IMAGE" --signal=TERM --kill-after=30s 7200s /bin/bash -c '
set -euo pipefail
umask 077
timeout --signal=TERM --kill-after=30s 6600s python3 -B /code/scripts/audit-main-cache.py --run /cycle --review /review --inference-codepath /code --prefix-preflight /cycle/cpu-prefixes --continuation-preflight /cycle/cpu-continuations --model /model --output /work/cache-audit > /work/cache-audit.log 2>&1
python3 -B -c '\''import json; a=json.load(open("/work/cache-audit/audit.json")); assert a["status"]=="passed_cache_only" and a["new_model_calls"]==0'\''
timeout --signal=TERM --kill-after=30s 600s python3 -B /code/scripts/analyze-main.py --run /cycle --review /review --cache-audit /work/cache-audit/audit.json --output /work/analysis.json > /work/analysis.log 2>&1
')
printf '%s\n' "$FINAL_CID" > "$FINAL_WORK/created-container-id.txt"
docker inspect "$FINAL_CID" > "$FINAL_WORK/docker-created-inspect.json"
jq -e '.[0] | .Config.User=="4200509:4200509" and .HostConfig.NetworkMode=="none" and ((.HostConfig.DeviceRequests // [])|length)==0 and ((.HostConfig.Devices // [])|length)==0 and .HostConfig.NanoCpus==8000000000 and .HostConfig.Memory==68719476736' "$FINAL_WORK/docker-created-inspect.json" >/dev/null
test ! -e "$FINAL_PROJECT/bootstrap/control/STOP"
test ! -e "$FINAL_PROJECT/STOP"
test ! -e "$FINAL_RUN/STOP"
docker start "$FINAL_CID"
docker inspect "$FINAL_CID" > "$FINAL_WORK/docker-started-inspect.json"
printf 'FINAL_OUTPUT=%s\nCONTAINER_ID=%s\n' "$FINAL_WORK" "$FINAL_CID"
