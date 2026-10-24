#!/usr/bin/env bash
# Read only own process/VRAM/metrics metadata; never restart the smoke.
set -euo pipefail
CAN_CODE_SHA=${1:?Pass the full smoke source commit SHA}
CAN_STAGE=${2:-run}
[[ "$CAN_CODE_SHA" =~ ^[0-9a-f]{40}$ ]]
case "$CAN_STAGE" in run|native-run) ;; *) exit 2 ;; esac
test "$(hostname)" = caniculus.appliedai.local
test "$(id -un)" = a.maslov
umask 077
CAN_PROJECT=/mnt/ssd/a.maslov/ai-collusion-sprint
CAN_WORK="$CAN_PROJECT/bootstrap/smoke-run-$CAN_CODE_SHA"
if [ "$CAN_STAGE" = native-run ]; then CAN_WORK="$CAN_PROJECT/runs/native-dev-$CAN_CODE_SHA"; fi
CAN_NAME="a-maslov-ai-collusion-smoke-$CAN_STAGE-${CAN_CODE_SHA:0:7}"
test "$(realpath "$CAN_WORK")" = "$CAN_WORK"
test "$(stat -c %u "$CAN_WORK")" = "$(id -u)"
test "$(docker inspect "$CAN_NAME" --format '{{index .Config.Labels "owner"}}')" = a.maslov
CAN_STARTED=$(cat "$CAN_PROJECT/bootstrap/environment/compute-start.unix")
while [ "$(docker inspect "$CAN_NAME" --format '{{.State.Running}}')" = true ]; do
  test "$(date +%s)" -lt "$((CAN_STARTED+43200))" || break
  date -u +%FT%TZ >> "$CAN_WORK/vram-samples.csv"
  nvidia-smi --id=GPU-441e6008-cd27-34b5-c81a-17dad9d4a894 \
    --query-gpu=uuid,memory.used,memory.free,utilization.gpu --format=csv,noheader >> "$CAN_WORK/vram-samples.csv"
  if curl -fsS --max-time 2 http://127.0.0.1:18083/metrics > "$CAN_WORK/metrics.prom.tmp" 2>/dev/null; then
    mv "$CAN_WORK/metrics.prom.tmp" "$CAN_WORK/metrics.prom"
  fi
  sleep 5
done
docker inspect "$CAN_NAME" > "$CAN_WORK/docker-inspect-final.json"
nvidia-smi --id=GPU-441e6008-cd27-34b5-c81a-17dad9d4a894 \
  --query-gpu=uuid,memory.used,memory.free,utilization.gpu --format=csv,noheader > "$CAN_WORK/gpu-after.csv"
cat "$CAN_WORK/gpu-after.csv"
