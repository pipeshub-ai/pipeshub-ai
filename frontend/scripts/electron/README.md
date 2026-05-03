# Electron build scripts

Scripts used by `npm run build:electron`, `electron:prepare`, and `electron:build:*` in the parent `package.json`.

| File | Role |
|------|------|
| `next-build-electron.mjs` | Runs `next build` with `ELECTRON_STATIC=1` (static export to `out/`) |
| `electron-prepare.mjs` | Copies `out/` → `electron/out/`, generates PNG icon for packager |
| `build-electron-mac.sh` | Optional standalone macOS DMG build (uses `npm run build`; adjust if you prefer `build:electron`) |
| `build-electron-win.cmd` | Optional standalone Windows NSIS build |

General-purpose scripts (deploy, etc.) stay in `scripts/` at the package root.
