/**
 * Rotating `SECRET_KEY` (ADR-KEY-01; the procedure is in docs/agent-harness-v2/guides/runbooks.md):
 *
 *   node dist/modules/agent-harness/ops/rotate-secret-key-cli.js dry-run|rotate|verify|retire \
 *     [--mongo-uri <uri> --db <name>] [--python <exe>] [--python-dir <dir>] [--accept-unreadable <n>]
 *
 * Run it where the Node service runs, with the same environment: `SECRET_KEY` the new key and
 * `SECRET_KEY_PREVIOUS` the old one, the KV store's settings, and DOC_STORE's (`--mongo-uri` and `--db` on MongoDB).
 *
 * - `dry-run` counts, per step, what is under each key and writes nothing.
 * - `rotate` takes the rotation lock, then: rewraps every org data key, reseals the egress proxy's state (the Python
 *   step, `python -m app.harness.sandbox.egress_rekey`), reseals connector sync points' delta links (the Python step
 *   `python -m app.connectors.core.base.sync_point.rekey`, through the graph provider), re-encrypts the configuration
 *   store and every OAuth app's client secret, and checks again. It refuses to start while anything opens under neither key (a wrong
 *   `SECRET_KEY_PREVIOUS`), unless `--accept-unreadable` names exactly how many. Stopped anywhere, run it again.
 * - `verify` exits 0 only when nothing is left under the previous key.
 * - `retire` exits 0 only when nothing needs the previous key any more: remove `SECRET_KEY_PREVIOUS` then.
 *
 * Prints one JSON line per step's progress and a final result line; counts only, never a key, digest or value.
 * Exits 0 on success, 1 when the mode's check fails or the run is refused, 2 on a usage error.
 */
