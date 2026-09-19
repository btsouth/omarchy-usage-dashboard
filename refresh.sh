#!/bin/bash
set -eu
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
force=()
for arg in "$@"; do
  if [[ "$arg" == --force ]]; then force=(--force); fi
done
# Native quota records are maintained by Omarchy; Go is collected below.
# A machine with its own refresh orchestrator (one that routes custom
# collectors and keeps each record written by exactly one writer) takes
# precedence; the packaged updater covers everyone else.
if command -v omarchy-agent-usage-refresh >/dev/null 2>&1; then
  timeout 60 omarchy-agent-usage-refresh --limits-only "${force[@]}" --except grok --except cursor --except fireworks || true
elif command -v omarchy-agent-usage-update >/dev/null 2>&1; then
  timeout 60 omarchy-agent-usage-update --limits-only "${force[@]}" --except grok --except cursor --except fireworks || true
fi
status=0
python3 "$project_dir/collector.py" scan "${force[@]}" || status=$?
# Reset notifications ride every refresh path, timer and panel alike.
if command -v omarchy-usage-dashboard-notify-resets >/dev/null 2>&1; then
  omarchy-usage-dashboard-notify-resets || true
fi
exit "$status"
