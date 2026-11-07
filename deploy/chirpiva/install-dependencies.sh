#!/usr/bin/env bash
# Run as the chirpiva service user on the Linux target; this never starts the app.
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
if [[ "$(uname -s)" != Linux || "$TASK_ROOT" != /srv/chirpiva/app || "$(id -u)" == 0 ]]; then
  echo 'Run as the unprivileged chirpiva user from the real /srv/chirpiva/app directory on Linux.' >&2
  exit 1
fi
if systemctl is-active --quiet chirpiva.service; then
  echo 'Stop chirpiva.service before rebuilding its dependencies.' >&2
  exit 1
fi
cd "$TASK_ROOT"
python3.12 -c 'import sys; assert sys.version_info[:2] == (3, 12)'
node -e 'const [major,minor]=process.versions.node.split(".").map(Number); if (!(major===24 && minor>=16)) {throw Error("This deployment uses Node 24.x >=24.16; install that LTS line first")}'
command -v ffmpeg >/dev/null
command -v ffprobe >/dev/null
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install --prefer-binary -e .
npm --prefix web/frontend ci --no-audit --no-fund
npm --prefix web/frontend run build
bash integrations/huobao-drama/install.sh
.venv/bin/python -m playwright install chromium
.venv/bin/python -m patchright install chromium
test -s web/frontend/dist/index.html
test -s integrations/huobao-drama/source/frontend/.output/public/index.html
.venv/bin/python -m pip check
echo 'Dependencies built. Install browser OS libraries, configure the chosen Agent and private endpoints, then follow README verification before starting production.'
