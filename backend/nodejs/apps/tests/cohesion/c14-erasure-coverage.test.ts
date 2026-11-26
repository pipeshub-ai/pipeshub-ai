import 'reflect-metadata';
import { expect } from 'chai';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  AGENT_DATA_STORES,
  ERASURE_STEPS,
  PRINCIPAL_DATA_STORES,
  SESSION_DATA_STORES,
} from '../../src/modules/agent-harness/domain/session-erasure';
import { ModelAccountVault } from '../../src/modules/agent-harness/adapters/configuration/model-account-vault';
import { vaultPath } from '../../src/modules/agent-harness/domain/model-accounts';
import { ModelAccountEraser } from '../../src/modules/agent-harness/ports/model-accounts';
import { ScheduleEraser } from '../../src/modules/agent-harness/ports/schedules';
import { InMemoryScheduleStore } from '../../src/modules/agent-harness/adapters/in-memory-schedules';
import { MemoryEraser } from '../../src/modules/agent-harness/ports/memory-store';
import type { EngineSignInEraser } from '../../src/modules/agent-harness/ports/engine-signins';
import { engineSignInEraser } from '../../src/modules/agent-harness/application/engine-signin-service';
import { ConnectorPersonalInstanceEraser } from '../../src/modules/agent-harness/adapters/connector-personal-instances';
import type { PersonalInstanceEraser } from '../../src/modules/agent-harness/ports/personal-instances';
import { InMemoryEngineSignIns } from '../../src/modules/agent-harness/adapters/in-memory-engine-signins';
import { InMemoryMemoryStore } from '../../src/modules/agent-harness/adapters/in-memory-memory-store';
import { InMemoryInboxStore } from '../../src/modules/agent-harness/adapters/in-memory-inbox-store';
import type { InboxEraser } from '../../src/modules/agent-harness/ports/inbox';
import { MongoMemoryStore } from '../../src/libs/repositories/agent-harness/memory/mongo';
import { MongoScheduleStore } from '../../src/libs/repositories/agent-harness/schedules/mongo';
import { KEYS, MemoryKv, platformCipher } from '../modules/agent-harness/support/model-accounts';
import { createErasureSteps } from '../../src/modules/agent-harness/application/session-erasure-steps';
import { SessionErasureRunner } from '../../src/modules/agent-harness/application/session-erasure';
import { InMemoryErasureJobStore } from '../../src/modules/agent-harness/adapters/in-memory-session-erasure';
import { ORG_STORE_COVERAGE, UNMODELLED_COLLECTIONS } from '../../src/modules/agent-harness/domain/principal-erasure';
import { SESSION_BACKUP_COVERAGE } from '../../src/modules/agent-harness/domain/session-backup';
import { SESSION_BACKUP_SELECTED } from '../../src/libs/repositories/agent-harness/session-backup/mongo';

/** Erasure in these tests runs over no conversation document (E7B's guard reads 'absent'). */
const NO_CONVERSATION = { state: () => Promise.resolve('absent' as const) };

/**
 * C-14 (EXECUTION-PLAN §4): session erasure reaches every store a session's
 * data is written to. Every collection the harness and the conversation
 * module define must be listed in SESSION_DATA_STORES with the erasure step
 * that covers it, or with why it is kept; a collection a later phase adds
 * without that fails here. The replica-set half (C-14b: every listed store is
 * populated, erased, and another session's left alone) is in
 * tests/modules/agent-harness/session-erasure.mongo.itest.ts; the census
 * that scans every store after the composed worker erased a session (QA-15)
 * is erasure-census.mongo.itest.ts. The order of the deletion paths is held
 * by behaviour, not source text: the E7B guard by QA15-3, the user, org and
 * service-account hooks by QA15-10, agent deletion (H2, H4, H5) by
 * agent-erasure.mongo.itest.ts and agent-erasure-crash.test.ts.
 */

const SRC = join(__dirname, '../../src');
const MODEL_DIRECTORIES = ['modules/enterprise_search/schema', 'modules/enterprise_search/citations'];
/** The harness's and the control plane's Mongo repositories: their models are in each store's `schema/`. */
const REPOSITORY_ROOTS = ['libs/repositories/agent-harness', 'libs/repositories/control-plane'];
const MODEL_FILES = [
  'libs/repositories/outbox/schema/outbox.schema.ts',
  'modules/notification/schema/notification.schema.ts',
];

interface ModelLike {
  modelName: string;
  collection: { collectionName: string };
}

const isModel = (value: unknown): value is ModelLike =>
  typeof value === 'function' &&
  typeof (value as Partial<ModelLike>).modelName === 'string' &&
  typeof (value as Partial<ModelLike>).collection?.collectionName === 'string';

