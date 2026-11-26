#!/usr/bin/env bash
# Regenerate pydantic + zod models from contracts/harness/**/*.schema.json, and the
# token vector (vectors/token-patterns.json) as Python and frontend modules.
# Needs the backend/python dev deps (datamodel-code-generator==0.83.0) and `npm ci` in
# backend/nodejs/apps (json-schema-to-zod 2.8.1). PYTHON overrides the interpreter.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python="${PYTHON:-}"
if [[ -z "$python" ]]; then
  for candidate in "$here/../../backend/python/.venv/bin/python" "$here/../../backend/python/venv/bin/python"; do
    if [[ -x "$candidate" ]]; then python="$candidate"; break; fi
  done
fi
exec "${python:-python3}" "$here/codegen/generate.py" "$@"
