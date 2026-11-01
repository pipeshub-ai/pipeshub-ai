import { loadConfig, redact } from './config';
import { Log, bulk, StoppedForTest } from './bulk';
import { cancelDrain, drain } from './drain';
import { readMongoFence } from './fence';
import { follow, followStart } from './follow';
import { migrateOffline } from './offline';
import { cutover, onlineStart, onlineVerify, unfreeze } from './online';
import { reverseFollow, rollbackOnline } from './reverse-follow';
import { preflight } from './preflight';
import { reset } from './reset';
import { rollback } from './reverse';
import { MigrationRuntime } from './runtime';
import { postLoad, runMigrateJob } from './schema';
import { JobState } from './state';
import { verifyAll } from './verify';

interface Args {
  cmd: string;
  sub?: string;
  flags: Map<string, string | true>;
}

function parse(argv: string[]): Args {
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

const out = (o: unknown): void =>
  void process.stdout.write(`${JSON.stringify(o, null, 2)}\n`);
const log: Log = (event, data) =>
  void process.stderr.write(
    `${JSON.stringify({ at: new Date().toISOString(), event, ...data })}\n`,
  );

const HELP = `commands: preflight, schema, bulk, follow, post_load, verify, drain, migrate --offline, migrate --online, cutover, unfreeze, reverse, rollback [--reverse|--online], reset, status
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
  const only =
    typeof a.flags.get('only') === 'string'
      ? String(a.flags.get('only')).split(',')
      : undefined;
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
        const stop = a.flags.get('stop-after-batches');
        try {
          await bulk(rt, js, {
            ...(only ? { only } : {}),
            ...(typeof stop === 'string'
              ? { stopAfterBatches: Number(stop) }
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
        const idle =
          typeof a.flags.get('until-idle') === 'string'
            ? Number(a.flags.get('until-idle')) * 1000
            : 0;
        const ac = new AbortController();
        process.once('SIGTERM', () => {
          ac.abort();
        });
        process.once('SIGINT', () => {
          ac.abort();
        });
        const r = await follow(rt, js, {
          untilIdleMs: idle,
          stop: ac.signal,
          log,
        });
        out({ ok: true, ...r });
        return 0;
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
          const sample = a.flags.get('sample');
          const r = await onlineVerify(rt, {
            ...(typeof sample === 'string' ? { sample: Number(sample) } : {}),
            log,
          });
          out(r);
          return r.ok ? 0 : 1;
        }
        const sample = a.flags.get('sample');
        const r = await verifyAll(rt, {
          ...(only ? { only } : {}),
          ...(typeof sample === 'string' ? { sample: Number(sample) } : {}),
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
        const wait =
          typeof a.flags.get('wait') === 'string'
            ? Number(a.flags.get('wait')) * 1000
            : 300_000;
        const r = await drain(rt, {
          waitMs: wait,
          force: a.flags.has('force'),
          log,
        });
        out(r);
        return r.ok ? 0 : 1;
      }
      case 'migrate': {
        if (a.flags.has('online')) {
          const sample = a.flags.get('sample');
          const r = await onlineStart(rt, {
            dryRun: a.flags.has('dry-run'),
            ...(typeof sample === 'string' ? { sample: Number(sample) } : {}),
            log,
          });
          out(r);
          return r.ok ? 0 : 1;
        }
        if (!a.flags.has('offline')) {
          process.stderr.write(
            'use `migrate --offline` (the application is stopped) or `migrate --online` (it keeps running)\n',
          );
          return 2;
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
        const secs = (k: string, d: number): number => {
          const v = a.flags.get(k);
          return typeof v === 'string' ? Number(v) * 1000 : d;
        };
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
          ...(typeof a.flags.get('fail-at') === 'string' &&
          env.MIGRATE_TEST_HOOKS === '1'
            ? { failAt: String(a.flags.get('fail-at')) }
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
        const js = await JobState.attach(rt);
        const idle =
          typeof a.flags.get('until-idle') === 'string'
            ? Number(a.flags.get('until-idle')) * 1000
            : 0;
        const ac = new AbortController();
        process.once('SIGTERM', () => {
          ac.abort();
        });
        process.once('SIGINT', () => {
          ac.abort();
        });
        const r = await reverseFollow(rt, js, {
          untilIdleMs: idle,
          stop: ac.signal,
          log,
        });
        out({ ok: true, ...r });
        return 0;
      }
      case 'rollback': {
        if (a.flags.has('online')) {
          const sample = a.flags.get('sample');
          const max = a.flags.get('max-freeze');
          const r = await rollbackOnline(rt, {
            log,
            maxFreezeMs: typeof max === 'string' ? Number(max) * 1000 : 900_000,
            ...(typeof sample === 'string' ? { sample: Number(sample) } : {}),
          });
          out(r);
          return r.ok ? 0 : 1;
        }
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
        if (!a.flags.has('yes')) {
          process.stderr.write(
            'reset deletes every copied row in PostgreSQL; pass --yes\n',
          );
          return 2;
        }
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
