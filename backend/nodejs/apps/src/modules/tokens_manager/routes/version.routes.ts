import { Router, Request, Response } from 'express';
import fs from 'fs';
import path from 'path';

export interface BackendVersionInfo {
  /** Node API release version (package.json `version`). */
  version: string;
  /** Compatible @pipeshub-ai/mcp SDK version (package.json `sdkVersion`). */
  sdkVersion: string;
}

interface PackageManifest {
  version?: string;
  sdkVersion?: string;
  dependencies?: Record<string, string>;
}

let cachedVersionInfo: BackendVersionInfo | null = null;

function resolvePackageJsonPath(): string {
  const fromCwd = path.join(process.cwd(), 'package.json');
  if (fs.existsSync(fromCwd)) {
    return fromCwd;
  }
  return path.resolve(__dirname, '../../../../package.json');
}

function readSdkVersionFromDependencies(
  dependencies: Record<string, string> | undefined,
): string | undefined {
  const raw = dependencies?.['@pipeshub-ai/mcp'];
  if (!raw) {
    return undefined;
  }
  return raw.replace(/^[\^~>=<]*/, '');
}

export function loadBackendVersionInfo(): BackendVersionInfo {
  if (cachedVersionInfo) {
    return cachedVersionInfo;
  }

  const packagePath = resolvePackageJsonPath();
  const manifest = JSON.parse(
    fs.readFileSync(packagePath, 'utf8'),
  ) as PackageManifest;

  const version = manifest.version ?? 'unknown';
  const sdkVersion =
    manifest.sdkVersion ??
    readSdkVersionFromDependencies(manifest.dependencies) ??
    'unknown';

  cachedVersionInfo = { version, sdkVersion };
  return cachedVersionInfo;
}

/** Clears the in-memory cache (for tests). */
export function resetBackendVersionInfoCache(): void {
  cachedVersionInfo = null;
}

export function createVersionRouter(): Router {
  const router = Router();

  router.get('/', (_req: Request, res: Response) => {
    res.json(loadBackendVersionInfo());
  });

  return router;
}
