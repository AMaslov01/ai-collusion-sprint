#!/usr/bin/env bash
# File operations only. Scientific checks run in non-root Docker separately.
set -euo pipefail
test "$(hostname)" = caniculus.appliedai.local
test "$(id -un)" = a.maslov
test "$(id -u)" -ne 0
umask 077

CAN_ROOT=/mnt/ssd/a.maslov
test ! -L "$CAN_ROOT"
test "$(realpath -- "$CAN_ROOT")" = "$CAN_ROOT"
test "$(stat -c %u -- "$CAN_ROOT")" = "$(id -u)"
CAN_PROJECT="$CAN_ROOT/ai-collusion-sprint"
if [ ! -e "$CAN_PROJECT" ]; then mkdir -- "$CAN_PROJECT"; fi
test ! -L "$CAN_PROJECT"
test "$(realpath -- "$CAN_PROJECT")" = "$CAN_PROJECT"
test "$(stat -c %u -- "$CAN_PROJECT")" = "$(id -u)"
test "$(stat -c %a -- "$CAN_PROJECT")" = 700
for CAN_DIR in code inputs cache bootstrap bootstrap/environment bootstrap/logs; do
  mkdir -p -- "$CAN_PROJECT/$CAN_DIR"
  test "$(realpath -- "$CAN_PROJECT/$CAN_DIR")" = "$CAN_PROJECT/$CAN_DIR"
  test "$(stat -c %u -- "$CAN_PROJECT/$CAN_DIR")" = "$(id -u)"
done

CAN_UPSTREAM="$CAN_PROJECT/inputs/MemoryArena"
CAN_UPSTREAM_SHA=6cd9de14b71915e39ac742a20dc33785e14b6aab
if [ ! -e "$CAN_UPSTREAM" ]; then
  git init --quiet "$CAN_UPSTREAM"
  git -C "$CAN_UPSTREAM" remote add origin https://github.com/ZexueHe/MemoryArena.git
  git -C "$CAN_UPSTREAM" fetch --depth=1 origin "$CAN_UPSTREAM_SHA"
  git -C "$CAN_UPSTREAM" checkout --quiet --detach FETCH_HEAD
fi
test "$(realpath -- "$CAN_UPSTREAM")" = "$CAN_UPSTREAM"
test "$(stat -c %u -- "$CAN_UPSTREAM")" = "$(id -u)"
test "$(git -C "$CAN_UPSTREAM" rev-parse HEAD)" = "$CAN_UPSTREAM_SHA"
test -z "$(git -C "$CAN_UPSTREAM" status --porcelain)"

CAN_IMAGE=vllm/vllm-openai@sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9
CAN_IMAGE_ID=$(docker image inspect "$CAN_IMAGE" --format '{{.Id}}')
CAN_OWN_IMAGE=a-maslov-ai-collusion-vllm:0.32.0
if docker image inspect "$CAN_OWN_IMAGE" >/dev/null 2>&1; then
  test "$(docker image inspect "$CAN_OWN_IMAGE" --format '{{.Id}}')" = "$CAN_IMAGE_ID"
else
  docker tag "$CAN_IMAGE" "$CAN_OWN_IMAGE"
fi
docker image inspect "$CAN_IMAGE" --format '{{json .RepoDigests}}' > "$CAN_PROJECT/bootstrap/environment/image-repodigests.json"
printf '%s\n' "$CAN_IMAGE_ID" > "$CAN_PROJECT/bootstrap/environment/image-id.txt"
printf '%s\n' "$CAN_PROJECT"
