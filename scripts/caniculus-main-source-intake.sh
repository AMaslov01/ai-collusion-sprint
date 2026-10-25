#!/usr/bin/env bash
# Administrative source indexing inside the existing main worker, never a model call.
set -euo pipefail
CAN_SHA=${1:?Frozen inference SHA}
CAN_REVIEW=${2:?External reviewed packet directory}
CAN_MANIFEST_SHA=${3:?Canonical main manifest SHA}
[[ "$CAN_SHA" =~ ^[0-9a-f]{40}$ && "$CAN_MANIFEST_SHA" =~ ^[0-9a-f]{64}$ ]]
test "$(hostname)" = caniculus.appliedai.local
test "$(id -un)" = a.maslov
test "$(id -u)" -ne 0
umask 077
CAN_PROJECT=/mnt/ssd/a.maslov/ai-collusion-sprint
CAN_MANIFEST="$CAN_REVIEW/main-manifest.json"
CAN_RUN=$(jq -r .run_id "$CAN_MANIFEST")
[[ "$CAN_RUN" =~ ^[a-z0-9][a-z0-9-]{1,63}$ ]]
CAN_ROOT=$(jq -r .artifacts.run_root "$CAN_MANIFEST")
test "$CAN_ROOT" = "$CAN_PROJECT/runs/$CAN_RUN"
test "$(jq -r .pipeline.code_commit "$CAN_MANIFEST")" = "$CAN_SHA"
for CAN_PATH in "$CAN_PROJECT" "$CAN_REVIEW" "$CAN_ROOT"; do
 test "$(realpath "$CAN_PATH")" = "$CAN_PATH"
 test "$(stat -c %u "$CAN_PATH")" = "$(id -u)"
done
test ! -e "$CAN_PROJECT/bootstrap/control/STOP"
CAN_INDEXER_SHA=22f0ac6f969b81f2cbfad741066350ca0f9c61189532749b278e9fd67947964a
test "$(sha256sum "$CAN_REVIEW/neutral_source_index_main_shared_worker.py" | cut -d ' ' -f1)" = "$CAN_INDEXER_SHA"
CAN_CONTAINER="a-maslov-ai-collusion-main-$CAN_RUN-prefixes-${CAN_SHA:0:7}"
CAN_INSPECTION=$(docker inspect "$CAN_CONTAINER")
printf '%s' "$CAN_INSPECTION" | jq -e --arg sha "$CAN_SHA" --arg run "$CAN_RUN" --arg user "$(id -u):$(id -g)" \
 '.[0] | .State.Running == true and .Config.User == $user and .Config.Labels.owner == "a.maslov" and
 .Config.Labels.main_run == $run and .Config.Labels.adapter_commit == $sha and
 .HostConfig.NanoCpus == 8000000000 and .HostConfig.Memory == 68719476736 and .HostConfig.Privileged == false' >/dev/null
CAN_OUTPUT="$CAN_ROOT/source-review-index"
mkdir -p "$CAN_OUTPUT"
test "$(realpath "$CAN_OUTPUT")" = "$CAN_OUTPUT"
test "$(stat -c %u "$CAN_OUTPUT")" = "$(id -u)"
exec 8>"$CAN_OUTPUT/intake.lock"
flock -n 8
CAN_BATCH="$(date -u +%Y%m%dT%H%M%SZ)-$$"
while read -r CAN_GROUP; do
 [[ "$CAN_GROUP" =~ ^[0-9]+$ ]]
 CAN_SOURCE="$CAN_ROOT/prefixes/prefix/group-$CAN_GROUP"
 test -f "$CAN_SOURCE/status.json" || continue
 test "$(jq -r .status "$CAN_SOURCE/status.json")" = prefix_collected || continue
 test ! -e "$CAN_OUTPUT/group-$CAN_GROUP.json" || continue
 test ! -e "$CAN_PROJECT/bootstrap/control/STOP"
 CAN_LOG="$CAN_OUTPUT/group-$CAN_GROUP.$CAN_BATCH.log"
 test ! -e "$CAN_LOG"
 docker exec --user "$(id -u):$(id -g)" --env CUDA_VISIBLE_DEVICES= --env PYTHONDONTWRITEBYTECODE=1 \
  "$CAN_CONTAINER" /bin/bash -c 'umask 077; exec /usr/bin/timeout --signal=TERM --kill-after=5s 60s python3 -B "$@"' _ \
  /review/neutral_source_index_main_shared_worker.py --execution-context shared_gpu_worker_cpu_diagnostic \
  --source "/cycle/prefixes/prefix/group-$CAN_GROUP/source.json" \
  --prefix-status "/cycle/prefixes/prefix/group-$CAN_GROUP/status.json" \
  --manifest /review/main-manifest.json --manifest-sha256 "$CAN_MANIFEST_SHA" \
  --output "/cycle/source-review-index/group-$CAN_GROUP.json" > "$CAN_LOG" 2>&1
 cat "$CAN_LOG"
done < <(jq -r '.sample.group_ids[]' "$CAN_MANIFEST")
