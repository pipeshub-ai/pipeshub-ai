import * as fs from 'fs';
import * as path from 'path';

export interface ResolvedLauncher {
  command: string;
  args: string[];
  /** Folder holding the whole bundle (pg/ py/ app/ node/ ...), exported to the launcher as PIPESHUB_BUNDLE. */
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

/**
 * Where the launcher lives. The bundle is read-only app content; nothing is
 * written next to it (data goes to the OS app-data folder).
 *
 * `PIPESHUB_LAUNCHER` (+ `PIPESHUB_LAUNCHER_ARGS`, a JSON array placed before the
 * subcommand) substitutes the launcher for tests. It is ignored in a packaged
 * app, so an environment variable can never make the shipped app run
 * something else.
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
  const bundleDir = input.isPackaged
    ? path.join(input.resourcesPath, BUNDLE_DIR_NAME)
    : path.join(input.devBundleDir, BUNDLE_DIR_NAME);
  const command = path.join(bundleDir, input.platform === 'win32' ? 'pipeshub-native.exe' : 'pipeshub-native');
  return exists(command) ? { command, args: [], bundleDir } : null;
}