/** Every collection defined by a model file of the session-owning modules. */
function definedCollections(): Set<string> {
  const files = [
    ...MODEL_DIRECTORIES.flatMap((directory) =>
      readdirSync(join(SRC, directory))
        .filter((name) => /\.(models|schema)\.ts$/.test(name))
        .map((name) => join(SRC, directory, name)),
    ),
    ...REPOSITORY_ROOTS.flatMap((root) => sourcesUnder(join(SRC, root)).filter((file) => file.endsWith('.schema.ts'))),
    ...MODEL_FILES.map((file) => join(SRC, file)),
  ];
  // Held by an install though no model here defines them: still inventoried, so still erased.
  const collections = new Set<string>(UNMODELLED_COLLECTIONS);
  for (const file of files) {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- the files are discovered at run time
    const exported = require(file) as Record<string, unknown>;
    for (const value of Object.values(exported)) {
      if (isModel(value)) collections.add(value.collection.collectionName);
    }
  }
  return collections;
}

/** Every `.ts` file under `directory`, recursively. */
const sourcesUnder = (directory: string): string[] =>
  readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return entry.name === 'generated' ? [] : sourcesUnder(path);
    return entry.name.endsWith('.ts') ? [path] : [];
  });

/**
 * Collections the session-owning modules and the control plane open by name,
 * without a model file: a `collection: '…'` option, a `.collection('…')`
 * call, or a `.collection(NAME)` whose `const NAME = '…'` is in one of them.
 */
function rawCollections(): Set<string> {
  const files = RAW_DIRECTORIES.flatMap((directory) => sourcesUnder(join(SRC, directory)));
  const texts = files.map((file) => readFileSync(file, 'utf8'));
  const constants = new Map<string, string>();
  for (const text of texts) {
    for (const [, name, value] of text.matchAll(/const ([A-Z][A-Z0-9_]*)\s*=\s*'([A-Za-z_][\w-]*)'/g)) {
      constants.set(name!, value!);
    }
  }
  const found = new Set<string>();
  for (const text of texts) {
    for (const [, literal] of text.matchAll(/collection: '([A-Za-z_][\w-]*)'/g)) found.add(literal!);
    for (const [, literal, constant] of text.matchAll(/\.collection(?:<[^>()]*>)?\(\s*(?:'([A-Za-z_][\w-]*)'|([A-Z][A-Z0-9_]*)\s*\))/g)) {
      const name = literal ?? constants.get(constant!);
      if (name !== undefined) found.add(name);
    }
  }
  return found;
}

const RAW_DIRECTORIES = [
  'modules/agent-harness',
  'modules/enterprise_search/adapters',
  'modules/control-plane',
  ...REPOSITORY_ROOTS,
];

const CONTROL_PLANE =
  'control-plane configuration (catalog, installations, grants, bindings, policy layers, approvals of catalog items, migration ledgers): org, team and user scoped, no session data; what an org deletion does with it waits on G1 (cleanup audit)';

const NOT_SESSION_DATA: Readonly<Record<string, string>> = {
  searches: 'semantic search history: not a conversation; deleted by its own route',
  agentHarnessLiveness: "each Node's and worker feature's last heartbeat, deployment-wide; no org or session data",
  agentHarnessProbes: 'one deployment-wide document the journal durability probe rewrites at start; no org or session data',
  agentAuditChainChecks: "the org's latest whole-chain check: counts, a record number and a reason, keyed by the org; no session data",
  agentDispatchOutbox: 'legacy dispatch rows of builds before the shared outbox: org and run ids only, never written now; reported at start (composition.ts)',
  cpBindings: CONTROL_PLANE,
  cpCapabilityLedger: CONTROL_PLANE,
  cpCatalogItems: CONTROL_PLANE,
  cpGrants: CONTROL_PLANE,
  cpInstallationHistory: CONTROL_PLANE,
  cpInstallations: CONTROL_PLANE,
  cpMigrationLedger: CONTROL_PLANE,
  cpOrgHeads: CONTROL_PLANE,
  cpPolicyHistory: CONTROL_PLANE,
  cpPolicyLayers: CONTROL_PLANE,
};

