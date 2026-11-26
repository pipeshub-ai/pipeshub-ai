/** The cutover tooling's side of the Mongo fence document (see libs/repositories/fence.ts). */
import {
  FENCE_POLL_GAP_MS,
  fenceCutoverWaitMs,
  fenceSendMs,
  appFenceSendOptions,
  FENCED_WRITE_OP_MATCH,
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

/**
 * Tests of the command line set MIGRATE_FENCE_TEST_MS (with MIGRATE_TEST_HOOKS=1) to shorten the two fixed waits of a cutover.
 * Anything but a non-negative whole number is refused, so a typo cannot change the timing silently.
 */
export const fenceTestMs = (): number | undefined => {
  const raw = process.env.MIGRATE_FENCE_TEST_MS;
  if (process.env.MIGRATE_TEST_HOOKS !== '1' || raw === undefined)
    return undefined;
  if (!/^\d+$/.test(raw))
    throw new Error(
      `MIGRATE_FENCE_TEST_MS must be a non-negative whole number of milliseconds, got ${JSON.stringify(raw)}`,
    );
  return Number(raw);
};

/** Tunables; tests shorten them. `opsTimeoutMs` is the operator's limit (MIGRATE_FENCE_OPS_TIMEOUT_S, default 120 s). */
export const fenceTuning = {
  /**
   * The client options the application's writes run with: the application's own settings applied to the same MONGO_URI (see
   * appFenceSendOptions), with the bounded wait queue it runs with while the fence is armed. Tests of the
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
    // the application's own options, not this tool's client (which does not retry writes and waits longer to find a server)
    return appFenceSendOptions(rt.cfg.appMongoUri);
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
  const opts = fenceTuning.clientOptions(rt);
  const shortened = fenceTestMs();
  const wait =
    shortened === undefined
      ? fenceCutoverWaitMs(opts)
      : shortened + fenceSendMs(opts);
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
      if (clean < 2) await sleep.ms(fenceTestMs() ?? fenceTuning.pollMs);
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
    await sleep.ms(fenceTestMs() ?? fenceTuning.pollMs);
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

interface RoleRef {
  role: string;
  db: string;
}
interface AppWritesRecord {
  _id: string;
  users: Array<{
    user: string;
    db: string;
    revoked: RoleRef[];
    granted: RoleRef[];
  }>;
}
/** Kept beside the fence: what the switch took away, so that the rollback gives exactly that back. */
export const APP_WRITES_ID = 'app_writes';
/** The built-in roles that write a database when granted on it. */
const DB_WRITE_ROLES = ['readWrite', 'dbOwner'];
/** The actions that write documents (MongoDB privilege actions). */
const WRITE_ACTIONS = ['insert', 'update', 'remove'];
interface Privilege {
  resource: { db?: string; collection?: string; anyResource?: boolean };
  actions: string[];
}
/**
 * Whether a user's effective privileges (all its roles, custom ones included) write one of `dbs`. A resource names a database, or all of
 * them with db "" (https://www.mongodb.com/docs/manual/reference/resource-document/: `{ db: "", collection: "" }` is "all collections …
 * in all the databases"), or `anyResource`.
 */
const writesAny = (privs: Privilege[], dbs: string[]): boolean =>
  privs.some(
    (p) =>
      (p.resource.anyResource === true ||
        p.resource.db === '' ||
        dbs.includes(p.resource.db ?? '')) &&
      p.actions.some((a) => WRITE_ACTIONS.includes(a)),
  );

export interface AppWritesReport {
  /** `user@db: role on db, …` for every write role taken away (a `read` role is granted on the same database). */
  revoked: string[];
  /** Users MongoDB still lets write the application's databases, with why. */
  stillWrite: string[];
  /** Set when the revoke failed partway or could not run; the store switch itself is done. */
  error?: string;
}

/** The sentence the switch's message carries about the revoke ('' when MongoDB refuses every other user's writes). */
export const appWritesNote = (w: AppWritesReport): string =>
  w.error
    ? ` MongoDB's write roles were not all taken away (${w.error}): revoke readWrite on the application's database from the application's MongoDB user by hand (revokeRolesFromUser).`
    : w.stillWrite.length
      ? ` MongoDB still accepts writes from ${w.stillWrite.join('; ')}: when the application signs in as one of them, only the fence refuses its writes (give it a MongoDB user of its own, and the tool MIGRATE_MONGO_URI, to have MongoDB refuse them too).`
      : '';
const userKey = (u: { user: string; db: string }): string =>
  `${u.user}@${u.db}`;
const roleText = (r: RoleRef[]): string =>
  r.map((x) => `${x.role} on ${x.db}`).join(', ');

/**
 * After the switch (MongoDB retired): every MongoDB user but this tool's own loses the built-in write roles it holds on the application's
 * databases and gets `read` there instead, so a process that skips the fence (or runs with it off) is refused by the server (code 13).
 * The tool keeps its login: it writes MongoDB back at a rollback. What was taken is recorded beside the fence for restoreAppWrites.
 * MongoDB docs (fetched 2026-11-24): revokeRolesFromUser needs the `revokeRole` action, grantRolesToUser `grantRole`, and
 * `usersInfo: { forAllDBs: true }` lists every user with its roles (https://www.mongodb.com/docs/manual/reference/command/usersInfo/).
 * Built-in write roles granted on the application's databases are taken away; a user that still writes them afterwards (an administrator,
 * a custom role) is reported, from its effective privileges (`usersInfo` with `showPrivileges`, one user at a time).
 */
export async function revokeAppWrites(
  rt: MigrationRuntime,
): Promise<AppWritesReport> {
  const dbs = [rt.cfg.mongoDb, ...(rt.cfg.slackDb ? [rt.cfg.slackDb] : [])];
  const report: AppWritesReport = { revoked: [], stillWrite: [] };
  try {
    const admin = rt.mongo.db('admin');
    const status = (await admin.command({ connectionStatus: 1 })) as {
      authInfo?: { authenticatedUsers?: Array<{ user: string; db: string }> };
    };
    const me = new Set(
      (status.authInfo?.authenticatedUsers ?? []).map(userKey),
    );
    const users = ((await admin.command({ usersInfo: { forAllDBs: true } }))
      .users ?? []) as Array<{
      user: string;
      db: string;
      roles: RoleRef[];
    }>;
    const plan: AppWritesRecord['users'] = [];
    const stillWrites = async (u: {
      user: string;
      db: string;
    }): Promise<boolean> => {
      const r = (await rt.mongo
        .db(u.db)
        .command({ usersInfo: u.user, showPrivileges: true })) as {
        users?: Array<{ inheritedPrivileges?: Privilege[] }>;
      };
      return writesAny(r.users?.[0]?.inheritedPrivileges ?? [], dbs);
    };
    const others: Array<{ user: string; db: string; roles: RoleRef[] }> = [];
    for (const u of users) {
      const own = u.roles.filter(
        (r) => DB_WRITE_ROLES.includes(r.role) && dbs.includes(r.db),
      );
      if (me.has(userKey(u))) {
        // without MIGRATE_MONGO_URI the application signs in as this login too, and it cannot be taken away from both
        if (rt.cfg.mongoUri === rt.cfg.appMongoUri && (await stillWrites(u)))
          report.stillWrite.push(
            `${userKey(u)} (${roleText(u.roles)}; the application signs in as this tool's login)`,
          );
        continue;
      }
      others.push(u);
      if (!own.length) continue;
      const granted = [...new Set(own.map((r) => r.db))]
        .filter((db) => !u.roles.some((r) => r.role === 'read' && r.db === db))
        .map((db) => ({ role: 'read', db }));
      plan.push({ user: u.user, db: u.db, revoked: own, granted });
    }
    // after the revoke: whoever still writes, through any role
    const reportOthers = async (): Promise<void> => {
      for (const u of others)
        if (await stillWrites(u)) {
          const left = plan.some((q) => userKey(q) === userKey(u))
            ? u.roles.filter(
                (r) => !(DB_WRITE_ROLES.includes(r.role) && dbs.includes(r.db)),
              )
            : u.roles;
          report.stillWrite.push(`${userKey(u)} (${roleText(left)})`);
        }
    };
    if (!plan.length) {
      await reportOthers();
      return report;
    }
    // recorded first: a revoke cut short still leaves the record that undoes it (granting a role a user holds changes nothing)
    const col = rt.db().collection<AppWritesRecord>('docstore_fence');
    const prior = (await col.findOne({ _id: APP_WRITES_ID }))?.users ?? [];
    await col.updateOne(
      { _id: APP_WRITES_ID },
      {
        $set: {
          users: [
            ...prior.filter(
              (p) => !plan.some((q) => userKey(q) === userKey(p)),
            ),
            ...plan,
          ],
        },
      },
      { upsert: true },
    );
    for (const u of plan) {
      const on = rt.mongo.db(u.db);
      // read first, so the user never loses its reads
      if (u.granted.length)
        await on.command({ grantRolesToUser: u.user, roles: u.granted });
      await on.command({ revokeRolesFromUser: u.user, roles: u.revoked });
      report.revoked.push(`${userKey(u)}: ${roleText(u.revoked)}`);
    }
    await reportOthers();
  } catch (e) {
    report.error = e instanceof Error ? e.message : String(e);
  }
  return report;
}

/**
 * When MongoDB is made the store again: gives back the write roles revokeAppWrites took and drops the `read` it granted. Returns ''
 * when there was nothing to do or all went well; otherwise what an operator must run, since the application cannot write until then.
 */
export async function restoreAppWrites(rt: MigrationRuntime): Promise<string> {
  const col = rt.db().collection<AppWritesRecord>('docstore_fence');
  const todo: string[] = [];
  try {
    const rec = await col.findOne({ _id: APP_WRITES_ID });
    if (!rec) return '';
    for (const u of rec.users) {
      try {
        const on = rt.mongo.db(u.db);
        await on.command({ grantRolesToUser: u.user, roles: u.revoked });
        if (u.granted.length)
          await on.command({ revokeRolesFromUser: u.user, roles: u.granted });
      } catch {
        todo.push(
          `db.getSiblingDB(${JSON.stringify(u.db)}).grantRolesToUser(${JSON.stringify(u.user)}, ${JSON.stringify(u.revoked)})`,
        );
      }
    }
    if (!todo.length) await col.deleteOne({ _id: APP_WRITES_ID });
  } catch (e) {
    return ` MongoDB's write roles could not be given back (${e instanceof Error ? e.message : String(e)}): read the ${APP_WRITES_ID} document of docstore_fence and grant each user its revoked roles before the application starts.`;
  }
  return todo.length
    ? ` The application's MongoDB users could not get their write roles back: run in mongosh as an administrator, before the application starts: ${todo.join('; ')}`
    : '';
}
