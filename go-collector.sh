#!/bin/bash
set -eu
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
flags=()
for arg in "$@"; do
  [[ "$arg" != --force ]] || flags+=(--force)
done
python3 "$project_dir/collector.py" go "${flags[@]}" >/dev/null
cat "${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/agents/usage/opencode-go.json"
