#!/usr/bin/env bash
# Run on caniculus after the ordinary server resource/access checks.
set -euo pipefail
MC_MODE=${1:?test or analysis}
MC_OUTPUT=${2:?New absolute output directory on your personal SSD}
case "$MC_MODE" in test|analysis) ;; *) exit 2;; esac
test "$(hostname)" = caniculus.appliedai.local
test "$(id -un)" = a.maslov
test "$(id -u)" -ne 0
MC_CODE=$(cd "$(dirname "$0")/.." && pwd -P)
MC_IMAGE=vllm/vllm-openai@sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9
case "$MC_OUTPUT" in /mnt/ssd/a.maslov/*) ;; *) exit 2;; esac
test ! -e "$MC_OUTPUT"
MC_PARENT=$(realpath "$(dirname "$MC_OUTPUT")")
test "$(stat -c %u "$MC_PARENT")" = "$(id -u)"
case "$MC_PARENT/" in /mnt/ssd/a.maslov/*) ;; *) exit 2;; esac
case "$MC_OUTPUT/" in "$MC_CODE/"*) exit 2;; esac
umask 077
mkdir "$MC_OUTPUT"
MC_NAME="a-maslov-memcontrast-public-$MC_MODE-$(date -u +%Y%m%dT%H%M%S)"
MC_COMMAND=test-public.py
if [ "$MC_MODE" = analysis ]; then MC_COMMAND=reproduce-public-analysis.py; fi
MC_ARGS=()
if [ "$MC_MODE" = analysis ]; then MC_ARGS+=(--output /work/recomputed-analysis.json); fi
docker run --name "$MC_NAME" --label owner=a.maslov --restart=no \
 --user "$(id -u):$(id -g)" --network=none --cpus=4 --memory=16g --memory-swap=16g --pids-limit=512 \
 --read-only --cap-drop=ALL --security-opt=no-new-privileges --tmpfs /tmp:rw,nosuid,size=2g \
 --env NVIDIA_VISIBLE_DEVICES=void --env CUDA_VISIBLE_DEVICES= --env PYTHONDONTWRITEBYTECODE=1 \
 --env HOME=/tmp --env PYTHONPATH=/code/src --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1 \
 --env OMP_NUM_THREADS=1 --env OPENBLAS_NUM_THREADS=1 --env MKL_NUM_THREADS=1 \
 --mount "type=bind,src=$MC_CODE,dst=/code,readonly" --mount "type=bind,src=$MC_OUTPUT,dst=/work" \
 --workdir /code --entrypoint /usr/bin/timeout "$MC_IMAGE" --signal=TERM --kill-after=15s 900s \
 python3 -B "/code/scripts/$MC_COMMAND" "${MC_ARGS[@]}" > "$MC_OUTPUT/run.log" 2>&1
