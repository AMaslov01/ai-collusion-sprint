#!/usr/bin/env bash
set -euo pipefail
CAN_CODE_SHA=${1:?Pass the full committed adapter SHA}
CAN_STAGE=${2:?Choose install or check}
[[ "$CAN_CODE_SHA" =~ ^[0-9a-f]{40}$ ]]
case "$CAN_STAGE" in install|check) ;; *) exit 2 ;; esac
test "$(hostname)" = caniculus.appliedai.local
test "$(id -un)" = a.maslov
test "$(id -u)" -ne 0
umask 077
CAN_PROJECT=/mnt/ssd/a.maslov/ai-collusion-sprint
CAN_CODE="$CAN_PROJECT/code/$CAN_CODE_SHA"
CAN_LOCK_SHA=$(sha256sum "$CAN_CODE/configs/travel-tools.lock" | cut -d ' ' -f 1)
CAN_VENDOR="$CAN_PROJECT/dependencies/travel-tools-$CAN_LOCK_SHA"
CAN_WORK="$CAN_PROJECT/bootstrap/tools-$CAN_STAGE-$CAN_CODE_SHA"
CAN_UPSTREAM=/opt/ai-collusion/sources/MemoryArena
for CAN_PATH in "$CAN_PROJECT" "$CAN_CODE" "$CAN_PROJECT/inputs/MemoryArena" "$CAN_PROJECT/inputs/travel-database"; do
  test "$(realpath -- "$CAN_PATH")" = "$CAN_PATH"
  test "$(stat -c %u -- "$CAN_PATH")" = "$(id -u)"
done
test "$(jq -r '.runtime.travel_tool_dependencies.status' "$CAN_CODE/configs/dev-package.draft.json")" = confirmed
for CAN_PATH in "$CAN_WORK/home" "$CAN_VENDOR"; do
  mkdir -p -- "$CAN_PATH"
  test "$(realpath -- "$CAN_PATH")" = "$CAN_PATH"
  test "$(stat -c %u -- "$CAN_PATH")" = "$(id -u)"
done
test ! -e "$CAN_PROJECT/bootstrap/control/STOP"
CAN_STARTED=$(cat "$CAN_PROJECT/bootstrap/environment/compute-start.unix")
CAN_LIMIT=$(jq -r '.operations.wall_time_seconds' "$CAN_CODE/configs/dev-package.draft.json")
CAN_REMAINING=$((CAN_STARTED + CAN_LIMIT - $(date +%s)))
test "$CAN_REMAINING" -gt 0
CAN_VENDOR_MOUNT="type=bind,src=$CAN_VENDOR,dst=/vendor"
CAN_SCRIPT=install-travel-tools.py
if [ "$CAN_STAGE" = check ]; then
  CAN_VENDOR_MOUNT="$CAN_VENDOR_MOUNT,readonly"
  CAN_SCRIPT=check-travel-tools.py
fi
docker run -d --name "a-maslov-ai-collusion-tools-$CAN_STAGE-${CAN_CODE_SHA:0:7}" \
  --label owner=a.maslov --label "adapter_commit=$CAN_CODE_SHA" \
  --user "$(id -u):$(id -g)" --restart=no \
  --cpus 8 --memory 64g --pids-limit 1024 --shm-size 8g \
  --security-opt=no-new-privileges --cap-drop=ALL \
  --env NVIDIA_VISIBLE_DEVICES=void --env CUDA_VISIBLE_DEVICES= \
  --env HOME=/work/home --env PYTHONDONTWRITEBYTECODE=1 \
  --env MEMORYARENA_ROOT="$CAN_UPSTREAM" --env "PYTHONPATH=/vendor:/code/src:$CAN_UPSTREAM" \
  --workdir "$CAN_UPSTREAM" \
  --mount "type=bind,src=$CAN_CODE,dst=/code,readonly" \
  --mount "type=bind,src=$CAN_PROJECT/inputs/MemoryArena,dst=$CAN_UPSTREAM,readonly" \
  --mount "type=bind,src=$CAN_PROJECT/inputs/travel-database,dst=/database,readonly" \
  --mount "$CAN_VENDOR_MOUNT" --mount "type=bind,src=$CAN_WORK,dst=/work" \
  --mount "type=bind,src=$CAN_PROJECT/bootstrap/control,dst=/control,readonly" \
  --entrypoint /usr/bin/timeout "$(cat "$CAN_PROJECT/bootstrap/environment/image-id.txt")" \
  --signal=TERM --kill-after=30s "${CAN_REMAINING}s" /bin/bash -c \
  'set -e; umask 077; test ! -e /control/STOP; exec python3 "/code/scripts/$1" > /work/execution.log 2>&1' \
  -- "$CAN_SCRIPT"
