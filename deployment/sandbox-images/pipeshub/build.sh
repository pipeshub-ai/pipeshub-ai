#!/bin/sh
# Adds `ph` and the /pipeshub contract (R3, OWD-6) to the engines' run images a host already has, tagged as the
# allowlist pins them now (backend/python/harness_engines/allowlist.json `image`, e.g. pxh-opencode-tools:1.18.34-ph7).
#
#   deployment/sandbox-images/pipeshub/build.sh [KIND...]     # default: opencode codex claude_code acp-gemini acp-goose acp-qwen acp-dsh
#
# The run image tag is <repo>:<version>-ph<N>. Its base is the first of these the host has:
#   - <repo>:<version>, the tag without the suffix, which a host built with engine-build.sh before R3;
#   - the newest earlier run image <repo>:<version>-ph<M>, M < N (the Dockerfile replaces its /pipeshub and ph whole).
# The new image is one layer of a few kB over it: the engine binary and its digest label are the base's, so the broker
# vouches for it exactly as before. With no base the kind is refused with the build.sh that makes the full image
# (Claude Code's is admin-built: deployment/sandbox-images/claude-code/build.sh --binary PATH). A run image tag is
# immutable: one already on the host is kept, never built over, since other checkouts run it. The built image carries
# pipeshub.harness.owner=x-lead. Nothing is pulled or pushed; the build context is a temporary folder removed on exit.
#
# The overlay adds `ph` only: a run image is also the runtime layer the allowlist pins with it (the tools lock and
# the Node lock in opencode/tools, SKILLPACKS). A base that does not carry exactly that layer, such as a pre-R3
# image that needs scipy and Node, is refused with the build.sh that makes the run image.
#
# A fresh host builds the full images instead, `ph` included:
#   deployment/sandbox-images/opencode/build.sh
#   deployment/sandbox-images/codex/build.sh
#   deployment/sandbox-images/claude-code/build.sh --binary PATH
#   deployment/sandbox-images/acp-gemini/build.sh
#   deployment/sandbox-images/acp-goose/build.sh
#   deployment/sandbox-images/acp-qwen/build.sh
#   deployment/sandbox-images/acp-dsh/build.sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../../.." && pwd)
SRC="$REPO/backend/python/harness_engines/pipeshub"
ALLOWLIST="$REPO/backend/python/harness_engines/allowlist.json"
PH_CONTRACT=$(cat "$SRC/VERSION")
[ $# -gt 0 ] || set -- opencode codex claude_code acp-gemini acp-goose acp-qwen acp-dsh

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM
mkdir "$WORK/context" "$WORK/check"
cp "$HERE/Dockerfile" "$SRC/ph.py" "$SRC/AGENTS.md" "$SRC/VERSION" "$SRC/install.sh" "$WORK/context/"
TOOLS="$HERE/../opencode/tools"
cp "$TOOLS/runtime_note.py" "$TOOLS/requirements.lock" "$TOOLS/node/package-lock.json" "$WORK/check/"
chmod -R a+rX "$WORK/check"

status=0
SEEN=
for KIND in "$@"; do
    TAG=$(python3 - "$ALLOWLIST" "$KIND" <<'EOF'
import json, sys
print(next(e for e in json.load(open(sys.argv[1]))["engines"] if e["kind"] == sys.argv[2])["image"])
EOF
)
    DIR=$(echo "$KIND" | tr _ -)
    # acp-opencode runs opencode's image, made by opencode's build.
    [ -d "$REPO/deployment/sandbox-images/$DIR" ] || DIR=${DIR#acp-}
    FULL="deployment/sandbox-images/$DIR/build.sh"
    BASE=$(echo "$TAG" | sed 's/-ph[0-9][0-9]*$//')
    if [ "$BASE" = "$TAG" ]; then
        echo "$KIND: the allowlist's image $TAG has no -ph<N> suffix; build it with its engine's build.sh" >&2
        status=1
        continue
    fi
    case " $SEEN " in *" $TAG "*) continue ;; esac
    SEEN="$SEEN $TAG"
    if docker image inspect "$TAG" >/dev/null 2>&1; then
        echo "$KIND: $TAG is already on this host; kept (a run image tag is never built over)"
        continue
    fi
    if ! docker image inspect "$BASE" >/dev/null 2>&1; then
        M=${TAG##*-ph}
        FOUND=
        while [ "$M" -gt 1 ]; do
            M=$((M - 1))
            if docker image inspect "$BASE-ph$M" >/dev/null 2>&1; then
                FOUND="$BASE-ph$M"
                break
            fi
        done
        if [ -z "$FOUND" ]; then
            echo "$KIND: no base for $TAG on this host (neither $BASE nor an earlier $BASE-ph<M>); build it with $FULL" >&2
            status=1
            continue
        fi
        BASE=$FOUND
    fi
    if ! docker run --rm --network none --label pipeshub.harness.owner=x-lead --user 0:0 --entrypoint python3 \
        -v "$WORK/check:/check:ro" "$BASE" /check/runtime_note.py --check /check/requirements.lock /check/package-lock.json \
        >"$WORK/check.log" 2>&1; then
        echo "$KIND: base image $BASE lacks the runtime layer $TAG pins (tools/requirements.lock, tools/node); this overlay" \
            "adds only ph. Build $TAG with $FULL instead:" >&2
        sed 's/^/    /' "$WORK/check.log" | head -n 8 >&2
        status=1
        continue
    fi
    docker build --build-arg "BASE=$BASE" --build-arg "PH_CONTRACT=$PH_CONTRACT" \
        --label pipeshub.harness.owner=x-lead -t "$TAG" "$WORK/context"
    echo "built $TAG (/pipeshub contract $PH_CONTRACT over $BASE)"
done
exit $status
