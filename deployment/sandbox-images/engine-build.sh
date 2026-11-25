#!/bin/sh
# Builds the image of one engine pinned in backend/python/harness_engines/allowlist.json.
#
#   deployment/sandbox-images/engine-build.sh KIND [--version VERSION] [--variant NAME] [--asset PATH | --binary PATH] [--tag TAG]
#   deployment/sandbox-images/engine-build.sh KIND --candidate VERSION --digest sha256:HEX [--variant NAME] \
#       (--asset PATH | --binary PATH)
#
# An engine may have several allow-listed versions (C14.01): --version picks the entry, and without it the
# kind's first entry (its default version) is built.
#
# KIND names the allowlist entry and the image directory beside this script (KIND with `_` as `-`: its
# Dockerfile and licenses/). The release asset is downloaded (or taken from --asset), verified against the
# allowlist's asset sha256, unpacked, and the binary verified against the allowlist's binaryDigest before it
# enters the build context as `engine-binary`; the Dockerfile verifies it once more inside the image. The
# static connect relay (P5.1-f) comes from opencode/relay. Nothing else is fetched from the release. The
# build context lives in a temporary directory that is removed on exit.
#
# An asset marked `tree` (Gemini CLI's release bundle: a Node entry point and the chunks it imports) is unpacked
# whole (zip or tar.gz, refusing absolute or `..` paths and links) into the context as `engine-tree/`; its
# `member` is the entry point verified against binaryDigest.
#
# An asset that also names a `lock` (DeepSeek Harness: a Node program over some 650 npm packages) has its
# dependency tree pinned by that package-lock.json, a path under the image directory whose sha256 is the asset's
# `lockSha256`: the lock and its package.json enter the context as `engine-lock/`, and the Dockerfile installs
# exactly that tree (npm ci checks every package's integrity, no install scripts) and verifies the entry point
# against binaryDigest once more.
#
# An entry whose `supply` is `admin` (a proprietary engine PipesHub may not redistribute, D29) has no
# release asset: the admin passes their own binary with --binary, it is verified against binaryDigest, and
# nothing is downloaded. --binary is refused for any other entry, --asset for an admin entry.
#
# An entry with `variants` (E0b) builds one of them: the Dockerfile target and VARIANT build arg named
# NAME, tagged with the variant's image; the default is the variant the entry's `image` names. The
# image directory's tools/ (if any) joins the context. An entry without variants builds as before.
# Every build context also holds pipeshub/ (R3): `ph` and the static /pipeshub files the tools target installs.
#
# A candidate build (CC1) tests a version the allowlist does not pin yet: the binary is verified against the
# digest given on the command line instead of the allowlist's, nothing is downloaded (a release engine's asset
# is a local --asset), and the image is tagged `<repo of the run image or variant>:VERSION-candidate` with the
# label io.pipeshub.engine.candidate=true; --tag is refused. Nothing runs that tag in production: the allowlist
# refuses a candidate tag, the broker allows only allowlisted images and the registry only allowlisted digests.
# Admitting the version is a reviewed allowlist change after its gate, then a normal build.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../.." && pwd)
ALLOWLIST="$REPO/backend/python/harness_engines/allowlist.json"
[ $# -ge 1 ] || { echo "usage: $0 KIND [--asset PATH] [--tag TAG]" >&2; exit 2; }
KIND="$1"
shift
ASSET=""
BINARY=""
TAG=""
VARIANT=""
PICK=""
CANDIDATE=""
CANDIDATE_DIGEST=""
usage() {
    echo "usage: $0 KIND [--version VERSION] [--variant NAME] [--asset PATH | --binary PATH] [--tag TAG]" >&2
    echo "       $0 KIND --candidate VERSION --digest sha256:HEX [--variant NAME] (--asset PATH | --binary PATH)" >&2
    exit 2
}
while [ $# -gt 0 ]; do
    [ $# -ge 2 ] || usage
    case "$1" in
        --asset) ASSET="$2"; shift 2 ;;
        --candidate) CANDIDATE="$2"; shift 2 ;;
        --digest) CANDIDATE_DIGEST="$2"; shift 2 ;;
        --binary) BINARY="$2"; shift 2 ;;
        --tag) TAG="$2"; shift 2 ;;
        --variant) VARIANT="$2"; shift 2 ;;
        --version) PICK="$2"; shift 2 ;;
        *) usage ;;
    esac
