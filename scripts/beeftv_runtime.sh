#!/usr/bin/env bash
# Select writable state from EASEL_ROOT, even when scripts is a workspace symlink.
set -euo pipefail
repository_root="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve().parents[1])' "${BASH_SOURCE[0]}")"
python="$repository_root/.venv/bin/python"
if [[ ! -x "$python" ]]; then python="$(command -v python3)"; fi
exec "$python" "$repository_root/easel/beeftv_runtime.py" --repository "$repository_root" "$@"
