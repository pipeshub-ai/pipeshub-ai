/**
 * SLASH2 C3: a session workspace written as a worker writes it (each object
 * sealed with the session key, the commit through `WorkspaceService.commit`
 * under the run's attempt) and read as its owner reads it in the chat.
 */
import { InMemoryWorkspaceObjects } from '../../../../src/modules/agent-harness/adapters/in-memory-workspace-objects';
import {
  InMemoryWorkspaceKeyStore,
  InMemoryWorkspaceRepository,
  InMemoryWorkspaceWriterStore,
} from '../../../../src/modules/agent-harness/adapters/in-memory-workspace-store';
import { SessionWorkspaceKeys } from '../../../../src/modules/agent-harness/application/session-workspace-keys';
import { SessionWorkspaceService } from '../../../../src/modules/agent-harness/application/session-workspace-service';
import { WorkspaceObjectResealer } from '../../../../src/modules/agent-harness/application/workspace-object-resealer';
import { WorkspaceService } from '../../../../src/modules/agent-harness/application/workspace-service';
import { AttemptClaims } from '../../../../src/modules/agent-harness/domain/authority';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { CommitReason, WorkspaceFileEntry } from '../../../../src/modules/agent-harness/domain/workspace';
import { decodeSealedId, encodeSealedId, openObject, sealObject } from '../../../../src/modules/agent-harness/domain/workspace-sealing';
import { SessionStreamAccess } from '../../../../src/modules/agent-harness/ports/chat-session-repository';
import { dataKeyRing } from './data-protection';

export const ORG = 'org-1';
export const SESSION = 's1';
export const OWNER = 'user-owner';
export const WORKSPACE = `ws:${SESSION}`;
export const scope = { orgId: ORG, sessionId: SESSION, actorId: OWNER };

export const claims = (r: HarnessRun): AttemptClaims => ({
  orgId: r.orgId,
  runId: r.runId,
  attemptId: r.attemptId ?? '',
  fence: r.fence,
  workerId: r.workerId ?? '',
});

/** A file's new text, a binary's bytes, or null to delete it. */
export type FileWrite = string | Buffer | null;

export function workspaceWorld() {
  const runs = new Map<string, HarnessRun>();
  const ring = dataKeyRing();
  const repository = new InMemoryWorkspaceRepository();
  const objects = new InMemoryWorkspaceObjects();
  const keyStore = new InMemoryWorkspaceKeyStore();
  const keys = new SessionWorkspaceKeys({ store: keyStore, cipher: ring.cipher, now: () => 42 });
  let now = 10_000;
  const workspaces = new WorkspaceService({
    runs: { get: async (orgId, runId) => runs.get(`${orgId}/${runId}`) ?? null },
    repository,
    cipher: ring.cipher,
    keys,
    writers: new InMemoryWorkspaceWriterStore(),
    now: () => (now += 1),
  });
  const audience: { value: SessionStreamAccess['audience'] | null } = { value: 'owner' };
  const sessions = {
    findStreamAccess: async () => (audience.value === null ? null : { audience: audience.value }),
  };
  const files = new WorkspaceObjectResealer(keys, objects);
  const owner = new SessionWorkspaceService(
    sessions,
    workspaces,
    (orgId, sessionId) => files.reader(orgId, sessionId),
    undefined,
    (orgId, sessionId) => files.writer(orgId, sessionId),
  );

  const run = (runId: string, extra: Partial<HarnessRun> = {}): HarnessRun => {
    const r = {
      runId,
      orgId: ORG,
      sessionId: SESSION,
      actorId: OWNER,
      harnessMajor: 2,
      status: 'running',
      workerId: 'worker-1',
      attemptId: `attempt-${runId}`,
      fence: 1,
      revision: 3,
      ...extra,
    } as HarnessRun;
    runs.set(`${ORG}/${runId}`, r);
    return r;
  };

  /** One commit as a worker's sync writes it: changed objects sealed with the session key. */
  const write = async (r: HarnessRun, changes: Record<string, FileWrite>, reason: CommitReason = 'turn_end') => {
    const issued = await keys.issue(ORG, SESSION);
    const key = { keyId: issued.keyId, key: issued.key };
    const upserts: WorkspaceFileEntry[] = [];
    const deletes: string[] = [];
    for (const [path, content] of Object.entries(changes)) {
      if (content === null) {
        deletes.push(path);
        continue;
      }
      const sealed = sealObject({ tenantId: ORG, partition: WORKSPACE }, key, Buffer.from(content));
      const inner = await objects.put(ORG, sealed.stored);
      upserts.push({
        path,
        objectId: encodeSealedId(inner),
        contentHash: sealed.contentHash,
        sizeBytes: sealed.sizeBytes,
        mimeType: typeof content === 'string' ? 'text/plain' : 'application/octet-stream',
      });
    }
    const head = await workspaces.head(claims(r), WORKSPACE);
    return workspaces.commit(claims(r), WORKSPACE, {
      baseRevision: head.revision,
      reason,
      changes: { upserts, deletes },
    });
  };

  /** The tree a run's worker hydrates at its start: every head file's text. */
  const tree = async (r: HarnessRun): Promise<Record<string, string>> => {
    const head = await workspaces.head(claims(r), WORKSPACE);
    const issued = await keys.issue(ORG, SESSION);
    const out: Record<string, string> = {};
    for (const entry of head.files) {
      const stored = await objects.get(ORG, decodeSealedId(entry.objectId));
      out[entry.path] = openObject(
        { tenantId: ORG, partition: WORKSPACE },
        { keyId: issued.keyId, key: issued.key },
        stored,
        entry,
      ).toString('utf8');
    }
    return out;
  };

  return {
    runs,
    repository,
    keys,
    objects,
    workspaces,
    owner,
    audience,
    run,
    write,
    tree,
    tick: (ms: number) => {
      now += ms;
    },
  };
}
