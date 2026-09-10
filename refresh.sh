#!/bin/bash
set -eu
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
force=()
for arg in "$@"; do
  if [[ "$arg" == --force ]]; then force=(--force); fi
done
# Native quota records are maintained by Omarchy; Go is collected below.
if command -v omarchy-agent-usage-update >/dev/null 2>&1; then
  timeout 60 omarchy-agent-usage-update --limits-only "${force[@]}" --except grok --except cursor --except fireworks || true
fi
exec python3 "$project_dir/collector.py" scan "${force[@]}"
