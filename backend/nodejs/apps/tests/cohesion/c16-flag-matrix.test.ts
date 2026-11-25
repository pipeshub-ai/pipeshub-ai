import { expect } from 'chai';
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative } from 'node:path';
import { PLATFORM_FEATURE_FLAGS as SHARED_PLATFORM_FEATURE_FLAGS } from '../../src/modules/configuration_manager/constants/constants';
import { EDITION_PLATFORM_FEATURE_FLAGS } from '../../src/edition';
import { configPaths } from '../../src/modules/configuration_manager/paths/paths';
import { describeAgentHarnessReadiness } from '../../src/modules/agent-harness/config';
import { ENV_FLAG_DEFAULTS } from '../../src/libs/utils/env-flags';
import {
  flagMatrix,
  matrixFlag,
  REPO_ROOT,
} from '../modules/agent-harness/support/flag-matrix';

// The build's Labs registry: the shared flags and the edition's own (src/edition.ts).
const PLATFORM_FEATURE_FLAGS = [...SHARED_PLATFORM_FEATURE_FLAGS, ...EDITION_PLATFORM_FEATURE_FLAGS];

/**
 * C-16 (F3), Node half: every environment variable, Labs flag and
 * `/services/agentHarness` document the agent-harness module reads is in
 * `contracts/harness/flags.json` (as a flag, or as a setting that is not one),
 * every Node reader the matrix names reads it, and the code's defaults are
 * the matrix's. The Python half is backend/python/tests/cohesion/test_c16_flag_matrix.py.
 */

