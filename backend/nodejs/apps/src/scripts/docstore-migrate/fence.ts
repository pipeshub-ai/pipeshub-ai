/** The cutover tooling's side of the Mongo fence document (see libs/repositories/fence.ts). */
import {
  FENCE_POLL_GAP_MS,
  FENCE_WAIT_QUEUE_MS,
  FENCED_WRITE_OP_MATCH,
  fenceCutoverWaitMs,
  type FenceSendOptions,
} from '../../libs/repositories/fence';
import type { MigrationRuntime } from './runtime';

export type FenceMode = 'active' | 'draining' | 'readonly' | 'retired';

export async function readMongoFence(
  rt: MigrationRuntime,
): Promise<{ mode: FenceMode; epoch: number }> {
  const d = await rt
    .db()
    .collection<{
      _id: string;
      mode?: FenceMode;
      epoch?: number;
    }>('docstore_fence')
    .findOne({ _id: 'fence' });
  return { mode: d?.mode ?? 'active', epoch: d?.epoch ?? 1 };
}

export const sleep = {
  ms: (n: number): Promise<void> => new Promise((r) => setTimeout(r, n)),
};

/** Raised when a fenced write was still running at the operator's deadline. The store is left `draining`; nothing is forced. */
export class FenceOpsTimeoutError extends Error {
  constructor(
    readonly running: number,
    readonly waitedMs: number,
  ) {
    super(
      `${running} fenced write(s) still running after ${Math.round(waitedMs / 1000)} s; the store stays 'draining' and the cutover is aborted`,
    );
  }
}

/** Tunables; tests shorten them. `opsTimeoutMs` is the operator's limit (MIGRATE_FENCE_OPS_TIMEOUT_S, default 120 s). */
export const fenceTuning = {
  /**
   * The client options the application's writes run with: its connect, server selection and retry settings come from the same
   * deployment as this tool's own client, and its wait queue is the bounded one it runs with while the fence is armed. Tests of the
   * command line set MIGRATE_FENCE_CLIENT_OPTIONS (JSON) with MIGRATE_TEST_HOOKS=1 to shorten the wait.
   */
  clientOptions: (rt: MigrationRuntime): FenceSendOptions => {
    if (
      process.env.MIGRATE_TEST_HOOKS === '1' &&
      process.env.MIGRATE_FENCE_CLIENT_OPTIONS
    )
      return JSON.parse(
        process.env.MIGRATE_FENCE_CLIENT_OPTIONS,
      ) as FenceSendOptions;
    const o = rt.db().client.options as unknown as FenceSendOptions;
    return { ...o, waitQueueTimeoutMS: FENCE_WAIT_QUEUE_MS };
  },
  pollMs: FENCE_POLL_GAP_MS,
  opsTimeoutMs: Number(process.env.MIGRATE_FENCE_OPS_TIMEOUT_S ?? 120) * 1000,
  log: (event: string, data: Record<string, unknown>): void => {
    process.stderr.write(`${JSON.stringify({ event, ...data })}\n`);
  },
  /** How many operations tagged as fenced writes `$currentOp` lists (all users, idle sessions included). */
  fencedOpsRunning: async (rt: MigrationRuntime): Promise<number> => {
    const ops = await rt
      .db()
      .client.db('admin')
      .aggregate([
        { $currentOp: { allUsers: true, idleSessions: true } },
        { $match: FENCED_WRITE_OP_MATCH },
      ])
      .toArray();
    return ops.length;
  },
};

/**
 * The rule for leaving `draining`. A write that passed the fence earlier can still be on its way to the server, for as long as the
 * client can hold it (wait queue, connect, server selection, once more for a retry). So, before the fence becomes `readonly` or
 * `retired`: it must be `draining`; it has been for fenceCutoverWaitMs(client options), counted from the fence document's `at`; and
 * two polls of `$currentOp`, FENCE_POLL_GAP_MS apart, list no operation carrying the fenced-write tag (and every poll before them
 * that did list one has been waited out). The polling phase logs its progress and ends at the operator's timeout by throwing
 * FenceOpsTimeoutError, never by moving the fence anyway. A client with an unbounded wait queue gives no bound at all: refused.
 */