describe('C-14: erasure covers every store a session writes to', () => {
  it('finds the collections it checks', () => {
    const collections = definedCollections();
    expect(collections.size).to.be.greaterThan(20);
    expect([...collections]).to.include.members(['sessionEvents', 'chatSessions', 'agentSessionErasures']);
  });

  it('lists every collection with the step that erases it or why it is kept', () => {
    const listed = new Set(SESSION_DATA_STORES.map((entry) => entry.store));
    const unlisted = [...definedCollections()].filter(
      (collection) => !listed.has(collection) && NOT_SESSION_DATA[collection] === undefined,
    );
    expect(unlisted, 'add these to SESSION_DATA_STORES (domain/session-erasure.ts)').to.deep.equal([]);
  });

  it('CLEANUP-S5: a collection opened by name or defined by the control plane is listed too, or said not to be session data', () => {
    const raw = rawCollections();
    expect([...raw], 'the scan finds the raw collections it must').to.include.members(['agentHarnessLiveness', 'agentHarnessProbes', 'agentDispatchOutbox', 'agentAuditRecords']);
    const listed = new Set(SESSION_DATA_STORES.map((entry) => entry.store));
    const unlisted = [...new Set([...raw, ...definedCollections()])].filter(
      (collection) => !listed.has(collection) && NOT_SESSION_DATA[collection] === undefined,
    );
    expect(unlisted, 'add these to SESSION_DATA_STORES or NOT_SESSION_DATA with the reason').to.deep.equal([]);
    for (const name of Object.keys(NOT_SESSION_DATA)) {
      expect(listed.has(name), `${name} is listed both ways`).to.equal(false);
    }
  });

  it("CLEANUP-G1: an org's erasure covers every collection, or says why it holds nothing of an org", () => {
    const collections = new Set([
      ...definedCollections(),
      ...rawCollections(),
      ...SESSION_DATA_STORES.filter((entry) => entry.kind === 'mongo').map((entry) => entry.store),
    ]);
    const uncovered = [...collections].filter((name) => ORG_STORE_COVERAGE[name] === undefined).sort();
    expect(uncovered, 'add these to ORG_STORE_COVERAGE (domain/principal-erasure.ts)').to.deep.equal([]);
    const stale = Object.keys(ORG_STORE_COVERAGE).filter((name) => !collections.has(name)).sort();
    expect(stale, 'ORG_STORE_COVERAGE names a collection nothing defines').to.deep.equal([]);
  });

  it('lists no Mongo store that no model defines (a renamed collection is not silently uncovered)', () => {
    const collections = new Set([...definedCollections(), ...rawCollections()]);
    const stale = SESSION_DATA_STORES.filter(
      (entry) => entry.kind === 'mongo' && !collections.has(entry.store),
    ).map((entry) => entry.store);
    expect(stale).to.deep.equal([]);
  });

  it('covers the stores outside Mongo the earlier phases write to', () => {
    const others = SESSION_DATA_STORES.filter((entry) => entry.kind !== 'mongo').map((entry) => entry.store);
    expect(others).to.include.members([
      'liveBus',
      'artifactRecords',
      'artifactBlobs',
      'workspaceObjects',
      'skillBundles',
      'engineState',
      'sandboxSnapshots',
      'sandboxFolders',
    ]);
  });

  it('names only steps the runner has, and the runner has exactly one eraser per step', () => {
    for (const entry of SESSION_DATA_STORES) {
      if ('step' in entry.coverage) {
        expect(ERASURE_STEPS, entry.store).to.include(entry.coverage.step);
      }
    }
    const stub = {} as never;
    const erasers = createErasureSteps({
      runs: stub,
      executions: stub,
      workspaceKeys: stub,
      liveBus: null,
      artifacts: stub,
      stores: stub,
      conversations: stub,
      journal: stub,
    });
    expect(() => new SessionErasureRunner({
      jobs: new InMemoryErasureJobStore(),
      erasers,
      conversations: NO_CONVERSATION,
    })).to.not.throw();
    expect(erasers.map((eraser) => eraser.step)).to.deep.equal([...ERASURE_STEPS]);
  });

  it('declares a residue only where a step erases, saying what it keeps', () => {
    for (const entry of SESSION_DATA_STORES.filter((e) => e.residue !== undefined)) {
      expect('step' in entry.coverage, entry.store).to.equal(true);
      expect(entry.residue!.length, entry.store).to.be.greaterThan(20);
    }
  });

  it('QA15F-11: defers nothing; chat attachments go with the conversation', () => {
    const deferred = SESSION_DATA_STORES.filter((entry) => 'deferred' in entry.coverage).map((entry) => entry.store);
    expect(deferred).to.deep.equal([]);
    expect(SESSION_DATA_STORES.find((entry) => entry.store === 'chatAttachments')?.coverage).to.deep.equal({ step: 'conversation' });
  });
});

