#!/usr/bin/env bash
# Node for the native bundle: the official Node runtime (checked against the release's SHASUMS256), the API built and pruned
# to its production dependencies, and the web UI as a static export, laid out as in the image (Dockerfile).
#   build-node.sh <bundle-dir>   writes <bundle>/node/bin/node and <bundle>/app/backend/{dist,dist/public,dist/node_modules,src}
. "$(dirname "$0")/lib.sh"
out=$(abspath "$1")
case "$os-$arch" in
  linux-x86_64) plat=linux-x64 sum=$NODE_SHA256_LINUX_X64;;
  darwin-arm64) plat=darwin-arm64 sum=$NODE_SHA256_DARWIN_ARM64;;
  *) echo "build-node: no pinned Node for $os-$arch" >&2; exit 1;;
esac
rm -rf "$out/node" "$out/app/backend"; mkdir -p "$out/node/bin" "$out/app/backend/src"
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
tar xJf "$(fetch "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-$plat.tar.xz" "$sum" "node-v$NODE_VERSION-$plat.tar.xz")" -C "$work"
cp "$work/node-v$NODE_VERSION-$plat/bin/node" "$out/node/bin/node"; cp "$work/node-v$NODE_VERSION-$plat/LICENSE" "$out/node/"
export PATH="$work/node-v$NODE_VERSION-$plat/bin:$PATH"

cp -R "$repo/backend/nodejs/apps" "$work/api"; rm -rf "$work/api/node_modules" "$work/api/dist"
(cd "$work/api" && npm ci --no-audit --no-fund && npm run build && npm prune --omit=dev) >/dev/null
cp -R "$work/api/dist" "$out/app/backend/dist"
cp -R "$work/api/node_modules" "$out/app/backend/dist/node_modules"
# tsc does not emit .hbs, and the mail templates are read from <cwd>/src at run time; the OpenAPI spec too.
(cd "$work/api/src" && find . -name '*.hbs' | while read -r f; do mkdir -p "$out/app/backend/src/$(dirname "$f")"; cp "$f" "$out/app/backend/src/$f"; done)
mkdir -p "$out/app/backend/src/modules/api-docs" && cp "$work/api/src/modules/api-docs/pipeshub-openapi.yaml" "$out/app/backend/src/modules/api-docs/"

cp -R "$repo/frontend" "$work/ui"; rm -rf "$work/ui/node_modules" "$work/ui/.next" "$work/ui/out"
(cd "$work/ui" && npm config set legacy-peer-deps true --location=project && npm ci --no-audit --no-fund && ELECTRON_STATIC=1 npm run build) >/dev/null
cp -R "$work/ui/out" "$out/app/backend/dist/public"
"$out/node/bin/node" --version
du -sh "$out/node" "$out/app/backend"
