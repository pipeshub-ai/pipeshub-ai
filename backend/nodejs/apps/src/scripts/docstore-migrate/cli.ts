import { loadConfig, redact } from './config';
import { Log, bulk, StoppedForTest } from './bulk';
import { cancelDrain, drain } from './drain';
import { readMongoFence } from './fence';
import { follow, followStart } from './follow';
import { MAPPINGS } from './mapping';
import { migrateOffline } from './offline';
import { cutover, onlineStart, onlineVerify, unfreeze } from './online';
import { preflight } from './preflight';
import { reset } from './reset';
import { MigrationRuntime } from './runtime';
import { postLoad, runMigrateJob } from './schema';
import { JobState } from './state';
import { verifyAll } from './verify';

export interface Args {
  cmd: string;
  sub?: string;
  flags: Map<string, string | true>;
}

export function parse(argv: string[]): Args {
  const [cmd = 'help', ...rest] = argv;
  const flags = new Map<string, string | true>();
  let sub: string | undefined;
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i] as string;
    if (a.startsWith('--')) {
      const next = rest[i + 1];
      if (next !== undefined && !next.startsWith('--')) {
        flags.set(a.slice(2), next);
        i += 1;
      } else flags.set(a.slice(2), true);
    } else sub ??= a;
  }
  return { cmd, ...(sub ? { sub } : {}), flags };
}

/** A command line that cannot be run as written: exit 2, before anything is touched. */
export class UsageError extends Error {}

/** The text operand of `--k`: absent is undefined; a flag without a value, or with an empty one, is refused rather than read as "on". */
export function strFlag(a: Args, k: string): string | undefined {
  if (!a.flags.has(k)) return undefined;
  const v = a.flags.get(k);
  if (typeof v !== 'string' || v.trim() === '')
    throw new UsageError(`--${k} needs a value`);
  return v;
}

/** The number operand of `--k`: finite and within the range of the option, or refused. */
export function numFlag(
  a: Args,
  k: string,
  o: { min: number; max?: number; int?: boolean },
): number | undefined {
  const v = strFlag(a, k);
  if (v === undefined) return undefined;
  const n = Number(v);
  if (
    !Number.isFinite(n) ||
    n < o.min ||
    (o.max !== undefined && n > o.max) ||
    (o.int === true && !Number.isInteger(n))
  )
    throw new UsageError(
      `--${k} must be ${o.int === true ? 'a whole number' : 'a number'} from ${String(o.min)}${o.max !== undefined ? ` to ${String(o.max)}` : ''}, not '${v}'`,
    );
  return n;
}

const COMMANDS = new Set([
  'preflight',
  'schema',
  'bulk',
  'follow',
  'post_load',
  'verify',
  'drain',
  'migrate',
  'cutover',
  'unfreeze',
  'reset',
  'status',
  'reverse',
  'rollback',
]);

/** Everything a command reads from its flags, checked before a connection is opened. */
function plan(a: Args): {
  only?: string[];
  stopAfterBatches?: number;
  idleMs: number;
  sample?: number;
  waitMs: number;
  secs: Record<string, number | undefined>;
  failAt?: string;
} {
  const onlyRaw = strFlag(a, 'only');
  const only = onlyRaw
    ?.split(',')
    .map((x) => x.trim())
    .filter((x) => x !== '');
  if (only?.length === 0) throw new UsageError('--only needs collection names');
  // an unknown name would make bulk and verify do nothing and still succeed
  const unknown = only?.filter(
    (x) => !MAPPINGS.some((m) => m.collection === x),
  );
  if (unknown?.length)
    throw new UsageError(
      `--only names no known collection: ${unknown.join(', ')}`,
    );
  const stop = numFlag(a, 'stop-after-batches', { min: 1, int: true });
  const idle = numFlag(a, 'until-idle', { min: 0 });
  const sample = numFlag(a, 'sample', { min: 0.000001, max: 1 });
  const wait = numFlag(a, 'wait', { min: 0 });
  const secs: Record<string, number | undefined> = {};
  for (const k of ['drain-wait', 'max-freeze', 'grace', 'max-lag'])
    secs[k] = numFlag(a, k, { min: 0 });
  const failAt = strFlag(a, 'fail-at');
  return {
    ...(only ? { only } : {}),
    ...(stop !== undefined ? { stopAfterBatches: stop } : {}),
    idleMs: (idle ?? 0) * 1000,
    ...(sample !== undefined ? { sample } : {}),
    waitMs: (wait ?? 300) * 1000,
    secs,
    ...(failAt !== undefined ? { failAt } : {}),
  };
}

const out = (o: unknown): void =>
  void process.stdout.write(`${JSON.stringify(o, null, 2)}\n`);
