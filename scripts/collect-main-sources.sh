#!/usr/bin/env bash
# Local bounded collector: export only completed, indexed sources; never restart inference.
set -euo pipefail
CAN_SHA=${1:?Frozen inference SHA}
CAN_REVIEW=${2:?Remote external packet directory}
CAN_MANIFEST=${3:?Local frozen manifest}
CAN_MANIFEST_SHA=${4:?Canonical manifest SHA}
CAN_OUTPUT=${5:?Absolute local source-only destination}
CAN_SOCKET=${6:?Existing SSH control socket}
CAN_CYCLES=${7:?Finite number of 60-second collection cycles}
[[ "$CAN_SHA" =~ ^[0-9a-f]{40}$ && "$CAN_MANIFEST_SHA" =~ ^[0-9a-f]{64}$ ]]
[[ "$CAN_SOCKET" =~ ^/tmp/[a-zA-Z0-9._/-]+$ && "$CAN_CYCLES" =~ ^[1-9][0-9]*$ ]]
test "$CAN_CYCLES" -le 720
[[ "$CAN_OUTPUT" = /* && "$CAN_REVIEW" =~ ^/mnt/ssd/a.maslov/ai-collusion-sprint/inputs/[a-z0-9-]+$ ]]
test "$(jq -r .pipeline.code_commit "$CAN_MANIFEST")" = "$CAN_SHA"
CAN_RUN=$(jq -r .run_id "$CAN_MANIFEST")
CAN_ROOT=$(jq -r .artifacts.run_root "$CAN_MANIFEST")
CAN_TOTAL=$(jq -r .sample.assigned_group_count "$CAN_MANIFEST")
[[ "$CAN_RUN" =~ ^[a-z0-9][a-z0-9-]{1,63}$ ]]
test "$CAN_ROOT" = "/mnt/ssd/a.maslov/ai-collusion-sprint/runs/$CAN_RUN"
CAN_TARGET=a.maslov@10.16.90.19
CAN_RSH="ssh -S $CAN_SOCKET -o BatchMode=yes"
CAN_SSH=(ssh -S "$CAN_SOCKET" -o BatchMode=yes "$CAN_TARGET")
CAN_CONTAINER="a-maslov-ai-collusion-main-$CAN_RUN-prefixes-${CAN_SHA:0:7}"
CAN_INTAKE=/mnt/ssd/a.maslov/ai-collusion-sprint/inputs/main-source-ops-ea3d426/caniculus-main-source-intake.sh
umask 077
mkdir -p "$CAN_OUTPUT/source-review-index"
CAN_LIST=$(mktemp "$CAN_OUTPUT/.source-files.XXXXXX")
trap 'rm -f -- "$CAN_LIST"' EXIT
CAN_PREVIOUS=0
for ((CAN_CYCLE=1;CAN_CYCLE<=CAN_CYCLES;CAN_CYCLE++)); do
 CAN_RUNNING=$("${CAN_SSH[@]}" "docker inspect '$CAN_CONTAINER' --format '{{.State.Running}}'")
 if [ "$CAN_RUNNING" = true ]; then
  "${CAN_SSH[@]}" "bash '$CAN_INTAKE' '$CAN_SHA' '$CAN_REVIEW' '$CAN_MANIFEST_SHA'"
 fi
 rsync -an --ignore-existing --include='group-*.json' --exclude='*' --bwlimit=5000 -e "$CAN_RSH" \
  "$CAN_TARGET:$CAN_ROOT/source-review-index/" "$CAN_OUTPUT/source-review-index/"
 rsync -a --ignore-existing --include='group-*.json' --exclude='*' --bwlimit=5000 -e "$CAN_RSH" \
  "$CAN_TARGET:$CAN_ROOT/source-review-index/" "$CAN_OUTPUT/source-review-index/"
 : > "$CAN_LIST"
 while IFS= read -r CAN_INDEX; do
  CAN_NAME=$(basename "$CAN_INDEX" .json)
  CAN_GROUP=${CAN_NAME#group-}
  [[ "$CAN_GROUP" =~ ^[0-9]+$ ]]
  printf 'prefixes/prefix/group-%s/source.json\n' "$CAN_GROUP" >> "$CAN_LIST"
 done < <(rg --files "$CAN_OUTPUT/source-review-index" -g 'group-*.json')
 rsync -an --ignore-existing --files-from="$CAN_LIST" --bwlimit=5000 -e "$CAN_RSH" "$CAN_TARGET:$CAN_ROOT/" "$CAN_OUTPUT/"
 rsync -a --ignore-existing --files-from="$CAN_LIST" --bwlimit=5000 -e "$CAN_RSH" "$CAN_TARGET:$CAN_ROOT/" "$CAN_OUTPUT/"
 CAN_COUNT=0
 while IFS= read -r CAN_INDEX; do
  CAN_NAME=$(basename "$CAN_INDEX" .json)
  CAN_GROUP=${CAN_NAME#group-}
  CAN_HASH=$(shasum -a 256 "$CAN_OUTPUT/prefixes/prefix/group-$CAN_GROUP/source.json")
  test "${CAN_HASH%% *}" = "$(jq -r .source_file_sha256 "$CAN_INDEX")"
  CAN_COUNT=$((CAN_COUNT+1))
 done < <(rg --files "$CAN_OUTPUT/source-review-index" -g 'group-*.json')
 if [ "$CAN_COUNT" -ne "$CAN_PREVIOUS" ]; then
  printf 'source_only_exported=%s assigned=%s\n' "$CAN_COUNT" "$CAN_TOTAL"
  CAN_PREVIOUS=$CAN_COUNT
 fi
 if [ "$CAN_COUNT" = "$CAN_TOTAL" ]; then exit 0; fi
 if [ "$CAN_RUNNING" != true ]; then
  printf 'prefix_worker_stopped; remaining source indexes require the original isolated CPU intake\n'
  exit 0
 fi
 sleep 60
done
printf 'bounded_source_collection_window_ended; model worker was not modified\n'
