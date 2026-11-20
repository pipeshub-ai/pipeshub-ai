import * as fs from 'fs';
import * as path from 'path';

export interface ResolvedLauncher {
  command: string;
  args: string[];
  /** Folder holding the whole bundle (launcher/ node/ pg/ py/ app/), exported to the launcher as PIPESHUB_BUNDLE. */
  bundleDir: string | null;
}

export interface ResolveLauncherInput {
  isPackaged: boolean;
  resourcesPath: string;
  platform: NodeJS.Platform;
  env: NodeJS.ProcessEnv;
  /** Unpackaged runs look for a bundle next to the frontend package. */
  devBundleDir: string;
  exists?: (p: string) => boolean;
}

/** Folder name under the app's resources, set by `extraResources` in electron-builder.yml. */
export const BUNDLE_DIR_NAME = 'pipeshub-native';

/** The launcher inside the bundle: TypeScript run by the bundle's own Node (24.12 or later runs it without a build step). */
export const LAUNCHER_MAIN = ['launcher', 'src', 'main.ts'];
export const BUNDLE_NODE = ['node', 'bin', 'node'];

/**
 * Where the launcher lives. The bundle is read-only app content; nothing is
 * written next to it (data goes to the OS app-data folder).
 *
 * `PIPESHUB_LAUNCHER` (+ `PIPESHUB_LAUNCHER_ARGS`, a JSON array placed before the
 * subcommand) substitutes the launcher for tests. It is ignored in a packaged
 * app, so an environment variable can never make the shipped app run
 * something else. There is no Windows bundle: the app offers server mode only there.
 */
export function resolveLauncher(input: ResolveLauncherInput): ResolvedLauncher | null {
  const exists = input.exists ?? fs.existsSync;
  if (!input.isPackaged && input.env.PIPESHUB_LAUNCHER) {
    let args: string[] = [];
    try {
      const parsed = JSON.parse(input.env.PIPESHUB_LAUNCHER_ARGS || '[]');
      if (Array.isArray(parsed)) args = parsed.map(String);
    } catch {
      return null;
    }
    return { command: input.env.PIPESHUB_LAUNCHER, args, bundleDir: null };
  }
  if (input.platform === 'win32') return null;
  const bundleDir = input.isPackaged
    ? path.join(input.resourcesPath, BUNDLE_DIR_NAME)
    : path.join(input.devBundleDir, BUNDLE_DIR_NAME);
  const command = path.join(bundleDir, ...BUNDLE_NODE);
  const main = path.join(bundleDir, ...LAUNCHER_MAIN);
  return exists(command) && exists(main) ? { command, args: [main], bundleDir } : null;
}