import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import {
  needsMongoOptions,
  openCliDocStore,
  type CliDocStore,
} from './cli-doc-store';
import {
  EgressStateRekeyStep,
  SyncPointRekeyStep,
  type PythonCommand,
} from './egress-rekey-step';
import {
  SecretKeyRotation,
  type RingState,
  type RotationMode,
  type RotationResult,
  type RotationStep,
  type StepReport,
} from '../application/secret-key-rotation';
import {
  ConfigStoreReencryptStep,
  DataKeyRewrapStep,
  OAuthClientSecretStep,
  type ForOrg,
} from '../application/secret-key-rotation-steps';
import { platformKeyEncryptionKey } from '../adapters/crypto/hkdf-key-encryption-key';
import { createDataKeyStore } from '../../../libs/repositories/agent-harness/data-protection';
import { createLeaderLease } from '../../../libs/repositories/agent-harness/leader-lease';
import { activeOrgIds } from '../../../libs/repositories/agent-harness/active-orgs';
import { getOAuthAppRepository } from '../../../libs/repositories/oauth-app';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import {
  secretKeyRing,
  writerKdf,
  type SecretKeyRing,
} from '../../../libs/encryptor/secret-key-ring';
import { loadConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { forEnumeratedOrg } from '../../../libs/context/orgContext';

const MODES: ReadonlySet<string> = new Set([
  'dry-run',
  'rotate',
  'verify',
  'retire',
]);
const FLAGS: ReadonlySet<string> = new Set([
  'mongo-uri',
  'db',
  'python',
  'python-dir',
  'accept-unreadable',
]);

export interface RotateCliOptions {
  mode: RotationMode;
  options: Record<string, string>;
  acceptUnreadable: number | undefined;
}

export function parseRotateArgs(argv: readonly string[]): RotateCliOptions {
  const [command = '', ...rest] = argv;
  if (!MODES.has(command))
    throw new Error('the command is dry-run, rotate, verify or retire');
  const options: Record<string, string> = {};
  for (let i = 0; i < rest.length; i += 2) {
    const key = rest[i] ?? '';
    const value = rest[i + 1];
    if (
      !key.startsWith('--') ||
      !FLAGS.has(key.slice(2)) ||
      value === undefined
    ) {
      throw new Error(`bad argument ${key}`);
    }
    options[key.slice(2)] = value;
  }
  let acceptUnreadable: number | undefined;
  if (options['accept-unreadable'] !== undefined) {
    acceptUnreadable = Number(options['accept-unreadable']);
    if (!Number.isInteger(acceptUnreadable) || acceptUnreadable < 1) {
      throw new Error(
        '--accept-unreadable takes the count the last run reported',
      );
    }
  }
  return { mode: command as RotationMode, options, acceptUnreadable };
}

export interface RotateCliBackend {
  rotation(
    parsed: RotateCliOptions,
    onProgress: (report: StepReport) => void,
  ): SecretKeyRotation;
  close(): Promise<void>;
}

/**
 * Which keys the rotation may read, by role. A rotate with no previous key moves SHA-256-sealed values to the HKDF
 * key (SEC-D-05) only where writers seal with HKDF; elsewhere there is nothing to move, and it is refused as a
 * rotation without SECRET_KEY_PREVIOUS.
 */
export function rotationRing(
  ring: SecretKeyRing,
  env: Readonly<Record<string, string | undefined>> = process.env,
): RingState {
  return {
    other: ring.other?.role ?? null,
    cipherMove: writerKdf(env) === 'hkdf',
  };
}

const bound: ForOrg = (orgId, fn) =>
  forEnumeratedOrg(orgId, 'secret-key-rotation', fn);

/** The deployment's stores and keys, as the Node service opens them. */
export async function connectRotation(
  parsed: RotateCliOptions,
): Promise<RotateCliBackend> {
  // Read first: a ring the services would refuse is refused here before anything is opened.
  const ring = secretKeyRing();
  const store: CliDocStore = await openCliDocStore('rotate-secret-key-cli', {
    uri: parsed.options['mongo-uri'],
    db: parsed.options['db'],
  });
  const config = loadConfigurationManagerConfig();
  const kv = KeyValueStoreService.getInstance(config);
  await kv.connect();
  const cipher = EncryptionService.getInstance(
    config.algorithm,
    config.secretKey,
  );
  const pythonDir =
    parsed.options['python-dir'] ??
    (existsSync('/app/python') ? '/app/python' : process.cwd());
  const python: PythonCommand = {
    executable: parsed.options['python'] ?? 'python',
    cwd: pythonDir,
  };
  const steps: RotationStep[] = [
    new DataKeyRewrapStep(
      createDataKeyStore(),
      platformKeyEncryptionKey(),
      bound,
    ),
    new EgressStateRekeyStep(python),
    new SyncPointRekeyStep(python),
    new ConfigStoreReencryptStep(kv, cipher),
    new OAuthClientSecretStep(
      getOAuthAppRepository(),
      activeOrgIds,
      cipher,
      bound,
    ),
  ];
  return {
    rotation: (options, onProgress) =>
      new SecretKeyRotation({
        steps,
        ring: rotationRing(ring),
        lease: createLeaderLease(),
        holder: `rotate-secret-key:${randomUUID()}`,
        ...(options.acceptUnreadable !== undefined
          ? { acceptUnreadable: options.acceptUnreadable }
          : {}),
        onProgress,
      }),
    close: async () => {
      await kv.disconnect();
      await store.close();
    },
  };
}

const resultLine = (result: RotationResult): string =>
  JSON.stringify({
    event: 'result',
    mode: result.mode,
    ok: result.ok,
    refused: result.refused,
    steps: result.steps,
    ...(result.ok && result.mode === 'retire'
      ? {
          next: 'remove SECRET_KEY_PREVIOUS from every service and restart them',
        }
      : {}),
  });

export async function runRotateSecretKeyCli(
  argv: readonly string[],
  connect: (
    parsed: RotateCliOptions,
  ) => Promise<RotateCliBackend> = connectRotation,
  out: (line: string) => void = (line) => process.stdout.write(`${line}\n`),
  err: (line: string) => void = (line) => process.stderr.write(`${line}\n`),
): Promise<number> {
  let parsed: RotateCliOptions;
  try {
    parsed = parseRotateArgs(argv);
    if (
      needsMongoOptions() &&
      (!parsed.options['mongo-uri'] || !parsed.options['db'])
    ) {
      throw new Error('missing --mongo-uri, --db');
    }
  } catch (error) {
    err(error instanceof Error ? error.message : String(error));
    return 2;
  }
  let backend: RotateCliBackend;
  try {
    backend = await connect(parsed);
  } catch (error) {
    // The ring's and the stores' own messages name variables and hosts, never a key.
    err(error instanceof Error ? error.message : String(error));
    return 1;
  }
  try {
    const rotation = backend.rotation(parsed, (report) =>
      out(JSON.stringify({ event: 'progress', ...report })),
    );
    const result = await rotation.run(parsed.mode);
    out(resultLine(result));
    if (result.refused !== null) err(result.refused);
    return result.ok ? 0 : 1;
  } catch (error) {
    err(error instanceof Error ? error.message : String(error));
    return 1;
  } finally {
    await backend.close();
  }
}

if (require.main === module) {
  runRotateSecretKeyCli(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (error: unknown) => {
      process.stderr.write(
        `${error instanceof Error ? error.message : String(error)}\n`,
      );
      process.exit(1);
    },
  );
}