async function enforceDrainDwell(
  rt: MigrationRuntime,
  to: FenceMode,
): Promise<void> {
  if (to !== 'readonly' && to !== 'retired') return;
  const d = await rt
    .db()
    .collection<{ _id: string; mode?: FenceMode; at?: Date }>('docstore_fence')
    .findOne({ _id: 'fence' });
  const mode = d?.mode ?? 'active';
  if (mode === 'readonly' || mode === 'retired') return; // already past the barrier
  if (mode !== 'draining')
    throw new Error(
      `the fence must be 'draining' before '${to}' (it is '${mode}')`,
    );
  const wait = fenceCutoverWaitMs(fenceTuning.clientOptions(rt));
  if (!Number.isFinite(wait))
    throw new Error(
      'the MongoDB client has an unbounded wait queue (waitQueueTimeoutMS 0): no time bounds a write that passed the fence; the cutover is refused',
    );
  const left = wait - (Date.now() - (d?.at?.getTime() ?? 0));
  if (left > 0) await sleep.ms(left);
  const t0 = Date.now();
  let clean = 0;
  while (clean < 2) {
    const running = await fenceTuning.fencedOpsRunning(rt);
    if (running === 0) {
      clean += 1;
      if (clean < 2) await sleep.ms(fenceTuning.pollMs);
      continue;
    }
    clean = 0;
    const waited = Date.now() - t0;
    if (waited >= fenceTuning.opsTimeoutMs)
      throw new FenceOpsTimeoutError(running, waited);
    fenceTuning.log('fence_waiting_for_writes', {
      running,
      waitedMs: waited,
      timeoutMs: fenceTuning.opsTimeoutMs,
    });
    await sleep.ms(fenceTuning.pollMs);
  }
}

export async function setMongoFence(
  rt: MigrationRuntime,
  mode: FenceMode,
  epoch?: number,
): Promise<void> {
  await enforceDrainDwell(rt, mode);
  const cur = await readMongoFence(rt);
  await rt
    .db()
    .collection<{ _id: string }>('docstore_fence')
    .replaceOne(
      { _id: 'fence' },
      {
        _id: 'fence',
        mode,
        epoch: epoch ?? cur.epoch,
        at: new Date(),
      },
      { upsert: true },
    );
}

/**
 * Moves the fence from one of the modes in `from` to `to`, in one atomic update, and says whether it did. No document means `active`.
 * A fence that is `readonly` or `retired` is never moved by a command that only means to undo a drain.
 */
export async function casMongoFence(
  rt: MigrationRuntime,
  from: FenceMode[],
  to: FenceMode,
): Promise<boolean> {
  await enforceDrainDwell(rt, to);
  const col = rt.db().collection<{
    _id: string;
    mode?: FenceMode;
    epoch?: number;
  }>('docstore_fence');
  const r = await col.updateOne(
    { _id: 'fence', mode: { $in: from } },
    { $set: { mode: to, at: new Date() } },
  );
  if (r.matchedCount > 0) return true;
  if (
    !from.includes('active') ||
    (await col.countDocuments({ _id: 'fence' })) > 0
  )
    return false;
  try {
    await col.insertOne({
      _id: 'fence',
      mode: to,
      epoch: 1,
      at: new Date(),
    } as never);
    return true;
  } catch {
    return false; // someone else created it in between
  }
}

export interface AppClient {
  appName: string;
  client: string;
}

/**
 * Connections to MongoDB that belong to a running application: the application names its connections `pipeshub-app` (the Slack bot
 * `pipeshub-slack`); every other Node driver connection counts too (mongosh excepted), whatever name it carries, so a name set in the URI cannot hide one. This sees an idle application too, because its pool
 * keeps connections open. This tool's own connections (`docstore-migrate`) and other tools (mongosh, backups) are not counted.
 */
export async function appClients(rt: MigrationRuntime): Promise<AppClient[]> {
  const rows = await rt.mongo
    .db('admin')
    .aggregate([{ $currentOp: { allUsers: true, idleConnections: true } }])
    .toArray();
  const out = new Map<string, AppClient>();
  for (const r of rows) {
    const appName = String(
      r.appName ?? r.clientMetadata?.application?.name ?? '',
    );
    const driver = String(r.clientMetadata?.driver?.name ?? '');
    if (appName === 'docstore-migrate') continue;
    // by name, or by the Node driver in the connection's metadata: a name set in MONGO_URI (appName=...) must not hide an application
    const app =
      appName.startsWith('pipeshub') ||
      (driver.startsWith('nodejs') && !driver.includes('mongosh'));
    if (app)
      out.set(String(r.client ?? r.connectionId), {
        appName,
        client: String(r.client ?? ''),
      });
  }
  return [...out.values()];
}

export const describeClients = (c: AppClient[]): string =>
  `${String(c.length)} application connection${c.length === 1 ? '' : 's'} to MongoDB (${c
    .slice(0, 3)
    .map((x) => x.client || x.appName)
    .join(', ')}${c.length > 3 ? ', …' : ''})`;