done
DIR="$HERE/$(echo "$KIND" | tr _ -)"
[ -f "$DIR/Dockerfile" ] || { echo "no image directory for $KIND" >&2; exit 2; }

# An allowlist field of the entry; a missing one prints the second argument when given, else fails.
field() {
    python3 - "$ALLOWLIST" "$KIND" "$PICK" "$@" <<'EOF'
import json, sys
entry = next((e for e in json.load(open(sys.argv[1]))["engines"] if e["kind"] == sys.argv[2] and sys.argv[3] in ("", e["version"])), None)
if entry is None:
    sys.exit(f"{sys.argv[2]}@{sys.argv[3]} is not in the allowlist")
del sys.argv[3]
value = entry
for key in sys.argv[3].split("."):
    if key not in value and len(sys.argv) > 4:
        value = sys.argv[4]
        break
    value = value[key]
print(value)
EOF
}

# The entry's variants as `name tag` lines (none: an engine with one image).
variants() {
    python3 - "$ALLOWLIST" "$KIND" "$PICK" <<'EOF'
import json, sys
entry = next((e for e in json.load(open(sys.argv[1]))["engines"] if e["kind"] == sys.argv[2] and sys.argv[3] in ("", e["version"])), None)
if entry is None:
    sys.exit(f"{sys.argv[2]}@{sys.argv[3]} is not in the allowlist")
for name, tag in (entry.get("variants") or {}).items():
    if not name.startswith("_"):
        print(name, tag)
EOF
}

VERSION=$(field version)
BINARY_DIGEST=$(field binaryDigest)
SUPPLY=$(field supply release)
RUN_IMAGE=$(field image)
if [ -n "$CANDIDATE$CANDIDATE_DIGEST" ]; then
    [ -n "$CANDIDATE" ] && [ -n "$CANDIDATE_DIGEST" ] || { echo "--candidate and --digest go together" >&2; usage; }
    echo "$CANDIDATE_DIGEST" | grep -Eqx 'sha256:[0-9a-f]{64}' || { echo "--digest must be sha256:<64 hex>" >&2; usage; }
    echo "$CANDIDATE" | grep -Eqx '[A-Za-z0-9_][A-Za-z0-9_.-]{0,100}' || { echo "--candidate must be a tag-safe version" >&2; usage; }
    [ -z "$TAG" ] || { echo "--tag is refused for a candidate: its tag is always VERSION-candidate" >&2; usage; }
    [ "$SUPPLY" = admin ] || [ -n "$ASSET" ] || { echo "a candidate of $KIND needs a local --asset (nothing is downloaded)" >&2; usage; }
    VERSION=$CANDIDATE
    BINARY_DIGEST=$CANDIDATE_DIGEST
