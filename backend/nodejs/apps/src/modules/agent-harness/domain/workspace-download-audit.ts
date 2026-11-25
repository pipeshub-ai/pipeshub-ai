import { createHash } from 'node:crypto';
import { canonicalDigest } from '../contracts/canonical-json';
import type { AuditEntry, AuditFields } from './audit';
import { principalRefFor } from './run-spec';
import { normalizeWorkspacePath } from './workspace';

export const WORKSPACE_DOWNLOADED_KIND = 'workspace.downloaded';

/** What a session workspace download (SLASH3 `/download`) reports to the audit: never a byte of a file. */
export interface WorkspaceDownloadAudit {
  type: typeof WORKSPACE_DOWNLOADED_KIND;
  orgId: string;
  actorId: string;
  sessionId: string;
  revision: number;
  /** One file as itself, or a zip of a folder (`path`) or of the whole workspace (no `path`). */
  target: 'file' | 'archive';
  path?: string;
  files: number;
  bytes: number;
}

/** The audit stream of a session's workspace downloads: their own stream of the org's chain, as `cp:` is for config. */
export const workspaceDownloadStreamId = (sessionId: string): string =>
  `wsdl:${sessionId}`;

export const workspacePathDigest = (path: string): string =>
  `sha256:${createHash('sha256').update(path, 'utf8').digest('hex')}`;

/**
 * The chain entry of a download (GAPS2-2): who, which chat, which revision,
 * file or zip, how many files and bytes, and the path's SHA-256. A path is the
 * person's content (a file name can name anyone), and the chain outlives the
 * session's erasure, so it holds the path's digest, never the path (REPORT
 * D28): an admin confirms a path by its digest. `at` is the download's time
 * in milliseconds, which orders the stream.
 */
export function workspaceDownloadAuditEntry(
  download: WorkspaceDownloadAudit,
  at: number,
): AuditEntry {
  const fields: AuditFields = {
    by: principalRefFor(download.actorId),
    target: download.target,
    revision: download.revision,
    files: download.files,
    bytes: download.bytes,
  };
  if (download.path !== undefined && download.path !== '') {
    // A folder is hashed in the canonical form a file path has, so one typed path checks both (AUDITUI).
    fields['pathDigest'] = workspacePathDigest(
      normalizeWorkspacePath(download.path),
    );
  }
  return {
    kind: WORKSPACE_DOWNLOADED_KIND,
    sessionId: download.sessionId,
    streamId: workspaceDownloadStreamId(download.sessionId),
    runId: null,
    journalSeq: at,
    writer: 'node',
    attemptId: null,
    fence: null,
    fields,
    dataDigest: canonicalDigest({ ...download, at }),
  };
}
