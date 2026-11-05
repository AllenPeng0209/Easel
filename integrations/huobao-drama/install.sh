#!/usr/bin/env bash
set -euo pipefail
INTEGRATION_ROOT="$(cd "$(dirname "$0")" && pwd)"
TASK_USER_CONFIG="$(mktemp)"
TASK_GLOBAL_CONFIG="$(mktemp)"
trap 'rm -f "$TASK_USER_CONFIG" "$TASK_GLOBAL_CONFIG"' EXIT
export NPM_CONFIG_USERCONFIG="$TASK_USER_CONFIG"
export NPM_CONFIG_GLOBALCONFIG="$TASK_GLOBAL_CONFIG"
npm --prefix "$INTEGRATION_ROOT/source/backend" ci --registry=https://registry.npmjs.org --no-audit --no-fund
npm --prefix "$INTEGRATION_ROOT/source/frontend" ci --registry=https://registry.npmjs.org --no-audit --no-fund
npm --prefix "$INTEGRATION_ROOT/source/frontend" run generate
node "$INTEGRATION_ROOT/configure-runtime.cjs"
