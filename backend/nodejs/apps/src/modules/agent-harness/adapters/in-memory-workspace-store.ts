/* eslint-disable @typescript-eslint/require-await -- This deterministic adapter implements the asynchronous store port without I/O. */
import { WorkspaceError } from '../domain/workspace';
import {
  ManifestSummary,
  MergeRecord,
  WorkspaceAppend,
  WorkspaceHeadRecord,
  WorkspaceKeyRecord,
  WorkspaceKeyStore,
  WorkspaceRepository,
  WorkspaceWriterStore,
  WriterAcquired,
  WriterLeaseRecord,
  StoredManifest,
} from '../ports/workspace-store';

const key = (...parts: Array<string | number>): string => JSON.stringify(parts);

/** Reference `WorkspaceRepository`; each method checks and writes without awaiting. */
export class InMemoryWorkspaceRepository implements WorkspaceRepository {
  readonly heads = new Map<string, WorkspaceHeadRecord>();
  readonly manifests = new Map<string, StoredManifest>();

  async head(
    orgId: string,
    workspaceId: string,
  ): Promise<WorkspaceHeadRecord | null> {
    const head = this.heads.get(key(orgId, workspaceId));
    return head === undefined ? null : { ...head };
  }

  async manifest(
    orgId: string,
    workspaceId: string,
    revision: number,
  ): Promise<StoredManifest | null> {
    return this.manifests.get(key(orgId, workspaceId, revision)) ?? null;
  }

  async revisions(
    orgId: string,
    workspaceId: string,
    page: { limit: number; before?: number },
  ): Promise<ManifestSummary[]> {
    return [...this.manifests.values()]
      .filter(
        (manifest) =>
          manifest.orgId === orgId &&
          manifest.workspaceId === workspaceId &&
          (page.before === undefined || manifest.revision < page.before),
      )
      .sort((a, b) => b.revision - a.revision)
      .slice(0, page.limit)
      .map(({ files: _files, ...summary }) => summary);
  }

  async append(write: WorkspaceAppend): Promise<void> {
    const { orgId, workspaceId, revision } = write.manifest;
    const headKey = key(orgId, workspaceId);
    const current = this.heads.get(headKey);
    if ((current?.revision ?? 0) !== write.expectedRevision) {
      throw new WorkspaceError(
        'WORKSPACE_CONFLICT',
        'Workspace HEAD moved since the expected revision',
      );
    }
    const fork =
      write.mergedFork === undefined
        ? undefined
        : this.heads.get(key(orgId, write.mergedFork.workspaceId));
    if (write.mergedFork !== undefined && fork?.forkOf == null) {
      throw new WorkspaceError('WORKSPACE_NOT_A_FORK', 'Not a fork');
    }
    this.manifests.set(key(orgId, workspaceId, revision), write.manifest);
    this.heads.set(headKey, {
      orgId,
      workspaceId,
      revision,
      forkOf: current?.forkOf ?? write.forkOf ?? null,
      mergeBase:
        current?.mergeBase ?? (write.forkOf === undefined ? null : revision),
      lastMerge: current?.lastMerge ?? null,
    });
    if (fork !== undefined && write.mergedFork !== undefined) {
      fork.mergeBase = write.mergedFork.mergeBase;
      if (write.mergedFork.record !== undefined) {
        fork.lastMerge = { ...write.mergedFork.record };
      }
    }
  }

  async setMergeBase(
    orgId: string,
    forkId: string,
    mergeBase: number,
    record?: MergeRecord,
  ): Promise<void> {
    const fork = this.heads.get(key(orgId, forkId));
    if (fork?.forkOf == null) {
      throw new WorkspaceError('WORKSPACE_NOT_A_FORK', 'Not a fork');
    }
    fork.mergeBase = mergeBase;
    if (record !== undefined) fork.lastMerge = { ...record };
  }
}

/** Reference `WorkspaceWriterStore`; check and write with no await in between. */
export class InMemoryWorkspaceWriterStore implements WorkspaceWriterStore {
  readonly leases = new Map<string, WriterLeaseRecord>();

  async acquire(
    lease: WriterLeaseRecord,
    now: number,
    takeOverFrom?: string,
  ): Promise<WriterAcquired> {
    const leaseKey = key(lease.orgId, lease.workspaceId);
    const current = this.leases.get(leaseKey);
    const live = current !== undefined && current.expiresAt > now;
    if (
      live &&
      current.runId !== lease.runId &&
      current.runId !== takeOverFrom
    ) {
      return { acquired: false, holder: current.runId };
    }
    this.leases.set(leaseKey, { ...lease });
    return {
      acquired: true,
      lease: { ...lease },
      renewed: live && current.runId === lease.runId,
    };
  }

  async release(
    orgId: string,
    workspaceId: string,
    runId: string,
  ): Promise<boolean> {
    const leaseKey = key(orgId, workspaceId);
    if (this.leases.get(leaseKey)?.runId !== runId) return false;
    this.leases.delete(leaseKey);
    return true;
  }
}

export class InMemoryWorkspaceKeyStore implements WorkspaceKeyStore {
  readonly records = new Map<string, WorkspaceKeyRecord>();

  async get(
    orgId: string,
    sessionId: string,
  ): Promise<WorkspaceKeyRecord | null> {
    const record = this.records.get(key(orgId, sessionId));
    return record === undefined ? null : { ...record };
  }

  async insert(record: WorkspaceKeyRecord): Promise<boolean> {
    const recordKey = key(record.orgId, record.sessionId);
    if (this.records.has(recordKey)) return false;
    this.records.set(recordKey, { ...record });
    return true;
  }

  async destroy(
    orgId: string,
    sessionId: string,
    at: number,
  ): Promise<boolean> {
    const recordKey = key(orgId, sessionId);
    const current = this.records.get(recordKey);
    this.records.set(recordKey, {
      orgId,
      sessionId,
      keyId: current?.keyId ?? '',
      wrapped: null,
      createdAt: current?.createdAt ?? at,
      destroyedAt: current?.destroyedAt ?? at,
    });
    return current?.wrapped != null;
  }
}
