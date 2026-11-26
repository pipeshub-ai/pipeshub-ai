#!/usr/bin/env bash
# Python for the native bundle: python-build-standalone (what `uv python install` fetches; uv checks its checksum) with the
# backend's and the schema tools' dependencies installed into it (no venv, so the folder can move), plus the source.
#   build-py.sh <bundle-dir>     needs uv (UV_VERSION), and network for the locked files; writes <bundle>/py and <bundle>/app/{python,db}
. "$(dirname "$0")/lib.sh"
out=$(abspath "$1")
rm -rf "$out/py" "$out/app/python" "$out/app/db"; mkdir -p "$out/app"
pyroot=$(mktemp -d); trap 'rm -rf "$pyroot"' EXIT
UV_PYTHON_INSTALL_DIR="$pyroot" uv python install "$PY_VERSION"
cp -R "$pyroot"/cpython-"$PY_VERSION"-*/ "$out/py"
py="$out/py/bin/python3"
# From the hashed locks (SEC-SC-07), as the images install: nothing resolves at build time, so a release published after
# the lock was written cannot reach the bundle. runtime.lock pins the CPU torch build and leaves out what
# [tool.uv] exclude-dependencies excludes. The standalone Python is marked externally managed; it is ours alone.
PYTHON="$py" sh "$repo/backend/python/requirements/install.sh" runtime -- --break-system-packages
"$py" -m pip install --disable-pip-version-check --no-input --no-deps --require-hashes --break-system-packages \
  -r "$repo/backend/db/requirements.lock"
"$py" -I -c 'import importlib.metadata as m, sys
bad = [n for n in sys.argv[1:] if any(True for _ in m.distributions(name=n))]
sys.exit(f"build-py: installed although backend/python/pyproject.toml excludes them: {bad}" if bad else 0)' cchardet unclecode-litellm
# What a server never imports: headers, Tk, IDLE, the standard library's own tests.
rm -rf "$out/py/include" "$out"/py/lib/python3.*/{test,idlelib,tkinter,turtledemo,ensurepip} "$out"/py/lib/{libtcl,libtk,tcl,tk}* 2>/dev/null || true
# The packages the image copies (Dockerfile): app imports the harness kernel and engine packages beside it.
mkdir -p "$out/app/python" && cp -R "$repo/backend/python/app" "$repo/backend/python/harness_core" \
  "$repo/backend/python/harness_engines" "$out/app/python/"
cp -R "$repo/backend/db" "$out/app/db"
find "$out/app" -type d \( -name __pycache__ -o -name tests \) -prune -exec rm -rf {} +
# Bytecode now: the launcher sets PYTHONDONTWRITEBYTECODE, so a read-only (signed) bundle is never written at run time.
"$py" -m compileall -q -j0 "$out/app" "$out/py/lib" >/dev/null || true
"$py" -I -c "import psycopg, torch, docling, onnxruntime, fastapi; print('py ok', torch.__version__)"
du -sh "$out/py" "$out/app/python" "$out/app/db"
