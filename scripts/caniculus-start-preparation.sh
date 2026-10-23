#!/usr/bin/env bash
# Launch once after caniculus-prepare.sh and transfer of this committed snapshot.
set -euo pipefail
CAN_CODE_SHA=${1:?Pass the full adapter commit SHA}
[[ "$CAN_CODE_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$(hostname)" = caniculus.appliedai.local
test "$(id -un)" = a.maslov
test "$(id -u)" -ne 0
umask 077
CAN_PROJECT=/mnt/ssd/a.maslov/ai-collusion-sprint
CAN_CODE="$CAN_PROJECT/code/$CAN_CODE_SHA"
CAN_MODEL="$CAN_PROJECT/inputs/Qwen3.8-27B-FP8/017b9c7af6b5689d5dd426a76e0bc077eb5ca20a"
CAN_WORK="$CAN_PROJECT/bootstrap/preparation-$CAN_CODE_SHA"
CAN_CONTROL="$CAN_PROJECT/bootstrap/control"
for CAN_PATH in "$CAN_PROJECT" "$CAN_CODE" "$CAN_PROJECT/inputs/MemoryArena"; do
  test "$(realpath -- "$CAN_PATH")" = "$CAN_PATH"
  test "$(stat -c %u -- "$CAN_PATH")" = "$(id -u)"
done
for CAN_PATH in "$CAN_MODEL" "$CAN_WORK/home" "$CAN_CONTROL"; do
  mkdir -p -- "$CAN_PATH"
  test "$(realpath -- "$CAN_PATH")" = "$CAN_PATH"
  test "$(stat -c %u -- "$CAN_PATH")" = "$(id -u)"
done
test ! -e "$CAN_CONTROL/STOP"
CAN_START="$CAN_PROJECT/bootstrap/environment/compute-start.unix"
if [ ! -e "$CAN_START" ]; then date +%s > "$CAN_START"; fi
CAN_STARTED=$(cat "$CAN_START")
[[ "$CAN_STARTED" =~ ^[0-9]+$ ]]
CAN_LIMIT=$(jq -r '.operations.wall_time_seconds' "$CAN_CODE/configs/dev-package.draft.json")
CAN_REMAINING=$((CAN_STARTED + CAN_LIMIT - $(date +%s)))
test "$CAN_REMAINING" -gt 0
CAN_IMAGE_ID=$(cat "$CAN_PROJECT/bootstrap/environment/image-id.txt")
CAN_NAME="a-maslov-ai-collusion-prepare-${CAN_CODE_SHA:0:7}"
if docker container inspect "$CAN_NAME" >/dev/null 2>&1; then
  printf 'Container already exists: %s\n' "$CAN_NAME" >&2
  exit 2
fi
docker run -d --name "$CAN_NAME" --label owner=a.maslov \
  --label "adapter_commit=$CAN_CODE_SHA" --user "$(id -u):$(id -g)" --restart=no \
  --cpus 8 --memory 64g --pids-limit 1024 --shm-size 8g \
  --security-opt=no-new-privileges --cap-drop=ALL \
  --env NVIDIA_VISIBLE_DEVICES=void --env CUDA_VISIBLE_DEVICES= \
  --env HOME=/work/home --env HF_HOME=/cache/hf --env XDG_CACHE_HOME=/cache/xdg \
  --env UV_CACHE_DIR=/cache/uv --env PYTHONDONTWRITEBYTECODE=1 \
  --env MEMORYARENA_ROOT=/upstream --env PYTHONPATH=/code/src:/upstream \
  --mount "type=bind,src=$CAN_CODE,dst=/code,readonly" \
  --mount "type=bind,src=$CAN_PROJECT/inputs/MemoryArena,dst=/upstream,readonly" \
  --mount "type=bind,src=$CAN_MODEL,dst=/model" \
  --mount "type=bind,src=$CAN_PROJECT/cache,dst=/cache" \
  --mount "type=bind,src=$CAN_WORK,dst=/work" \
  --mount "type=bind,src=$CAN_CONTROL,dst=/control,readonly" \
  --entrypoint /usr/bin/timeout "$CAN_IMAGE_ID" \
  --signal=TERM --kill-after=30s "${CAN_REMAINING}s" /bin/bash -c \
  'set -e; test ! -e /control/STOP; python -m unittest discover -s /code/tests -v > /work/unittest.log 2>&1; test ! -e /control/STOP; exec python /code/scripts/download-model.py --config /code/configs/dev-package.draft.json --destination /model --identity /work/model-identity.json > /work/download.log 2>&1'
