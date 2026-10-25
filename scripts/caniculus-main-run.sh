#!/usr/bin/env bash
# Main GPU entry: exact CPU receipt plus verified human authority and frozen review.
set -euo pipefail
test "$#" = 3
CAN_SHA=$1
CAN_REVIEW=$2
CAN_PHASE=$3
case "$CAN_PHASE" in prefixes|continuations) ;; *) exit 2;; esac
exec bash "$(dirname "$0")/caniculus-main-cpu.sh" "$CAN_SHA" "$CAN_REVIEW" "$CAN_PHASE" gpu
