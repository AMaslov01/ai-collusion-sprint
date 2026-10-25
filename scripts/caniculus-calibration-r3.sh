#!/usr/bin/env bash
# Explicit CPU preparation or separately dispatched GPU phase; no budget reset.
set -euo pipefail
CAN_SHA=${1:?Full committed code SHA}
CAN_PHASE=${2:?calibration-prefixes-r3 or calibration-handoff-r3}
CAN_MODE=${3:?cpu or gpu}
[[ "$CAN_SHA" =~ ^[0-9a-f]{40}$ ]]
case "$CAN_PHASE" in calibration-prefixes-r3|calibration-handoff-r3) ;; *) exit 2;; esac
case "$CAN_MODE" in cpu|gpu) ;; *) exit 2;; esac
test "$(hostname)" = caniculus.appliedai.local
test "$(id -un)" = a.maslov
test "$(id -u)" -ne 0
umask 077
CAN_PROJECT=/mnt/ssd/a.maslov/ai-collusion-sprint
CAN_GLOBAL="$CAN_PROJECT/runs/readiness-cycle-v5-20261024"
CAN_CODE="$CAN_PROJECT/code/$CAN_SHA"
CAN_CONFIG="$CAN_CODE/configs/calibration-r3.json"
CAN_PROFILE="$CAN_CODE/configs/readiness-cycle-v5.json"
CAN_RUN=$(jq -r .run_id "$CAN_CONFIG")
test "$CAN_RUN" = calibration-r3-20261025
CAN_CHILD="$CAN_GLOBAL/$CAN_RUN"
CAN_REVIEW="$CAN_PROJECT/inputs/calibration-r3-review-$CAN_SHA-$CAN_PHASE"
CAN_CLOSURE="$CAN_REVIEW/closed-parent-receipt-05729ef.json"
CAN_PREFLIGHT="$CAN_CHILD/cpu-$CAN_PHASE-$CAN_SHA"
CAN_MODEL="$CAN_PROJECT/inputs/Qwen3.8-27B-FP8/017b9c7af6b5689d5dd426a76e0bc077eb5ca20a"
CAN_VENDOR="$CAN_PROJECT/dependencies/travel-tools-d0bc2174eea3b3737f6922b89897db4f21ea6f835f7354ee702edbbc9880b3bc"
CAN_TASKS="$CAN_PROJECT/bootstrap/preparation-33d29710c408fd9851e9de0db5e436cc623b595c/task-data"
CAN_IDENTITIES="$CAN_PROJECT/bootstrap/preparation-31f9c028bd8615c4b7648144c278ed0d6ffbe56c"
CAN_INDEPENDENT="$CAN_PROJECT/inputs/calibration-r3-independent-v2"
CAN_UPSTREAM=/opt/ai-collusion/sources/MemoryArena
for CAN_PATH in "$CAN_PROJECT" "$CAN_GLOBAL" "$CAN_CODE" "$CAN_REVIEW" "$CAN_MODEL" "$CAN_VENDOR" "$CAN_TASKS" "$CAN_IDENTITIES" "$CAN_INDEPENDENT" "$CAN_PROJECT/cache" "$CAN_PROJECT/inputs/MemoryArena" "$CAN_PROJECT/inputs/travel-database"; do
 test "$(realpath "$CAN_PATH")" = "$CAN_PATH"
 test "$(stat -c %u "$CAN_PATH")" = "$(id -u)"
done
test "$(sha256sum "$CAN_CLOSURE" | cut -d ' ' -f1)" = "$(jq -r .parent_closure.sha256 "$CAN_CONFIG")"
test "$(sha256sum "$CAN_PROFILE" | cut -d ' ' -f1)" = "$(jq -r .global_profile.sha256 "$CAN_CONFIG")"
test "$(sha256sum "$CAN_GLOBAL/master-manifest.json" | cut -d ' ' -f1)" = "$(jq -r .master_manifest_sha256 "$CAN_CLOSURE")"
test ! -e "$CAN_PROJECT/bootstrap/control/STOP"
for CAN_ACTIVE in $(docker ps --filter label=owner=a.maslov --format '{{.Names}}'); do
 case "$CAN_ACTIVE" in a-maslov-ai-collusion*) exit 1;; esac
