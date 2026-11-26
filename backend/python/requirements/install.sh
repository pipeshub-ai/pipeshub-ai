#!/bin/sh
# Installs PipesHub's Python dependencies from the hashed locks beside this script (SEC-SC-07). pip checks
# every file it downloads against a sha256 in the lock (--require-hashes), resolves nothing (--no-deps) and
# builds a source archive only with the locked build backends (--no-build-isolation), so an install cannot
# bring in a package, a version or a file the lock does not name.
#
#   install.sh build|runtime|dev|tools [--project DIR [--editable]] [-- PIP_ARGS...]
#
#   build     the build backends alone (setuptools; maturin on Linux aarch64); every other set starts with it
#   runtime   the services' dependencies; dev adds the test and lint tools; tools is the standalone CI tools
#   --project the project at DIR (backend/python) after its dependencies, --editable as an editable install
#   PIP_ARGS  passed to every pip call, e.g. -- --dry-run --report out.json
#
# PYTHON names the interpreter (default python3): CPython 3.12, the only one the locks are resolved for.
# Changing a dependency: docs/agent-harness-v2/guides/SUPPLY-CHAIN.md.
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
usage="usage: $0 build|runtime|dev|tools [--project DIR [--editable]] [-- PIP_ARGS...]"
set_name=${1:-}
case "$set_name" in
  build | runtime | dev | tools) shift ;;
  *) echo "$usage" >&2; exit 2 ;;
esac
project=''
editable=''
while [ $# -gt 0 ]; do
  case "$1" in
    --project) [ $# -ge 2 ] || { echo "$usage" >&2; exit 2; }; project=$2; shift 2 ;;
    --editable) editable=-e; shift ;;
    --) shift; break ;;
    *) echo "$usage" >&2; exit 2 ;;
  esac
done
if [ -n "$editable" ] && [ -z "$project" ]; then echo "--editable needs --project DIR" >&2; exit 2; fi

py=${PYTHON:-python3}
"$py" -c 'import sys
v = sys.version_info
if sys.implementation.name != "cpython" or (v.major, v.minor) != (3, 12):
    sys.exit("the locks are resolved for CPython 3.12; this is %s %s" % (sys.implementation.name, sys.version.split()[0]))'

pip_install() {
  "$py" -m pip install --disable-pip-version-check --no-input --no-deps "$@"
}
# torch and torchvision are locked to their CPU builds, which only the PyTorch index serves; a hash pins
# each file, so where it is served from cannot change what is installed.
indexes="--index-url https://pypi.org/simple --extra-index-url https://download.pytorch.org/whl/cpu"

# shellcheck disable=SC2086 # $indexes is two flag pairs
pip_install --require-hashes $indexes -r "$here/build.lock" "$@"
[ "$set_name" = build ] && exit 0
# shellcheck disable=SC2086
pip_install --require-hashes --no-build-isolation $indexes -r "$here/$set_name.lock" "$@"
[ "$set_name" = tools ] && exit 0
# pip cannot hash a git checkout; vcs.lock pins each to a full commit id, and its dependencies came above.
pip_install --no-build-isolation -r "$here/vcs.lock" "$@"
if [ -n "$project" ]; then
  # shellcheck disable=SC2086 # $editable is empty or -e
  pip_install --no-build-isolation $editable "$project" "$@"
fi
