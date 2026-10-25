#!/usr/bin/env bash
# Source-only candidate screening; this entry point cannot start a model phase.
set -euo pipefail
CAN_SHA=${1:?Full committed code SHA}
[[ "$CAN_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$(hostname)" = caniculus.appliedai.local
test "$(id -un)" = a.maslov
test "$(id -u)" -ne 0
umask 077
CAN_PROJECT=/mnt/ssd/a.maslov/ai-collusion-sprint
CAN_CODE="$CAN_PROJECT/code/$CAN_SHA"
CAN_REVIEW="$CAN_PROJECT/inputs/main-candidate-$CAN_SHA"
CAN_WORK="$CAN_PROJECT/runs/main-candidate-$CAN_SHA"
CAN_UPSTREAM=/opt/ai-collusion/sources/MemoryArena
CAN_TASKS="$CAN_PROJECT/bootstrap/preparation-33d29710c408fd9851e9de0db5e436cc623b595c/task-data"
CAN_VENDOR="$CAN_PROJECT/dependencies/travel-tools-d0bc2174eea3b3737f6922b89897db4f21ea6f835f7354ee702edbbc9880b3bc"
for CAN_PATH in "$CAN_PROJECT" "$CAN_CODE" "$CAN_REVIEW" "$CAN_TASKS" "$CAN_VENDOR" "$CAN_PROJECT/inputs/MemoryArena" "$CAN_PROJECT/inputs/travel-database"; do
 test "$(realpath "$CAN_PATH")" = "$CAN_PATH"
 test "$(stat -c %u "$CAN_PATH")" = "$(id -u)"
done
test ! -e "$CAN_PROJECT/bootstrap/control/STOP"
exec 9>"$CAN_PROJECT/runs/readiness-cycle-v5-20261024/launch.lock"
flock -n 9
for CAN_ACTIVE in $(docker ps --filter label=owner=a.maslov --format '{{.Names}}'); do
 case "$CAN_ACTIVE" in a-maslov-ai-collusion*) exit 1;; esac
done
test "$(nproc)" -ge 8
test "$(awk '$1=="MemAvailable:" {print $2}' /proc/meminfo)" -ge 67108864
test "$(jq -r .schema "$CAN_REVIEW/candidate-frame-configuration.json")" = main_candidate_frame_configuration_v1
test "$(jq -r .code_commit "$CAN_REVIEW/candidate-frame-configuration.json")" = "$CAN_SHA"
test "$(sha256sum "$CAN_CODE/configs/main-science-plan.json" | cut -d ' ' -f1)" = "$(jq -r .science_freeze_sha256 "$CAN_REVIEW/candidate-frame-configuration.json")"
test ! -e "$CAN_WORK"
mkdir -p "$CAN_WORK/home" "$CAN_WORK/tmp"
sha256sum "$0" > "$CAN_WORK/launcher.sha256"
CAN_IMAGE=$(cat "$CAN_PROJECT/bootstrap/environment/image-id.txt")
test "$CAN_IMAGE" = sha256:bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d
CAN_NAME="a-maslov-ai-collusion-main-candidates-${CAN_SHA:0:7}"
CAN_ID=$(docker create --name "$CAN_NAME" --label owner=a.maslov --label computation=cpu --label scope=main-source-only-candidates --label "adapter_commit=$CAN_SHA" \
 --user "$(id -u):$(id -g)" --restart=no --network=none --cpus 8 --memory 64g --pids-limit 1024 --shm-size 8g --cap-drop=ALL --security-opt=no-new-privileges \
 --env NVIDIA_VISIBLE_DEVICES=void --env CUDA_VISIBLE_DEVICES= --env HOME=/work/home --env TMPDIR=/work/tmp --env PYTHONDONTWRITEBYTECODE=1 \
 --env "ADAPTER_COMMIT=$CAN_SHA" --env "MEMORYARENA_ROOT=$CAN_UPSTREAM" --env "PYTHONPATH=/vendor:/code/src:$CAN_UPSTREAM" --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1 \
 --mount "type=bind,src=$CAN_CODE,dst=/code,readonly" --mount "type=bind,src=$CAN_REVIEW,dst=/review,readonly" --mount "type=bind,src=$CAN_TASKS,dst=/tasks,readonly" \
 --mount "type=bind,src=$CAN_VENDOR,dst=/vendor,readonly" --mount "type=bind,src=$CAN_PROJECT/inputs/MemoryArena,dst=$CAN_UPSTREAM,readonly" \
 --mount "type=bind,src=$CAN_PROJECT/inputs/travel-database,dst=/database,readonly" --mount "type=bind,src=$CAN_WORK,dst=/work" --workdir "$CAN_UPSTREAM" \
 --entrypoint /usr/bin/timeout "$CAN_IMAGE" --signal=TERM --kill-after=30s 900s /bin/bash -c '
 set -eu
 umask 077
 exec python3 -B -m collusion_handoff.main_cycle --candidate-frame /review/candidate-frame-configuration.json --frame-source /tasks/data.jsonl --frame-output /work/candidate-frame.json > /work/candidate-frame.log 2>&1
 ')
docker start "$CAN_ID"
docker inspect "$CAN_ID" > "$CAN_WORK/docker-inspect.json"
