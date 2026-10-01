import { execFileSync } from 'child_process';
import fs from 'fs';
import path from 'path';

export interface BuildInfo {
  version: string | null;
  commitId: string | null;
  buildTime: string | null;
}

export type GitRunner = (args: string[]) => string | null;

const ENV_KEYS = ['APP_VERSION', 'GIT_COMMIT', 'BUILD_TIME'] as const;
const GIT_TIMEOUT_MS = 2000;

// `npm run build` writes this next to the compiled entry point (dist/).
export const BUILD_INFO_FILE = path.resolve(__dirname, '../../build-info.json');

const runGit: GitRunner = (args) => {
  try {
    const output = execFileSync('git', args, {
      cwd: __dirname,
      timeout: GIT_TIMEOUT_MS,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    });
    return output.trim() || null;
  } catch {
    return null;
  }
};

function stringOrNull(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

// The input is a file on disk or another service's response: do not trust its shape.
export function parseBuildInfo(raw: unknown): BuildInfo | null {
  if (!raw || typeof raw !== 'object') {
    return null;
  }
  const record = raw as Record<string, unknown>;
  return {
    version: stringOrNull(record.version),
    commitId: stringOrNull(record.commitId),
    buildTime: stringOrNull(record.buildTime),
  };
}

export function readBuildInfoFromGit(git: GitRunner = runGit): BuildInfo {
  const tag = git(['describe', '--tags', '--abbrev=0', '--match', 'v*']);
  return {
    version: tag ? tag.replace(/^v/, '') : null,
    commitId: git(['rev-parse', 'HEAD']),
    buildTime: null,
  };
}

function readBuildInfoFile(file: string): BuildInfo | null {
  try {
    return parseBuildInfo(JSON.parse(fs.readFileSync(file, 'utf8')));
  } catch {
    return null;
  }
}

export function resolveBuildInfo(
  env: NodeJS.ProcessEnv = process.env,
  file: string = BUILD_INFO_FILE,
  git: GitRunner = runGit,
): BuildInfo {
  // The Dockerfile always defines these, empty when no build arg was passed,
  // so a container never shells out to git.
  if (ENV_KEYS.some((key) => env[key] !== undefined)) {
    return {
      version: stringOrNull(env.APP_VERSION),
      commitId: stringOrNull(env.GIT_COMMIT),
      buildTime: stringOrNull(env.BUILD_TIME),
    };
  }
  return readBuildInfoFile(file) ?? readBuildInfoFromGit(git);
}

let cached: BuildInfo | undefined;

export function getBuildInfo(): BuildInfo {
  cached ??= resolveBuildInfo();
  return cached;
}