fi
# The repository part of an image reference (a registry port is not a tag).
repo_of() { case "${1##*:}" in */*) echo "$1" ;; *) echo "${1%:*}" ;; esac; }
candidate_tag() { echo "$(repo_of "$1"):$CANDIDATE-candidate"; }
if [ "$SUPPLY" = admin ]; then
    [ -n "$BINARY" ] || { echo "$KIND is supplied by the admin: pass --binary PATH (nothing is downloaded)" >&2; exit 2; }
    [ -z "$ASSET" ] || usage
else
    [ -z "$BINARY" ] || { echo "--binary is for admin-supplied engines only" >&2; exit 2; }
    ASSET_SHA256=$(field asset.sha256)
    ASSET_URL=$(field asset.url)
    MEMBER=$(field asset.member)
    TREE=$(field asset.tree false)
    LOCK=$(field asset.lock "")
    [ -z "$LOCK" ] || LOCK_SHA256=$(field asset.lockSha256)
fi
VARIANTS=$(variants)
TARGET_ARGS=""
if [ -n "$VARIANTS" ]; then
    if [ -z "$VARIANT" ]; then
        VARIANT=$(echo "$VARIANTS" | awk -v image="$RUN_IMAGE" '$2 == image {print $1}')
    fi
    VARIANT_TAG=$(echo "$VARIANTS" | awk -v name="$VARIANT" '$1 == name {print $2}')
    [ -n "$VARIANT_TAG" ] || usage
    [ -z "$CANDIDATE" ] || VARIANT_TAG=$(candidate_tag "$VARIANT_TAG")
    [ -n "$TAG" ] || TAG=$VARIANT_TAG
    TARGET_ARGS="--target $VARIANT --build-arg VARIANT=$VARIANT"
else
    [ -z "$VARIANT" ] || usage
    [ -z "$CANDIDATE" ] || RUN_IMAGE=$(candidate_tag "$RUN_IMAGE")
    [ -n "$TAG" ] || TAG=$RUN_IMAGE
fi
CANDIDATE_ARGS=""
[ -z "$CANDIDATE" ] || CANDIDATE_ARGS="--label io.pipeshub.engine.candidate=true"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM
mkdir "$WORK/context"
BINARY_SHA256=${BINARY_DIGEST#sha256:}
if [ "$SUPPLY" = admin ]; then
    echo "$BINARY_SHA256  $BINARY" | sha256sum -c -
    cp "$BINARY" "$WORK/context/engine-binary"
else
    if [ -z "$ASSET" ]; then
        ASSET="$WORK/asset.tar.gz"
        curl -fsSL -o "$ASSET" "$ASSET_URL"
    fi
    if [ -n "$CANDIDATE" ]; then
        echo "candidate asset sha256 (for its allowlist entry): $(sha256sum "$ASSET" | cut -d' ' -f1)"
    else
        echo "$ASSET_SHA256  $ASSET" | sha256sum -c -
    fi
    if [ "$TREE" = True ]; then
        python3 -I - "$ASSET" "$WORK/context/engine-tree" <<'UNPACK'
import pathlib, sys, tarfile, zipfile
source, target = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
target.mkdir()
def safe(name):
    path = pathlib.PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        sys.exit(f"unsafe path in the asset: {name}")
if zipfile.is_zipfile(source):
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            safe(info.filename)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                sys.exit(f"link in the asset: {info.filename}")
        archive.extractall(target)
else:
    with tarfile.open(source, "r:gz") as archive:
        for member in archive.getmembers():
            safe(member.name)
            if not (member.isfile() or member.isdir()):
                sys.exit(f"not a file or folder in the asset: {member.name}")
        archive.extractall(target, filter="data")
UNPACK
        echo "$BINARY_SHA256  $WORK/context/engine-tree/$MEMBER" | sha256sum -c -
    else
        tar -xzf "$ASSET" -C "$WORK" "$MEMBER"
        echo "$BINARY_SHA256  $WORK/$MEMBER" | sha256sum -c -
        mv "$WORK/$MEMBER" "$WORK/context/engine-binary"
    fi
fi
if [ "$SUPPLY" != admin ] && [ -n "$LOCK" ]; then
    case "$LOCK" in /*|*..*) echo "asset.lock must be a path under $DIR" >&2; exit 2 ;; esac
    echo "$LOCK_SHA256  $DIR/$LOCK" | sha256sum -c -
    mkdir "$WORK/context/engine-lock"
    cp "$DIR/$LOCK" "$(dirname "$DIR/$LOCK")/package.json" "$WORK/context/engine-lock/"
fi
cp "$DIR/Dockerfile" "$WORK/context/"
cp -R "$HERE/opencode/relay" "$DIR/licenses" "$WORK/context/"
# An image's own configuration files (Gemini CLI's system settings and policy), when it has any.
[ ! -d "$DIR/etc" ] || cp -R "$DIR/etc" "$WORK/context/"
# R3 / OWD-6: `ph`, the static /pipeshub files and the script that installs them, from the package the worker
# writes the session folder with.
mkdir "$WORK/context/pipeshub"
for name in ph.py AGENTS.md VERSION install.sh; do
    cp "$REPO/backend/python/harness_engines/pipeshub/$name" "$WORK/context/pipeshub/"
done
# -L: an engine may share another engine's tools/ through a symlink (codex/tools is opencode's).
[ ! -d "$DIR/tools" ] || cp -RL "$DIR/tools" "$WORK/context/"

# shellcheck disable=SC2086 # TARGET_ARGS and CANDIDATE_ARGS are empty or flag pairs without spaces in their values
docker build $TARGET_ARGS $CANDIDATE_ARGS \
    --build-arg "ENGINE_VERSION=$VERSION" \
    --build-arg "ENGINE_SHA256=$BINARY_SHA256" \
    -t "$TAG" "$WORK/context"
echo "built $TAG ($KIND $VERSION${VARIANT:+ $VARIANT}, binary $BINARY_DIGEST)${CANDIDATE:+: a candidate, not admitted}"
