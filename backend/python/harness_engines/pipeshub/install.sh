#!/bin/sh
# Installs `ph` and the static part of the /pipeshub contract (VERSION) into an engine image. Every engine's
# Dockerfile and the thin overlay (deployment/sandbox-images/pipeshub/) run this one script, so the layout is written
# once. DESTDIR installs under another root (the unit tests check the layout that way).
#
# What each turn writes (the tool index, the per-tool schemas, the gateway and the pinned skills) is not here: the
# controller seeds /pipeshub/session/, a read-only mount, and the links below point into it.
set -eu

SRC=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${DESTDIR:-}
FOLDER="$ROOT/pipeshub"

# Each folder on its own: mkdir -m applies only to the last one, and the sandbox user must enter all of them.
for dir in "$FOLDER" "$FOLDER/session" "$FOLDER/bin" "$ROOT/usr/local/bin"; do
    mkdir -p "$dir"
    chmod 0755 "$dir"
done
cp "$SRC/ph.py" "$ROOT/usr/local/bin/ph"
chmod 0555 "$ROOT/usr/local/bin/ph"
cp "$SRC/AGENTS.md" "$SRC/VERSION" "$FOLDER/"
chmod 0444 "$FOLDER/AGENTS.md" "$FOLDER/VERSION"

ln -s AGENTS.md "$FOLDER/README.md"
ln -s /usr/local/bin/ph "$FOLDER/bin/ph"
ln -s session/tools "$FOLDER/tools"
ln -s session/tools.txt "$FOLDER/tools.txt"
ln -s session/knowledge "$FOLDER/knowledge"
ln -s /skills "$FOLDER/skills"
ln -s /workspace "$FOLDER/workspace"
ln -s /workspace/in "$FOLDER/inputs"
ln -s /workspace/out "$FOLDER/outputs"
