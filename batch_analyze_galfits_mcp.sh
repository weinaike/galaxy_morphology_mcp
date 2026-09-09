#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "$0")" && pwd)"
python_bin="${PYTHON_BIN:-/home/www/ENTER/envs/galfit/bin/python}"

if [[ ! -x "$python_bin" ]]; then
  echo "Python executable is not available: $python_bin" >&2
  exit 1
fi

exec "$python_bin" "$project_root/src/tools/workflow_batch_runner.py" "$@"
