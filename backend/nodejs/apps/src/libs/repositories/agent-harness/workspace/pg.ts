import {
  MergeStatus,
  WorkspaceError,
} from '../../../../modules/agent-harness/domain/workspace';
import type { SealedContent } from '../../../../modules/agent-harness/domain/data-protection';
import type {
  ManifestSummary,
  MergeRecord,
  StoredManifest,
  WorkspaceAppend,
  WorkspaceHeadRecord,
  WorkspaceKeyRecord,
  WorkspaceKeyStore,
  WorkspaceRepository,
  WorkspaceWriterStore,
  WriterAcquired,
  WriterLeaseRecord,
} from '../../../../modules/agent-harness/ports/workspace-store';
import { orgOrJobsUnit } from '../pg-documents/org-or-jobs';
import {
  decodeDoc,
  docParam,
  rowRegion,
  type PgKernelTx,
} from '../pg-documents/pg';
import { moveWorkspaceHeadIn } from '../workspace-head-cas/pg';
import type { WorkspaceHeadDocument } from '../execution/document';

const REASON = 'workspaces';

const notAFork = (): WorkspaceError =>
  new WorkspaceError('WORKSPACE_NOT_A_FORK', 'Not a fork');

const sealed = (value: SealedContent): SealedContent => ({
  v: value.v,
  kid: value.kid,
  kv: value.kv,
  iv: value.iv,
  tag: value.tag,
  ct: value.ct,
});

const summary = (manifest: StoredManifest): ManifestSummary => ({
  orgId: manifest.orgId,
  workspaceId: manifest.workspaceId,
  revision: manifest.revision,
  parentRevision: manifest.parentRevision ?? null,
  contentHash: manifest.contentHash,
  fileCount: manifest.fileCount,
  reason: manifest.reason,
  runId: manifest.runId,
  createdAt: manifest.createdAt,
});

const mergedForkFields = (
  mergeBase: number,
  record: MergeRecord | undefined,
): Record<string, unknown> =>
  record === undefined ? { mergeBase } : { mergeBase, lastMerge: record };

/** Sets the merge fields on a fork's head; false when the workspace is not a fork. */
async function markMerged(
  k: PgKernelTx,
  orgId: string,
  forkId: string,
  mergeBase: number,
  record: MergeRecord | undefined,
): Promise<boolean> {
  const { rowCount } = await k.query(
    `/* pipeshub:harness.workspace_heads_merged */ UPDATE harness.workspace_heads SET doc = doc || $3::jsonb
      WHERE org_id = $1 AND workspace_id = $2 AND is_fork`,
    [orgId, forkId, docParam(mergedForkFields(mergeBase, record))],
  );
  return rowCount === 1;
}