describe('C-14 (P2.4b): deleting a user or an org erases what they own outside sessions', () => {
  it('lists the model-account vault, the schedules, the memories, the Inbox (G2), the engine sign-ins and the personal instances with a hook for each deletion', () => {
    expect(PRINCIPAL_DATA_STORES.map((entry) => entry.store)).to.deep.equal([
      'modelAccounts',
      'agentSchedules',
      'agentMemories',
      'engineSignIns',
      'inbox',
      'personalInstances',
    ]);
    const erasers: Record<string, ModelAccountEraser | ScheduleEraser | MemoryEraser | InboxEraser | EngineSignInEraser | PersonalInstanceEraser> = {
      modelAccounts: new ModelAccountVault(new MemoryKv(), platformCipher),
      agentSchedules: new InMemoryScheduleStore(),
      agentMemories: new InMemoryMemoryStore(),
      inbox: new InMemoryInboxStore(),
      engineSignIns: engineSignInEraser(new InMemoryEngineSignIns()),
      personalInstances: new ConnectorPersonalInstanceEraser(() => ({ connectorBackend: 'http://connectors', jwtSecret: 'j', scopedJwtSecret: 's' })),
    };
    for (const entry of PRINCIPAL_DATA_STORES) {
      const eraser = erasers[entry.store]!;
      expect(eraser[entry.onUserDeletion]).to.be.a('function');
      expect(eraser[entry.onOrgDeletion]).to.be.a('function');
    }
  });

  it('the hooks leave nothing of an erased user or org and nothing of anyone else', async () => {
    const kv = new MemoryKv();
    const vault = new ModelAccountVault(kv, platformCipher);
    const write = {
      kind: 'user_api_key' as const,
      provider: 'openAI',
      label: null,
      material: { provider: 'openAI', apiKey: KEYS.openAI },
      hint: KEYS.openAI,
    };
    await vault.put('org-1', { kind: 'user', userId: 'u1' }, 'openAI', write, 'u1');
    await vault.put('org-1', { kind: 'user', userId: 'u2' }, 'openAI', write, 'u2');
    await vault.put('org-1', { kind: 'org' }, 'acct-1', { ...write, kind: 'org_cloud' }, 'admin');
    await vault.put('org-2', { kind: 'user', userId: 'u1' }, 'openAI', write, 'u1');
    await vault.eraseUser('org-1', 'u1');
    expect(await kv.raw.listKeysInDirectory(`${vaultPath('org-1', 'users/u1')}/`)).to.deep.equal([]);
    expect(await vault.list('org-1', { kind: 'user', userId: 'u2' })).to.have.length(1);
    await vault.eraseOrg('org-1');
    expect(await kv.raw.listKeysInDirectory(`${vaultPath('org-1')}/`)).to.deep.equal([]);
    expect(await vault.list('org-2', { kind: 'user', userId: 'u1' })).to.have.length(1);
  });
});

describe('C-14 (H2): deleting an agent erases what the agent owns', () => {
  it('lists the memories and the schedules with an eraseAgent hook every store implements', () => {
    expect(AGENT_DATA_STORES.map((entry) => entry.store)).to.deep.equal(['agentMemories', 'agentSchedules']);
    const stores: Record<string, Array<Record<string, unknown>>> = {
      agentMemories: [new InMemoryMemoryStore() as never, MongoMemoryStore.prototype as never],
      agentSchedules: [new InMemoryScheduleStore() as never, MongoScheduleStore.prototype as never],
    };
    for (const entry of AGENT_DATA_STORES) {
      for (const store of stores[entry.store]!) {
        expect(store[entry.onAgentDeletion], entry.store).to.be.a('function');
      }
    }
  });

  it('H4: where a failed agent erasure waits is a collection erasure lists', () => {
    const collections = definedCollections();
    for (const entry of AGENT_DATA_STORES) {
      expect(collections.has(entry.retry.store), entry.retry.store).to.equal(true);
      expect(SESSION_DATA_STORES.map((s) => s.store)).to.include(entry.retry.store);
    }
  });
});

/**
 * P9 BR-1: a session backup covers what erasure covers. Every Mongo store of
 * SESSION_DATA_STORES says how a session's backup holds it (copied for the
 * session, copied for its org, or excluded with the reason), and the backup
 * selects exactly the copied ones.
 */
describe('C-14 (P9): the session backup covers every Mongo store erasure lists', () => {
  it('every Mongo store has a backup coverage, and no coverage names a store erasure does not list', () => {
    const mongo = SESSION_DATA_STORES.filter((entry) => entry.kind === 'mongo').map((entry) => entry.store);
    expect(Object.keys(SESSION_BACKUP_COVERAGE).sort()).to.deep.equal([...mongo].sort());
    for (const [store, coverage] of Object.entries(SESSION_BACKUP_COVERAGE)) {
      if ('excluded' in coverage) expect(coverage.excluded.length, store).to.be.greaterThan(10);
      if ('copy' in coverage && coverage.copy === 'org') expect(coverage.reason.length, store).to.be.greaterThan(10);
    }
  });

  it('the backup selects exactly the copied stores', () => {
    const copied = Object.entries(SESSION_BACKUP_COVERAGE)
      .filter(([, coverage]) => 'copy' in coverage)
      .map(([store]) => store);
    expect([...SESSION_BACKUP_SELECTED].sort()).to.deep.equal(copied.sort());
  });
});
