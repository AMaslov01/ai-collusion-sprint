#!/usr/bin/env bash
# Run on the server through an already authorized SSH session. Read-only;
# does not reserve a GPU, create directories, or start scientific checks.
set -euo pipefail

test "$(hostname)" = caniculus.appliedai.local
test "$(id -un)" = a.maslov
test "$(id -u)" -ne 0

hostname
id
docker version --format '{{.Server.Version}}'
findmnt -T /mnt/ssd
df -hT /mnt/ssd
df -i /mnt/ssd

CAN_ROOT=/mnt/ssd/a.maslov
test -d "$CAN_ROOT"
test ! -L "$CAN_ROOT"
test "$(realpath -- "$CAN_ROOT")" = "$CAN_ROOT"
test "$(stat -c %u -- "$CAN_ROOT")" = "$(id -u)"
stat -c '%U %u %G %g %a %n' "$CAN_ROOT"
getconf _NPROCESSORS_ONLN
free -m

nvidia-smi --query-gpu=index,uuid,name,driver_version,memory.used,memory.free,memory.total,utilization.gpu --format=csv
nvidia-smi --query-compute-apps=pid,gpu_uuid,used_gpu_memory --format=csv,noheader
docker ps -a --filter label=owner=a.maslov --format '{{.ID}} {{.Names}} {{.Status}}'