/** Heads in `harness.workspace_heads`, revisions in `harness.workspace_manifests`, one unit per append. */
export class PgWorkspaceRepository implements WorkspaceRepository {
  head(
    orgId: string,
    workspaceId: string,
  ): Promise<WorkspaceHeadRecord | null> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ doc: unknown }>(
          `/* pipeshub:harness.workspace_heads_get */ SELECT doc FROM harness.workspace_heads
          WHERE org_id = $1 AND workspace_id = $2`,
          [orgId, workspaceId],
        );
        if (rows[0] === undefined) return null;
        const document = decodeDoc<WorkspaceHeadDocument>(rows[0].doc);
        return {
          orgId,
          workspaceId,
          revision: document.revision,
          forkOf: document.forkOf
            ? {
                workspaceId: document.forkOf.workspaceId,
                revision: document.forkOf.revision,
              }
            : null,
          mergeBase: document.mergeBase ?? null,
          lastMerge: document.lastMerge
            ? {
                key: document.lastMerge.key,
                into: document.lastMerge.into,
                base: document.lastMerge.base,
                theirs: document.lastMerge.theirs,
                ours: document.lastMerge.ours,
                revision: document.lastMerge.revision ?? null,
                status: document.lastMerge.status as MergeStatus,
              }
            : null,
        };
      },
    );
  }

  manifest(
    orgId: string,
    workspaceId: string,
    revision: number,
  ): Promise<StoredManifest | null> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ doc: unknown }>(
          `/* pipeshub:harness.workspace_manifests_get */ SELECT doc FROM harness.workspace_manifests
          WHERE org_id = $1 AND workspace_id = $2 AND revision = $3`,
          [orgId, workspaceId, revision],
        );
        if (rows[0] === undefined) return null;
        const manifest = decodeDoc<StoredManifest>(rows[0].doc);
        return { ...summary(manifest), files: sealed(manifest.files) };
      },
    );
  }

  revisions(
    orgId: string,
    workspaceId: string,
    page: { limit: number; before?: number },
  ): Promise<ManifestSummary[]> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ doc: unknown }>(
          `/* pipeshub:harness.workspace_manifests_page */ SELECT doc - 'files' AS doc FROM harness.workspace_manifests
          WHERE org_id = $1 AND workspace_id = $2 AND ($3::bigint IS NULL OR revision < $3)
          ORDER BY revision DESC LIMIT $4`,
          [orgId, workspaceId, page.before ?? null, page.limit],
        );
        return rows.map((row) => summary(decodeDoc<StoredManifest>(row.doc)));
      },
    );
  }

  async append(write: WorkspaceAppend): Promise<void> {
    const { manifest } = write;
    await orgOrJobsUnit(
      undefined,
      manifest.orgId,
      REASON,
      { write: true },
      async (k) => {
        const moved = await moveWorkspaceHeadIn(
          k,
          {
            orgId: manifest.orgId,
            workspaceId: manifest.workspaceId,
            expectedRevision: write.expectedRevision,
            revision: manifest.revision,
            runId: manifest.runId,
            onCreate:
              write.forkOf === undefined
                ? undefined
                : { forkOf: write.forkOf, mergeBase: manifest.revision },
          },
          manifest.createdAt,
        );
        if (!moved) {
          throw new WorkspaceError(
            'WORKSPACE_CONFLICT',
            'Workspace HEAD moved since the expected revision',
          );
        }
        const { rowCount } = await k.query(
          `/* pipeshub:harness.workspace_manifests_insert */ INSERT INTO harness.workspace_manifests
           (org_id, workspace_id, revision, doc, region)
         VALUES ($1, $2, $3, $4::jsonb, $5)
         ON CONFLICT (org_id, workspace_id, revision) DO NOTHING`,
          [
            manifest.orgId,
            manifest.workspaceId,
            manifest.revision,
            docParam({ ...summary(manifest), files: sealed(manifest.files) }),
            rowRegion(),
          ],
        );
        // A revision already stored under this head: someone else's write won. Throwing undoes the head move.
        if (rowCount !== 1) {
          throw new WorkspaceError(
            'WORKSPACE_CONFLICT',
            'Workspace revision already exists',
          );
        }
        if (
          write.mergedFork !== undefined &&
          !(await markMerged(
            k,
            manifest.orgId,
            write.mergedFork.workspaceId,
            write.mergedFork.mergeBase,
            write.mergedFork.record,
          ))
        ) {
          throw notAFork();
        }
      },
    );
  }

  async setMergeBase(
    orgId: string,
    forkId: string,
    mergeBase: number,
    record?: MergeRecord,
  ): Promise<void> {
    const marked = await orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: true },
      (k) => markMerged(k, orgId, forkId, mergeBase, record),
    );
    if (!marked) throw notAFork();
  }
}

/**
 * Write leases in `harness.workspace_writers`, one row per workspace. The row is taken under its lock: a free or
 * expired lease, the holder's own, or the one `takeOverFrom` names is taken over; another live holder is named. Two
 * acquirers of a workspace with no row both insert-or-nothing, and the one that inserted nothing reads the winner.
 */
export class PgWorkspaceWriterStore implements WorkspaceWriterStore {
  acquire(
    lease: WriterLeaseRecord,
    now: number,
    takeOverFrom?: string,
  ): Promise<WriterAcquired> {
    return orgOrJobsUnit(
      undefined,
      lease.orgId,
      REASON,
      { write: true },
      async (k) => {
        for (let attempt = 0; attempt < 3; attempt += 1) {
          const { rows } = await k.query<{
            run_id: string;
            expires_at: string;
          }>(
            `/* pipeshub:harness.workspace_writers_lock */ SELECT run_id, expires_at::text AS expires_at
             FROM harness.workspace_writers WHERE org_id = $1 AND workspace_id = $2 FOR UPDATE`,
            [lease.orgId, lease.workspaceId],
          );
          const prior = rows[0];
          if (prior === undefined) {
            const { rowCount } = await k.query(
              `/* pipeshub:harness.workspace_writers_insert */ INSERT INTO harness.workspace_writers
               (org_id, workspace_id, session_id, run_id, expires_at, updated_at, region)
             VALUES ($1, $2, $3, $4, $5, $6, $7)
             ON CONFLICT (org_id, workspace_id) DO NOTHING`,
              [
                lease.orgId,
                lease.workspaceId,
                lease.sessionId,
                lease.runId,
                lease.expiresAt,
                now,
                rowRegion(),
              ],
            );
            if (rowCount === 1)
              return { acquired: true, lease: { ...lease }, renewed: false };
            continue;
          }
          const priorExpiry = Number(prior.expires_at);
          const takeable =
            prior.run_id === lease.runId ||
            priorExpiry <= now ||
            (takeOverFrom !== undefined && prior.run_id === takeOverFrom);
          if (!takeable) return { acquired: false, holder: prior.run_id };
          await k.query(
            `/* pipeshub:harness.workspace_writers_take */ UPDATE harness.workspace_writers
              SET session_id = $3, run_id = $4, expires_at = $5, updated_at = $6
            WHERE org_id = $1 AND workspace_id = $2`,
            [
              lease.orgId,
              lease.workspaceId,
              lease.sessionId,
              lease.runId,
              lease.expiresAt,
              now,
            ],
          );
          return {
            acquired: true,
            lease: { ...lease },
            renewed: prior.run_id === lease.runId && priorExpiry > now,
          };
        }
        throw new Error('Workspace writer lease kept changing hands');
      },
    );
  }