done
test "$(nproc)" -ge 8
CAN_AVAILABLE=$(awk '$1=="MemAvailable:" {print $2}' /proc/meminfo)
test "$CAN_AVAILABLE" -ge 67108864
exec 9>"$CAN_GLOBAL/launch.lock"
flock -n 9
for CAN_ACTIVE in $(docker ps --filter label=owner=a.maslov --format '{{.Names}}'); do
 case "$CAN_ACTIVE" in a-maslov-ai-collusion*) exit 1;; esac
done
CAN_BASE_CONFIG="$CAN_CODE/$(jq -r .base_runtime_config "$CAN_PROFILE")"
CAN_EXPECTED_DIGEST=$(jq -r .runtime.image_digest "$CAN_BASE_CONFIG")
CAN_IMAGE_ID=$(cat "$CAN_PROJECT/bootstrap/environment/image-id.txt")
docker image inspect "$CAN_IMAGE_ID" --format '{{json .RepoDigests}}' | jq -e --arg digest "$CAN_EXPECTED_DIGEST" 'any(.[];endswith("@"+$digest))' >/dev/null
mkdir -p "$CAN_CHILD"
test "$(realpath "$CAN_CHILD")" = "$CAN_CHILD"
test "$(stat -c %u "$CAN_CHILD")" = "$(id -u)"
CAN_END=$(date -u -d "$(jq -r .limits.absolute_cutoff_utc "$CAN_PROFILE")" +%s)
CAN_MOUNTS=()
CAN_NETWORK=none
CAN_ROOT_SUFFIX=,readonly
CAN_WORK="$CAN_PREFLIGHT"
CAN_NAME="a-maslov-ai-collusion-r3-cpu-$CAN_PHASE-${CAN_SHA:0:7}"
if [ "$CAN_MODE" = gpu ]; then
 CAN_DISPATCH="$CAN_REVIEW/dispatch-$CAN_PHASE.json"
 test "$(jq -r .status "$CAN_PREFLIGHT/preflight.json")" = passed_no_model_calls
 test "$(jq -r .status "$CAN_DISPATCH")" = authorized_bounded_DEV_calibration_phase
 test "$(jq -r .code_commit "$CAN_DISPATCH")" = "$CAN_SHA"
 test "$(sha256sum "$CAN_CONFIG" | cut -d ' ' -f1)" = "$(jq -r .config_sha256 "$CAN_DISPATCH")"
 test "$(sha256sum "$CAN_PREFLIGHT/preflight.json" | cut -d ' ' -f1)" = "$(jq -r .preflight_sha256 "$CAN_DISPATCH")"
 test "$(jq -r .human_message_id "$CAN_DISPATCH")" = "$(jq -r .authority.human_message_id "$CAN_CONFIG")"
 test "$(jq -r .resource_authority_message_id "$CAN_DISPATCH")" = "$(jq -r .runtime_hardware_override.resource_authority.human_message_id "$CAN_CONFIG")"
 CAN_UUID=$(jq -r .runtime_hardware_override.operations.gpu_uuid "$CAN_CONFIG")
 test "$(nvidia-smi --id="$CAN_UUID" --query-gpu=name --format=csv,noheader)" = "$(jq -r .runtime_hardware_override.operations.gpu_name "$CAN_CONFIG")"
 test -z "$(nvidia-smi --id="$CAN_UUID" --query-compute-apps=pid --format=csv,noheader)"
 test -z "$(ss -H -lnt 'sport = :18083')"
 mkdir -p "$CAN_CHILD/resource-inspects"
 for CAN_INTENT in "$CAN_CHILD"/launch-intent-*.json; do
  test -f "$CAN_INTENT" || continue
  test "$(jq -r .run_id "$CAN_INTENT")" = "$CAN_RUN"
  CAN_KNOWN=$(jq -r .container_name "$CAN_INTENT")
  if ! grep -Fxq "$CAN_KNOWN" "$CAN_GLOBAL/gpu-phase-names.txt"; then printf '%s\n' "$CAN_KNOWN" >> "$CAN_GLOBAL/gpu-phase-names.txt"; fi
 done
 while read -r CAN_KNOWN; do
  if ! grep -Fxq "$CAN_KNOWN" "$CAN_GLOBAL/gpu-phase-names.txt"; then printf '%s\n' "$CAN_KNOWN" >> "$CAN_GLOBAL/gpu-phase-names.txt"; fi
 done < <(docker ps -a --filter label=owner=a.maslov --filter "label=parent_budget_run=readiness-cycle-v5-20261024" --filter label=computation=gpu --format '{{.Names}}')
 CAN_USED=0
 while read -r CAN_KNOWN; do
  CAN_INSPECT="$CAN_CHILD/resource-inspects/$CAN_KNOWN.json"
  docker inspect "$CAN_KNOWN" > "$CAN_INSPECT"
  test "$(jq -r '.[0].Config.Labels.owner' "$CAN_INSPECT")" = a.maslov
  jq -e '.[0].Config.Labels | .scope=="readiness-cycle-v5" or .parent_budget_run=="readiness-cycle-v5-20261024"' "$CAN_INSPECT" >/dev/null
  test "$(jq -r '.[0].State.Running' "$CAN_INSPECT")" = false
  if [ "$(jq -r '.[0].State.Status' "$CAN_INSPECT")" = created ]; then
   test "$(jq -r '.[0].State.StartedAt' "$CAN_INSPECT")" = 0001-01-01T00:00:00Z
   continue
  fi
  CAN_NS=$(($(date -d "$(jq -r '.[0].State.FinishedAt' "$CAN_INSPECT")" +%s%N)-$(date -d "$(jq -r '.[0].State.StartedAt' "$CAN_INSPECT")" +%s%N)))
  test "$CAN_NS" -ge 0
  CAN_USED=$((CAN_USED+(CAN_NS+999999999)/1000000000))
 done < "$CAN_GLOBAL/gpu-phase-names.txt"
 CAN_BASELINE=$(jq -r .before.allocated_gpu_container_seconds "$CAN_CLOSURE")
 CAN_CHILD_USED=$((CAN_USED-CAN_BASELINE))
 test "$CAN_CHILD_USED" -ge 0
 CAN_GLOBAL_LEFT=$(($(jq -r .limits.gpu_service_wall_seconds "$CAN_PROFILE")-CAN_USED))
 CAN_CHILD_LEFT=$(($(jq -r .child_limits.gpu_container_seconds "$CAN_CONFIG")-CAN_CHILD_USED))
 CAN_LEFT=$CAN_GLOBAL_LEFT
 if [ "$CAN_CHILD_LEFT" -lt "$CAN_LEFT" ]; then CAN_LEFT=$CAN_CHILD_LEFT; fi
 CAN_LEFT=$((CAN_LEFT-30))
 CAN_SERVICE_END=$(($(date +%s)+CAN_LEFT))
 CAN_END=$((CAN_END-30))
 if [ "$CAN_SERVICE_END" -lt "$CAN_END" ]; then CAN_END=$CAN_SERVICE_END; fi
 printf '%s\n' "$CAN_END" > "$CAN_GLOBAL/gpu-service-deadline.unix"
 jq -n --argjson global "$CAN_USED" --argjson child "$CAN_CHILD_USED" --argjson end "$CAN_END" \
  '{previous_global_allocated_seconds:$global,previous_child_allocated_seconds:$child,phase_deadline_unix:$end,child_cap_seconds:5400,global_cap_seconds:14400,shutdown_reserve_seconds:30}' > "$CAN_CHILD/admission-$CAN_PHASE.json"
 CAN_NETWORK=host
 CAN_ROOT_SUFFIX=
 CAN_WORK="$CAN_CHILD/$CAN_PHASE"
 CAN_NAME="a-maslov-ai-collusion-r3-$CAN_PHASE-${CAN_SHA:0:7}"
 CAN_MOUNTS+=(--gpus "device=$CAN_UUID" --mount "type=bind,src=$CAN_PREFLIGHT,dst=/preflight,readonly")
 # Protect old source/result/master paths through every /cycle alias.
 for CAN_PATH in "$CAN_GLOBAL"/*; do
  CAN_BASE=$(basename "$CAN_PATH")
  case "$CAN_BASE" in ledger|"$CAN_RUN"|global-budget.json|gpu-service-deadline.unix) continue;; esac
  CAN_MOUNTS+=(--mount "type=bind,src=$CAN_PATH,dst=/cycle/$CAN_BASE,readonly")
 done
 for CAN_PATH in "$CAN_CHILD"/*; do
  CAN_BASE=$(basename "$CAN_PATH")
  case "$CAN_BASE" in accounting|"$CAN_PHASE") continue;; esac
  CAN_MOUNTS+=(--mount "type=bind,src=$CAN_PATH,dst=/cycle/$CAN_RUN/$CAN_BASE,readonly")
 done
else
 for CAN_SCRIPT in observer grader; do
  CAN_SCRIPT_FILE=$(jq -r ".independent_cpu_scripts.$CAN_SCRIPT.file" "$CAN_CONFIG")
  test "$(basename "$CAN_SCRIPT_FILE")" = "$CAN_SCRIPT_FILE"
  test "$(sha256sum "$CAN_INDEPENDENT/$CAN_SCRIPT_FILE" | cut -d ' ' -f1)" = "$(jq -r ".independent_cpu_scripts.$CAN_SCRIPT.sha256" "$CAN_CONFIG")"
 done
 test "$(sha256sum "$CAN_INDEPENDENT/next-version-reference-interface-fixtures.json" | cut -d ' ' -f1)" = "$(jq -r .interface_locks.receipt_fixture_sha256 "$CAN_CONFIG")"
 test "$(sha256sum "$CAN_INDEPENDENT/calibration-r3-namespace-fixtures.json" | cut -d ' ' -f1)" = "$(jq -r .interface_locks.namespace_fixture_sha256 "$CAN_CONFIG")"
 test "$(sha256sum "$CAN_INDEPENDENT/calibration-r3-proposal-version-visibility-fixtures.json" | cut -d ' ' -f1)" = "$(jq -r .interface_locks.proposal_visibility_fixture_sha256 "$CAN_CONFIG")"
 CAN_CPU_END=$(($(date +%s)+900))
 if [ "$CAN_CPU_END" -lt "$CAN_END" ]; then CAN_END=$CAN_CPU_END; fi
 CAN_MOUNTS+=(--env NVIDIA_VISIBLE_DEVICES=void --env CUDA_VISIBLE_DEVICES=
  --mount "type=bind,src=$CAN_INDEPENDENT,dst=/independent-reference,readonly"
  --mount "type=bind,src=$CAN_PROJECT/runs/repair-cycle-v3-20261024,dst=/previous,readonly"
  --mount "type=bind,src=$CAN_PROJECT/runs/memory-group1-cp2-format-v2,dst=/old,readonly")
fi
CAN_SECONDS=$((CAN_END-$(date +%s)))
test "$CAN_SECONDS" -gt 0
test ! -e "$CAN_WORK"
mkdir -p "$CAN_WORK/home" "$CAN_WORK/tmp"
sha256sum "$0" > "$CAN_WORK/launcher.sha256"
{ date -u; free -h; cat /proc/loadavg; df -hT "$CAN_PROJECT"; } > "$CAN_WORK/host-before.txt"
CAN_COMMAND='exec python3 -m collusion_handoff.calibration_cycle > /work/supervisor.log 2>&1'
if [ "$CAN_MODE" = cpu ]; then
 CAN_COMMAND='set -e; umask 077; python3 -m unittest discover -s /code/tests -v > /work/unittest.log 2>&1; python3 /code/scripts/probe-structured-output.py > /work/xgrammar.log 2>&1; python3 /code/scripts/probe-calibration-reference-interface.py --references /independent-reference/next-version-reference-interface-fixtures.json --namespace /independent-reference/calibration-r3-namespace-fixtures.json --visibility /independent-reference/calibration-r3-proposal-version-visibility-fixtures.json --output /work/reference-observations.json > /work/reference-observations.log 2>&1; python3 -B /independent-reference/probe_actual_reference_context_r3b.py --references /independent-reference/next-version-reference-interface-fixtures.json --namespace /independent-reference/calibration-r3-namespace-fixtures.json --visibility /independent-reference/calibration-r3-proposal-version-visibility-fixtures.json --output /work/independent-context-observations.json > /work/context.log 2>&1; python3 -B /independent-reference/compare_reference_observations_r3b.py --references /independent-reference/next-version-reference-interface-fixtures.json --namespace /independent-reference/calibration-r3-namespace-fixtures.json --visibility /independent-reference/calibration-r3-proposal-version-visibility-fixtures.json --factory-observations /work/reference-observations.json --context-observations /work/independent-context-observations.json --output /work/independent-reference-comparison.json > /work/comparison.log 2>&1; exec python3 -m collusion_handoff.calibration_cycle --preflight > /work/preflight.log 2>&1'
else
 CAN_INTENT="$CAN_CHILD/launch-intent-$CAN_PHASE.json"
 test ! -e "$CAN_INTENT"
 jq -n --arg name "$CAN_NAME" --arg run "$CAN_RUN" --arg code "$CAN_SHA" '{schema:"calibration_GPU_launch_intent_v1",container_name:$name,run_id:$run,code_commit:$code}' > "$CAN_INTENT"
 sync -d "$CAN_INTENT"
 CAN_MOUNTS+=(--mount "type=bind,src=$CAN_INTENT,dst=/cycle/$CAN_RUN/$(basename "$CAN_INTENT"),readonly")
fi
CAN_CONTAINER_ID=$(docker create --name "$CAN_NAME" --label owner=a.maslov --label scope=calibration-r3 --label "computation=$CAN_MODE" --label "parent_budget_run=readiness-cycle-v5-20261024" --label "adapter_commit=$CAN_SHA" \
 --user "$(id -u):$(id -g)" --restart=no --network="$CAN_NETWORK" --cpus 8 --memory 64g --pids-limit 1024 --shm-size 8g --cap-drop=ALL --security-opt=no-new-privileges \
 --env HOME=/work/home --env TMPDIR=/work/tmp --env PYTHONDONTWRITEBYTECODE=1 --env "ADAPTER_COMMIT=$CAN_SHA" --env "CYCLE_PHASE=$CAN_PHASE" --env "PACKAGE_DEADLINE_UNIX=$CAN_END" \
 --env HF_HOME=/cache/hf --env HF_HUB_OFFLINE=1 --env TRANSFORMERS_OFFLINE=1 --env XDG_CACHE_HOME=/cache/xdg \
 --env TORCH_HOME=/cache/torch --env TORCHINDUCTOR_CACHE_DIR=/cache/torchinductor --env TRITON_CACHE_DIR=/cache/triton --env TORCH_EXTENSIONS_DIR=/cache/torch-extensions \
 --env VLLM_CACHE_ROOT=/cache/vllm --env CUDA_CACHE_PATH=/cache/cuda --env NUMBA_CACHE_DIR=/cache/numba \
 --env "PYTHONPATH=/vendor:/code/src:$CAN_UPSTREAM" --env MEMORYARENA_ROOT="$CAN_UPSTREAM" \
 --mount "type=bind,src=$CAN_CODE,dst=/code,readonly" --mount "type=bind,src=$CAN_MODEL,dst=/model,readonly" --mount "type=bind,src=$CAN_VENDOR,dst=/vendor,readonly" \
 --mount "type=bind,src=$CAN_IDENTITIES,dst=/identities,readonly" --mount "type=bind,src=$CAN_REVIEW,dst=/review,readonly" --mount "type=bind,src=$CAN_TASKS,dst=/tasks,readonly" \
 --mount "type=bind,src=$CAN_PROJECT/inputs/MemoryArena,dst=$CAN_UPSTREAM,readonly" --mount "type=bind,src=$CAN_PROJECT/inputs/travel-database,dst=/database,readonly" \
 --mount "type=bind,src=$CAN_GLOBAL,dst=/cycle$CAN_ROOT_SUFFIX" --mount "type=bind,src=$CAN_WORK,dst=/work" --mount "type=bind,src=$CAN_PROJECT/cache,dst=/cache" \
 --mount "type=bind,src=$CAN_PROJECT/bootstrap/control,dst=/control,readonly" "${CAN_MOUNTS[@]}" --workdir "$CAN_UPSTREAM" \
 --entrypoint /usr/bin/timeout "$CAN_IMAGE_ID" --signal=TERM --kill-after=30s "${CAN_SECONDS}s" /bin/bash -c "$CAN_COMMAND")
if [ "$CAN_MODE" = gpu ]; then
 printf '%s\n' "$CAN_NAME" >> "$CAN_GLOBAL/gpu-phase-names.txt"
 printf '%s\n' "$CAN_CONTAINER_ID" > "$CAN_WORK/created-container-id.txt"
 sync -d "$CAN_GLOBAL/gpu-phase-names.txt" "$CAN_WORK/created-container-id.txt"
fi
test "$(date +%s)" -lt "$CAN_END"
docker start "$CAN_CONTAINER_ID"
docker inspect "$CAN_NAME" > "$CAN_WORK/docker-inspect.json"