const log: Log = (event, data) =>
  void process.stderr.write(
    `${JSON.stringify({ at: new Date().toISOString(), event, ...data })}\n`,
  );

const HELP = `commands: preflight, schema, bulk, follow, post_load, verify, drain, migrate --offline, migrate --online, cutover, unfreeze, reverse, rollback [--reverse|--online], reset, status
--confirm-fence-armed (drain): every application process runs with DOC_STORE_FENCE=on. Without it drain refuses while a connected
  application client does not name its connections pipeshub-fenced, because a process that does not wrap its writes keeps writing
  whatever the fence says.
--allow-clients (migrate --offline, preflight): go on although an application is still connected to MongoDB. WRITES THAT APPLICATION
  MAKES AFTER THE COPY BEGINS CAN BE LOST (they are fenced off or land after the copy read the data). Stop the application instead.`;

/** Exit codes: 0 done, 1 findings (red preflight, diffs, refused), 2 error. */
export async function runCli(
  argv: string[],
  env: NodeJS.ProcessEnv,
): Promise<number> {
  const a = parse(argv);
  if (a.cmd === 'help' || a.flags.has('help')) {
    process.stdout.write(`${HELP}\n`);
    return 0;
  }
  // what can be refused without a server is refused first: no configuration is read and no connection is opened
  if (!COMMANDS.has(a.cmd)) {
    process.stderr.write(`${redact(HELP)}\n`);
    return 2;
  }
  if (a.cmd === 'reset' && !a.flags.has('yes')) {
    process.stderr.write(
      'reset deletes every copied row in PostgreSQL; pass --yes\n',
    );
    return 2;
  }
  if (
    a.cmd === 'migrate' &&
    !a.flags.has('online') &&
    !a.flags.has('offline')
  ) {
    process.stderr.write(
      'use `migrate --offline` (the application is stopped) or `migrate --online` (it keeps running)\n',
    );
    return 2;
  }
  let p: ReturnType<typeof plan>;
  try {
    p = plan(a);
  } catch (e) {
    if (e instanceof UsageError) {
      process.stderr.write(`${e.message}\n`);
      return 2;
    }
    throw e;
  }
  const only = p.only;
  const cfg = loadConfig(env);
  // drain only touches MongoDB: it runs inside the application container, which has no migrate login
  const rt = await MigrationRuntime.open(
    a.cmd === 'drain'
      ? {
          ...cfg,
          pgImportUrl: '',
          pgMigrateUrl: '',
          pgNodeUrl: undefined,
        }
      : cfg,
  );
  try {
    switch (a.cmd) {
      case 'preflight': {
        const r = await preflight(rt, {
          log: (m) => {
            log('preflight', { m });
          },
        });
        out(r);
        return r.ok ? 0 : 1;
      }
      case 'schema': {
        await runMigrateJob(rt, (m) => {
          log('schema', { m });
        });
        out({ ok: true });
        return 0;
      }
      case 'bulk': {
        const js = await JobState.attach(rt);
        try {
          await bulk(rt, js, {
            ...(only ? { only } : {}),
            ...(p.stopAfterBatches !== undefined
              ? { stopAfterBatches: p.stopAfterBatches }
              : {}),
            log,
          });
        } catch (e) {
          if (e instanceof StoppedForTest) {
            out({ ok: false, stopped: e.message });
            return 1;
          }
          throw e;
        }
        out({ ok: true, job: js.id, progress: js.progress });
        return 0;
      }
      case 'follow': {
        const js = await JobState.attach(rt);
        if (a.sub === 'start') {
          await followStart(rt, js);
          out({ ok: true, job: js.id });
          return 0;
        }
        const ac = new AbortController();
        const onSignal = (): void => {
          ac.abort();
        };
        process.once('SIGTERM', onSignal);
        process.once('SIGINT', onSignal);
        try {
          const r = await follow(rt, js, {
            untilIdleMs: p.idleMs,
            stop: ac.signal,
            log,
          });
          out({ ok: true, ...r });
          return 0;
        } finally {
          // a handler left behind would hold the process after the command, and a second command would add another
          process.off('SIGTERM', onSignal);
          process.off('SIGINT', onSignal);
        }
      }
      case 'post_load': {
        const r = await postLoad(rt, (m) => {
          log('post_load', { m });
        });
        out(r);
        return r.ok ? 0 : 1;
      }
      case 'verify': {
        if (a.flags.has('incremental')) {
          const r = await onlineVerify(rt, {
            ...(p.sample !== undefined ? { sample: p.sample } : {}),
            log,
          });
          out(r);
          return r.ok ? 0 : 1;
        }
        const r = await verifyAll(rt, {
          ...(only ? { only } : {}),
          ...(p.sample !== undefined ? { sample: p.sample } : {}),
          log,
        });
        out(r);
        return r.ok ? 0 : 1;
      }
      case 'drain': {
        if (a.flags.has('cancel')) {
          const changed = await cancelDrain(rt);
          out({ ok: true, changed, fence: (await readMongoFence(rt)).mode });
          return 0;
        }
        const r = await drain(rt, {
          waitMs: p.waitMs,
          force: a.flags.has('force'),
          confirmArmed: a.flags.has('confirm-fence-armed'),
          log,
        });
        out(r);
        return r.ok ? 0 : 1;
      }
      case 'migrate': {
        if (a.flags.has('online')) {
          const r = await onlineStart(rt, {
            dryRun: a.flags.has('dry-run'),
            ...(p.sample !== undefined ? { sample: p.sample } : {}),
            log,
          });
          out(r);
          return r.ok ? 0 : 1;
        }
        const r = await migrateOffline(rt, {
          dryRun: a.flags.has('dry-run'),
          allowWriters:
            a.flags.has('allow-clients') || a.flags.has('allow-writers'),
          log,
        });
        out(r);
        return r.ok ? 0 : 1;
      }
      case 'cutover': {
        const secs = (k: string, d: number): number =>
          (p.secs[k] ?? d / 1000) * 1000;
        const r = await cutover(rt, {
          log,
          dryRun: a.flags.has('dry-run'),
          drainWaitMs: secs('drain-wait', 300_000),
          force: a.flags.has('force-drain'),
          maxFreezeMs: secs('max-freeze', 600_000),
          graceMs: secs('grace', 10_000),
          lagMaxMs: secs('max-lag', 5_000),
          fenceArmed: a.flags.has('fence-armed'),
          shadowClean: a.flags.has('shadow-clean'),
          ...(p.failAt !== undefined && env.MIGRATE_TEST_HOOKS === '1'
            ? { failAt: p.failAt }
            : {}),
        });
        out(r);
        return r.ok ? 0 : 1;
      }
      case 'unfreeze': {
        const changed = await unfreeze(rt);
        out({ ok: true, changed, fence: (await readMongoFence(rt)).mode });
        return 0;
      }
      case 'reverse': {
        // loaded when used: the reverse path brings in every Mongoose model of the application, which no other command needs
        const { reverseFollow } = await import('./reverse-follow');
        const js = await JobState.attach(rt);
        const ac = new AbortController();
        const onSignal = (): void => {
          ac.abort();
        };
        process.once('SIGTERM', onSignal);
        process.once('SIGINT', onSignal);
        try {
          const r = await reverseFollow(rt, js, {
            untilIdleMs: p.idleMs,
            stop: ac.signal,
            log,
          });
          out({ ok: true, ...r });
          return 0;
        } finally {
          process.off('SIGTERM', onSignal);
          process.off('SIGINT', onSignal);
        }
      }
      case 'rollback': {
        if (a.flags.has('online')) {
          const { rollbackOnline } = await import('./reverse-follow');
          const r = await rollbackOnline(rt, {
            log,
            maxFreezeMs: (p.secs['max-freeze'] ?? 900) * 1000,
            ...(p.sample !== undefined ? { sample: p.sample } : {}),
          });
          out(r);
          return r.ok ? 0 : 1;
        }
        const { rollback } = await import('./reverse');
        const r = await rollback(rt, {
          reverse: a.flags.has('reverse'),
          allowWriters:
            a.flags.has('allow-clients') || a.flags.has('allow-writers'),
          log,
        });
        out(r);
        return r.ok ? 0 : 1;
      }
      case 'reset': {
        const r = await reset(rt, { force: a.flags.has('force') });
        out(r);
        return r.ok ? 0 : 1;
      }
      case 'status': {
        const j = await JobState.latest(rt);
        // the import login is open only while a move runs; open with no job running means a script was cut short
        const open = (
          await rt.migratePool.query<{ b: boolean }>(
            "SELECT rolcanlogin AS b FROM pg_roles WHERE rolname = 'pipeshub_import'",
          )
        ).rows[0]?.b;
        const warn =
          open && j?.status !== 'running'
            ? {
                warning:
                  'the import login (pipeshub_import) can log in and no move is running: run scripts/migrate-to-postgres.sh or scripts/rollback-to-mongo.sh to close it, or ALTER ROLE pipeshub_import NOLOGIN',
              }
            : {};
        out(j ? { ...j, ...warn } : { job: null, ...warn });
        return 0;
      }
      default:
        process.stderr.write(`${redact(HELP)}\n`);
        return 2;
    }
  } finally {
    await rt.close();
  }
}