  release(orgId: string, workspaceId: string, runId: string): Promise<boolean> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: true },
      async (k) => {
        const { rowCount } = await k.query(
          `/* pipeshub:harness.workspace_writers_release */ DELETE FROM harness.workspace_writers
          WHERE org_id = $1 AND workspace_id = $2 AND run_id = $3`,
          [orgId, workspaceId, runId],
        );
        return rowCount === 1;
      },
    );
  }
}

type KeyDocument = Omit<WorkspaceKeyRecord, 'orgId' | 'sessionId'>;

/** Session keys (D33) in `harness.workspace_keys`: an issued key or a shred commits before the call returns. */
export class PgWorkspaceKeyStore implements WorkspaceKeyStore {
  get(orgId: string, sessionId: string): Promise<WorkspaceKeyRecord | null> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: false },
      async (k) => {
        const document = await this.read(k, orgId, sessionId, false);
        if (document === null) return null;
        return {
          orgId,
          sessionId,
          keyId: document.keyId,
          wrapped: document.wrapped ? sealed(document.wrapped) : null,
          createdAt: document.createdAt,
          destroyedAt: document.destroyedAt ?? null,
        };
      },
    );
  }

  insert(record: WorkspaceKeyRecord): Promise<boolean> {
    return orgOrJobsUnit(
      undefined,
      record.orgId,
      REASON,
      { write: true },
      async (k) => {
        const document: KeyDocument = {
          keyId: record.keyId,
          wrapped: record.wrapped === null ? null : sealed(record.wrapped),
          createdAt: record.createdAt,
          destroyedAt: record.destroyedAt,
        };
        const { rowCount } = await k.query(
          `/* pipeshub:harness.workspace_keys_insert */ INSERT INTO harness.workspace_keys (org_id, session_id, doc, region)
         VALUES ($1, $2, $3::jsonb, $4)
         ON CONFLICT (org_id, session_id) DO NOTHING`,
          [record.orgId, record.sessionId, docParam(document), rowRegion()],
        );
        return rowCount === 1;
      },
    );
  }

  /** The key's row under its lock, then the tombstone; a session with no key gets one, so no key is issued later. */
  destroy(orgId: string, sessionId: string, at: number): Promise<boolean> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      REASON,
      { write: true },
      async (k) => {
        for (let attempt = 0; attempt < 3; attempt += 1) {
          // A stored key document is JSON written by older code too: any field may be missing.
          const prior: Partial<KeyDocument> | null = await this.read(
            k,
            orgId,
            sessionId,
            true,
          );
          if (prior === null) {
            const tombstone: KeyDocument = {
              keyId: '',
              wrapped: null,
              createdAt: at,
              destroyedAt: at,
            };
            const { rowCount } = await k.query(
              `/* pipeshub:harness.workspace_keys_tombstone */ INSERT INTO harness.workspace_keys (org_id, session_id, doc, region)
             VALUES ($1, $2, $3::jsonb, $4)
             ON CONFLICT (org_id, session_id) DO NOTHING`,
              [orgId, sessionId, docParam(tombstone), rowRegion()],
            );
            if (rowCount === 1) return false;
            continue;
          }
          const next: KeyDocument = {
            keyId: prior.keyId ?? '',
            wrapped: null,
            createdAt: prior.createdAt ?? at,
            destroyedAt: prior.destroyedAt ?? at,
          };
          await k.query(
            `/* pipeshub:harness.workspace_keys_shred */ UPDATE harness.workspace_keys SET doc = $3::jsonb
            WHERE org_id = $1 AND session_id = $2`,
            [orgId, sessionId, docParam(next)],
          );
          return prior.wrapped != null;
        }
        throw new Error('Workspace key kept changing while it was shredded');
      },
    );
  }

  private async read(
    k: PgKernelTx,
    orgId: string,
    sessionId: string,
    lock: boolean,
  ): Promise<KeyDocument | null> {
    const { rows } = await k.query<{ doc: unknown }>(
      lock
        ? `/* pipeshub:harness.workspace_keys_lock */ SELECT doc FROM harness.workspace_keys
            WHERE org_id = $1 AND session_id = $2 FOR UPDATE`
        : `/* pipeshub:harness.workspace_keys_get */ SELECT doc FROM harness.workspace_keys
            WHERE org_id = $1 AND session_id = $2`,
      [orgId, sessionId],
    );
    return rows[0] === undefined ? null : decodeDoc<KeyDocument>(rows[0].doc);
  }
}
