#!/bin/bash
set -eu
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export AI_USAGE_ROOT="$project_dir"
if quickshell ipc -p "$project_dir/ui" call analytics showWindow >/dev/null 2>&1; then
  exit 0
fi
exec quickshell -d -n -p "$project_dir/ui"