const HARNESS_SRC = join(__dirname, '../../src/modules/agent-harness');
// CPD-9: the control plane reads its own switches; they are matrix flags like the harness's.
const CONTROL_PLANE_SRC = join(__dirname, '../../src/modules/control-plane');
// The harness's and the control plane's Mongo repositories are theirs too.
const MATRIX_SRCS = [
  HARNESS_SRC,
  CONTROL_PLANE_SRC,
  join(__dirname, '../../src/libs/repositories/agent-harness'),
  join(__dirname, '../../src/libs/repositories/control-plane'),
];
// Generated release data quotes tool descriptions, which may mention env names.
const CONTROL_PLANE_DATA = join(CONTROL_PLANE_SRC, 'contracts');
const SRC = join(__dirname, '../../src');
const FLAG_HELPER = join(SRC, 'libs/utils/env-flags.ts');
const FLAG_READ = /\benvFlag\(\s*['"]([A-Z][A-Z0-9_]*)['"]/g;
const ENV_READ =
  /\b(?:env|environment|process\.env)\s*(?:\?\.|\.)\s*([A-Z][A-Z0-9_]*)\b|\b(?:env|environment|process\.env)\s*\[\s*['"]([A-Z][A-Z0-9_]*)['"]\s*\]/g;

const sourceFiles = (directory: string): string[] =>
  readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return name.endsWith('.ts') ? [path] : [];
  });

const repoPath = (file: string): string => relative(REPO_ROOT, file);

// H6 FM-E4: a name held in a constant (`const X_ENV = 'NAME'`, then `env[X_ENV]`) is a read too.
const NAME_CONSTANT = /\bconst\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*['"]([A-Z][A-Z0-9_]*)['"]/g;
const ENV_READ_BY_CONSTANT =
  /\b(?:env|environment|process\.env)\s*(?:\?\.)?\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*\]/g;
// The same namespaces as the Python half, CPD-0's operator-only names included.
const HARNESS_ENV =
  /^(?:(?:AGENT_HARNESS|CONTROL_PLANE|HARNESS|SANDBOX|EGRESS|MODEL_GATEWAY|TOOL_GATEWAY|PIPESHUB_CODE|MCP_HUB|MCP_STDIO|MCP_ALLOW|PIPESHUB_SKILLS)_|PIPESHUB_AGENT_TRANSPORT$|PIPESHUB_ENABLE_(?:SKILLS|CODE_EXECUTION)$)/;

const namesReadIn = (text: string): Set<string> => {
  const names = new Set<string>();
  for (const match of text.matchAll(ENV_READ)) names.add(match[1] ?? match[2]!);
  for (const match of text.matchAll(FLAG_READ)) names.add(match[1]!);
  const constants = new Map(
    [...text.matchAll(NAME_CONSTANT)].map((m) => [m[1]!, m[2]!] as const),
  );
  for (const match of text.matchAll(ENV_READ_BY_CONSTANT)) {
    const name = constants.get(match[1]!);
    if (name !== undefined) names.add(name);
  }
  return names;
};

/** File (relative to the repository) -> env names it reads, over the harness module and the `files` given. */
const nodeReads = (files: readonly string[] = []): Map<string, Set<string>> => {
  const reads = new Map<string, Set<string>>();
  const scanned = MATRIX_SRCS.flatMap(sourceFiles).filter((f) => !f.startsWith(CONTROL_PLANE_DATA));
  for (const file of new Set([...scanned, ...files])) {
    const names = namesReadIn(readFileSync(file, 'utf8'));
    if (names.size > 0) reads.set(repoPath(file), names);
  }
  return reads;
};

/** Harness-namespace env names read anywhere in src, outside the harness module. */
const wideReads = (): Map<string, Set<string>> => {
  const reads = new Map<string, Set<string>>();
  for (const file of sourceFiles(SRC)) {
    if (MATRIX_SRCS.some((root) => file.startsWith(root))) continue;
    const names = [...namesReadIn(readFileSync(file, 'utf8'))].filter((n) =>
      HARNESS_ENV.test(n),
    );
    if (names.length > 0) reads.set(repoPath(file), new Set(names));
  }
  return reads;
};

const envFlags = () => flagMatrix().flags.filter((f) => f.kind === 'env');
const settings = (kind: string) =>
  new Set(
    flagMatrix()
      .settings.filter((s) => s.kind === kind)
      .map((s) => s.name),
  );

describe('C-16 flag matrix (Node)', () => {
  it('FM-3 every env name the harness module reads is a matrix flag or setting', () => {
    const known = new Set([
      ...envFlags().map((f) => f.name),
      ...settings('env'),
    ]);
    const missing = [...nodeReads()].flatMap(([file, names]) =>
      [...names].filter((n) => !known.has(n)).map((n) => `${n} (${file})`),
    );
    expect(missing).to.deep.equal([]);
  });

  it('FM-E4 a harness-namespace env name read anywhere in src is a matrix flag or setting', () => {
    const known = new Set([
      ...envFlags().map((f) => f.name),
      ...settings('env'),
    ]);
    const missing = [...wideReads()].flatMap(([file, names]) =>
      [...names].filter((n) => !known.has(n)).map((n) => `${n} (${file})`),
    );
    expect(missing).to.deep.equal([]);
  });

  it('FM-E4 a read through a named constant is seen', () => {
    const listener = [...nodeReads()].find(([file]) =>
      file.endsWith('transport/internal-listener.ts'),
    );
    expect([...(listener?.[1] ?? [])].sort()).to.deep.equal([
      'AGENT_HARNESS_INTERNAL_HEADERS_TIMEOUT_MS',
      'AGENT_HARNESS_INTERNAL_HOST',
      'AGENT_HARNESS_INTERNAL_MAX_CONNECTIONS',
      'AGENT_HARNESS_INTERNAL_PORT',
      'AGENT_HARNESS_INTERNAL_REQUEST_TIMEOUT_MS',
      // The native bundle's loopback: the listener binds the process's one address unless told otherwise.
      'PIPESHUB_BIND_HOST',
      // R51-COMPAT: the listener opens in broker mode with the harness off (internalListenerWanted).
      'SANDBOX_MODE',
    ]);
  });

  it('FM-3 the Node readers the matrix names are exactly the files that read each flag', () => {
    // A reader the matrix names outside the harness module (an edition's overlay may) is read too, not taken on trust.
    const named = envFlags()
      .flatMap((flag) => flag.readers.node ?? [])
      .map((file) => join(REPO_ROOT, file))
      .filter((file) => existsSync(file));
    const reads = nodeReads(named);
    for (const flag of envFlags()) {
      const readers = [...reads]
        .filter(([, names]) => names.has(flag.name))
        .map(([file]) => file)
        .sort();
      expect(readers, flag.name).to.deep.equal(
        [...(flag.readers.node ?? [])].sort(),
      );
    }
  });

  it('FM-3 a named reader outside the harness module is scanned like one inside it', () => {
    const dir = mkdtempSync(join(process.env.TMPDIR ?? tmpdir(), 'fm3-'));
    try {
      const reader = join(dir, 'reader.ts');
      writeFileSync(reader, "export const on = process.env.AGENT_HARNESS_FM3_PROBE === '1';\n");
      expect([...(nodeReads([reader]).get(repoPath(reader)) ?? [])]).to.deep.equal(['AGENT_HARNESS_FM3_PROBE']);
      expect(nodeReads().has(repoPath(reader))).to.equal(false);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('FM-4 every matrix Labs flag is registered with the matrix default', () => {
    for (const flag of flagMatrix().flags.filter((f) => f.kind === 'labs')) {
      const registered = PLATFORM_FEATURE_FLAGS.find((f) => f.key === flag.name);
      expect(registered, flag.name).to.not.equal(undefined);
      expect(!!registered!.defaultEnabled, flag.name).to.equal(flag.default);
    }
  });

  it('FM-4 every registered Labs flag the harness module names is in the matrix', () => {
    const labs = new Set(
      flagMatrix()
        .flags.filter((f) => f.kind === 'labs')
        .map((f) => f.name),
    );
    const text = sourceFiles(HARNESS_SRC)
      .map((file) => readFileSync(file, 'utf8'))
      .join('\n');
    const named = PLATFORM_FEATURE_FLAGS.map((f) => f.key).filter((key) =>
      new RegExp(`['"]${key}['"]`).test(text),
    );
    expect(named.length).to.be.greaterThan(0);
    expect(named.filter((key) => !labs.has(key))).to.deep.equal([]);
  });

  it('FM-5 every agentHarness configuration path is a matrix flag or setting', () => {
    const known = new Set([
      ...flagMatrix()
        .flags.map((f) => f.path)
        .filter((p): p is string => p !== undefined),
      ...settings('config'),
    ]);
    const paths = Object.values(configPaths.agentHarness);
    expect(paths.filter((p) => !known.has(p))).to.deep.equal([]);
    expect(known.has(configPaths.platform.settings)).to.equal(true);
  });

  it('FD-1 the readiness defaults are the matrix defaults', () => {
    const readiness = describeAgentHarnessReadiness({}, null);
    expect(readiness.transactions).to.equal(
      matrixFlag('REPLICA_SET_AVAILABLE').default,
    );
    expect(readiness.relay).to.equal(
      matrixFlag('AGENT_HARNESS_RELAY_ENABLED').default,
    );
    expect(readiness.worker).to.equal(
      matrixFlag('AGENT_HARNESS_WORKER_ENABLED').default,
    );
    expect(readiness.ready).to.equal(false);
  });

  it('FM-8 every boolean flag Node reads goes through envFlag, anywhere in src', () => {
    const booleans = new Set(
      envFlags()
        .filter((f) => (f as { parser?: string }).parser === 'env_flag')
        .map((f) => f.name),
    );
    const raw = sourceFiles(SRC)
      .filter((file) => file !== FLAG_HELPER)
      .flatMap((file) =>
        [...readFileSync(file, 'utf8').matchAll(ENV_READ)]
          .map((m) => m[1] ?? m[2]!)
          .filter((name) => booleans.has(name))
          .map((name) => `${name} (${repoPath(file)})`),
      );
    expect(raw).to.deep.equal([]);
  });

  it('FM-8 the Node helper defaults are the matrix defaults', () => {
    const node = Object.fromEntries(
      envFlags()
        .filter(
          (f) =>
            (f as { parser?: string }).parser === 'env_flag' &&
            (f.readers.node ?? []).length > 0,
        )
        .map((f) => [f.name, f.default]),
    );
    expect(node).to.deep.equal(ENV_FLAG_DEFAULTS);
  });

  it('FM-9 configuration reads in the harness module go only through the audited readers', () => {
    // Each propagates a key-value store error (fail closed); F3's doc records why for each.
    const audited = [
      // A1: the configured models an engine run pins concretely; no catch (a store error refuses admission).
      'adapters/configuration/ai-models-configured-models.ts',
      'adapters/configuration/ai-models-family-resolver.ts',
      'adapters/configuration/encrypted-config-document-store.ts',
      // CPD-6: what a pin reads (configured models, Labs stages); no catch (a store error fails the pin closed).
      'adapters/configuration/platform-pin-sources.ts',
      'adapters/configuration/platform-rollout-settings.ts',
      // R38IDENT-FU-9: applies the etcd roles through the store's auth admin, reads no configuration; an error reaches
      // the publisher, which logs it and leaves auth off.
      'adapters/etcd/etcd-component-roles.ts',
      // KEYRING: the SECRET_KEY rotation re-encrypts the KV store's values; a store error stops the run, which is resumable.
      'application/secret-key-rotation-steps.ts',
      'composition.ts',
      // ADR-OPS-01: names the settings store an edition's rollout stops are made from; reads none itself.
      'edition.ts',
      // CPD-7: the capability CLI opens the KV store for the credential vault (a removed installation's bindings are revoked there); no catch.
      'ops/capability-migration-cli.ts',
      // CPD-4: the migration CLI opens the KV store for the encrypted document stores; no catch.
      // KEYRING: the rotation CLI opens the KV store for the configuration step; no catch.
      'ops/rotate-secret-key-cli.ts',
    ];
    const touching = sourceFiles(HARNESS_SRC)
      .filter((file) =>
        // An org's settings are read through OrgSettings.of(orgId) (ORGSET-2), still a configuration read.
        /KeyValueStoreService|ConfigKeyValueStore|keyValueStore|[sS]ettings\.of\(/.test(
          readFileSync(file, 'utf8'),
        ),
      )
      .map((file) => relative(HARNESS_SRC, file))
      .sort();
    expect(touching).to.deep.equal(audited);
    const models = readFileSync(join(HARNESS_SRC, audited[0]!), 'utf8');
    expect(models).not.to.match(/catch\s*[({]/);
    const store = readFileSync(join(HARNESS_SRC, audited[2]!), 'utf8');
    expect(store).not.to.match(/catch\s*[({]/);
    // P2.4b/CPD-8: the credential vault's envelope (model accounts, bindings); its one catch turns a store error
    // into the caller's unavailable error (503), never "absent".
    const vault = readFileSync(join(HARNESS_SRC, '../../libs/vault/versioned-secret-documents.ts'), 'utf8');
    expect(vault.match(/catch\s*[({]/g) ?? []).to.have.length(1);
    expect(vault).to.match(/catch \{\s*throw this\.unavailable\(/);
  });

  it('FM-1 the Node readers the matrix names exist', () => {
    for (const flag of flagMatrix().flags) {
      for (const file of flag.readers.node ?? []) {
        expect(statSync(join(REPO_ROOT, file)).isFile(), file).to.equal(true);
      }
    }
  });
});
