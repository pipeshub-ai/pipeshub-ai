import * as fs from 'fs';
import * as path from 'path';
import type { ConnectorMeta } from './persistence/journal';

const STORAGE_PATH_PREFIX = 'storage://';

export interface OpenLocalFsRecordSourcePayload {
  connectorId?: string | null;
  localFsRelativePath?: string | null;
  absolutePath?: string | null;
}

type ResolveRecordSourcePathFailureCode =
  | 'MISSING_ROOT'
  | 'MISSING_PATH'
  | 'INVALID_ABSOLUTE_PATH'
  | 'PATH_OUTSIDE_ROOT';

export type ResolveRecordSourcePathResult =
  | {
      ok: true;
      path: string;
      source: 'relative' | 'absolute';
    }
  | {
      ok: false;
      code: ResolveRecordSourcePathFailureCode;
      error: string;
    };

export type OpenLocalFsRecordSourceResult =
  | {
      ok: true;
      path: string;
      action: 'show-item' | 'open-directory';
    }
  | {
      ok: false;
      code:
        | ResolveRecordSourcePathFailureCode
        | 'MISSING_CONNECTOR'
        | 'NOT_FOUND'
        | 'OPEN_FAILED';
      error: string;
    };

interface ResolveDeps {
  existsSync?: (targetPath: string) => boolean;
  realpathSync?: (targetPath: string) => string;
}

export interface OpenLocalFsRecordSourceDeps extends ResolveDeps {
  getMeta: (connectorId: string) => ConnectorMeta | null;
  showItemInFolder: (targetPath: string) => void;
  openPath: (targetPath: string) => Promise<string>;
  statSync?: (targetPath: string) => fs.Stats;
}

function realpathIfExists(targetPath: string, deps?: ResolveDeps): string {
  const existsSync = deps?.existsSync ?? fs.existsSync;
  const realpathSync = deps?.realpathSync ?? fs.realpathSync.native;
  if (!existsSync(targetPath)) return targetPath;
  return realpathSync(targetPath);
}

function normalizeForCompare(targetPath: string): string {
  const resolved = path.resolve(targetPath);
  return process.platform === 'win32' ? resolved.toLowerCase() : resolved;
}

function isInsideRoot(rootPath: string, targetPath: string): boolean {
  const root = normalizeForCompare(rootPath);
  const target = normalizeForCompare(targetPath);
  const relative = path.relative(root, target);
  return relative === '' || (!!relative && !relative.startsWith('..') && !path.isAbsolute(relative));
}

function cleanRelativePath(relativePath: string): string {
  return relativePath.trim().replace(/[\\/]+/g, path.sep);
}

function cleanAbsolutePath(absolutePath: string): string {
  return absolutePath.trim();
}

export function resolveRecordSourcePath(
  rootPath: string | null | undefined,
  payload: OpenLocalFsRecordSourcePayload,
  deps?: ResolveDeps,
): ResolveRecordSourcePathResult {
  const rawRoot = rootPath?.trim();
  if (!rawRoot) {
    return {
      ok: false,
      code: 'MISSING_ROOT',
      error: 'Local sync root is unavailable on this desktop.',
    };
  }

  const root = path.resolve(rawRoot);
  const realRoot = realpathIfExists(root, deps);
  const rawRelative = payload.localFsRelativePath?.trim();
  const rawAbsolute = payload.absolutePath?.trim();
  let candidate: string | null = null;
  let source: 'relative' | 'absolute' = 'relative';

  if (rawRelative) {
    const relativePath = cleanRelativePath(rawRelative);
    candidate = path.resolve(root, relativePath);
  } else if (rawAbsolute) {
    const absolutePath = cleanAbsolutePath(rawAbsolute);
    if (
      absolutePath.startsWith(STORAGE_PATH_PREFIX) ||
      !path.isAbsolute(absolutePath)
    ) {
      return {
        ok: false,
        code: 'INVALID_ABSOLUTE_PATH',
        error: 'Record source path is not a local absolute path.',
      };
    }
    candidate = path.resolve(absolutePath);
    source = 'absolute';
  }

  if (!candidate) {
    return {
      ok: false,
      code: 'MISSING_PATH',
      error: 'Record source path is unavailable on this desktop.',
    };
  }

  if (!isInsideRoot(root, candidate) && !isInsideRoot(realRoot, candidate)) {
    return {
      ok: false,
      code: 'PATH_OUTSIDE_ROOT',
      error: 'Record source path is outside the Local FS sync root.',
    };
  }

  const realCandidate = realpathIfExists(candidate, deps);
  if (!isInsideRoot(realRoot, realCandidate)) {
    return {
      ok: false,
      code: 'PATH_OUTSIDE_ROOT',
      error: 'Record source path resolves outside the Local FS sync root.',
    };
  }

  return { ok: true, path: realCandidate, source };
}

export async function openLocalFsRecordSource(
  payload: OpenLocalFsRecordSourcePayload,
  deps: OpenLocalFsRecordSourceDeps,
): Promise<OpenLocalFsRecordSourceResult> {
  const connectorId = payload.connectorId?.trim();
  if (!connectorId) {
    return {
      ok: false,
      code: 'MISSING_CONNECTOR',
      error: 'Local FS connector is unavailable for this record.',
    };
  }

  const meta = deps.getMeta(connectorId);
  const resolved = resolveRecordSourcePath(meta?.rootPath, payload, deps);
  if (resolved.ok === false) {
    return {
      ok: false,
      code: resolved.code,
      error: resolved.error,
    };
  }

  const existsSync = deps.existsSync ?? fs.existsSync;
  if (!existsSync(resolved.path)) {
    return {
      ok: false,
      code: 'NOT_FOUND',
      error: 'Local file was not found on this desktop.',
    };
  }

  try {
    const stat = (deps.statSync ?? fs.statSync)(resolved.path);
    if (stat.isDirectory()) {
      const openError = await deps.openPath(resolved.path);
      if (openError) {
        return { ok: false, code: 'OPEN_FAILED', error: openError };
      }
      return { ok: true, path: resolved.path, action: 'open-directory' };
    }

    deps.showItemInFolder(resolved.path);
    return { ok: true, path: resolved.path, action: 'show-item' };
  } catch (error) {
    return {
      ok: false,
      code: 'OPEN_FAILED',
      error: error instanceof Error ? error.message : 'Could not open local file.',
    };
  }
}
