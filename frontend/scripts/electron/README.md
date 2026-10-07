# Electron build scripts

Used by `npm run build:electron`, `electron:prepare`, and `electron:build:*` in the parent `package.json`.

| File | Role |
|------|------|
| `next-build-electron.mjs` | Runs `next build` with `ELECTRON_STATIC=1` (static export to `out/`) |
| `electron-prepare.mjs` | Compiles Electron TS, copies `out/` → `electron/out/`, generates **256 / 512 / 1024** PNG logos from `public/logo/pipes-hub.svg` |
| **`build-electron.mjs`** | **Single cross-platform driver:** `build:electron` → `electron:prepare` → `electron-builder`. Subcommands: `mac`, `win`, `mac-win`. Sets `CSC_IDENTITY_AUTO_DISCOVERY=false` by default. On macOS, if the DMG step fails after a successful `.app` pack, **retries DMG-only** with `--prepackaged`. |
| `build-electron-mac.sh` | Thin wrapper: `node build-electron.mjs mac` (for CI or direct bash). |
| `build-electron-win.cmd` | Thin wrapper: `node build-electron.mjs win` (for Windows CMD). |
| `retry-mac-dmg.mjs` | Manual DMG-only rebuild; run via `npm run electron:build:mac:dmg-retry`. |

## npm scripts

| Script | Produces |
|--------|-----------|
| `electron:build:mac` | macOS `.dmg` (+ `.blockmap`) under `frontend/dist-electron/` |
| `electron:build:win` | Windows **NSIS installer `.exe`** under `frontend/dist-electron/` |
| `electron:build:mac-win` | Both targets in one `electron-builder` run (same as `electron:build:all`) |
| `electron:build:all` | Alias of `mac-win` |
| `electron:build:mac:dmg-retry` | DMG only, from an existing packaged `.app` under `dist-electron/mac-*` |

## Artifacts (`frontend/dist-electron/`)

| Platform | Typical outputs |
|----------|-----------------|
| macOS | `PipesHub-<version>-mac.dmg`, `.zip` / `.blockmap` (per `electron-builder.yml`) |
| Windows | `PipesHub-<version>-win.exe` (NSIS installer) |
| Linux | `.AppImage`, `.deb`, **`.rpm`** (rpm needs `rpm` on the build host—use Fedora CI or Docker when building on Debian/Ubuntu) |

## Desktop app behavior (testers)

- **Server URL & login:** The server URL is stored in `localStorage` and persists across restarts. **Workspace logout** clears that URL and the confirmation flag, then sends you back to the add-server-URL screen (not after a normal 401/session-expire redirect to `/login`).
- **External links:** `http(s)` links from the app open in the **system default browser** (not an in-app popup window).
- **Social sign-in (Google / Microsoft / GitHub):** runs in the **system default browser**, because no provider accepts the packaged app's `app://` origin as a redirect URI. The provider redirects to `<your server URL>/auth/<provider>/callback` — the same URI the web app already uses, so **no Google Cloud or Azure app registration has to change** — and that page hands the result back over a `pipeshub://` deep link. The browser will ask once for permission to open PipesHub; if the automatic hand-off does not fire, the page shows an **Open PipesHub** button that always works. Two caveats: the scheme is registered on **first launch after install**, so deep links do not work from a never-run build; and **GitHub** additionally requires the server URL typed into the app to match the backend's `FRONTEND_PUBLIC_URL` exactly, because the backend derives GitHub's `redirect_uri` from that value. Deep links on **AppImage** need desktop integration (`appimaged` or a manually installed `.desktop` file); the `.deb` and the mac/Windows installers register the scheme themselves.
- **Voice input:** Prefer **server-side STT** (configure under AI models / `GET /api/v1/chat/speech/capabilities`). Web Speech API is unreliable under the packaged `app://` origin; the desktop build requests **microphone** permission on macOS when chat uses `getUserMedia` / `MediaRecorder`.

## Signing

For Apple / Windows code signing in production, install certificates and run with `CSC_IDENTITY_AUTO_DISCOVERY=true` (and the usual `CSC_LINK`, `WIN_CSC_LINK`, etc.) as required by [electron-builder code signing](https://www.electron.build/code-signing).

## Host notes

- **macOS DMG** (`hdiutil`) must run on macOS (or a macOS CI runner).
- **Windows NSIS** builds are most reliable on **Windows**. Building `--win` from macOS can work with Wine or CI-specific tooling; building `--mac` from Windows is not supported.
- **Linux `.rpm`:** `electron-builder` invokes `rpm` to build the package. On Ubuntu/Debian hosts without `rpm`, install it or run the Linux build in a **Fedora** container/CI job.

### DMG: `hdiutil detach` exit 16 / stuck volume

If a build stops mid-DMG, **`/Volumes/PipesHub`** can stay mounted; the next run fails detaching that volume. The build script tries to run `hdiutil detach -force` first. If it still fails, close any Finder window on that volume, eject it in Disk Utility, or run:

```bash
hdiutil detach /Volumes/PipesHub -force
```

General-purpose deploy scripts live in `scripts/` at the package root.
