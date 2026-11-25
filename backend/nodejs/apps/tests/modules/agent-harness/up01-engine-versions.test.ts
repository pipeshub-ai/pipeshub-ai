/**
 * UP-01, AD-01 (C14.01, C11.01): engine versions side by side. The allowlist may hold several versions of one
 * engine; the first is the one new chats start on. A chat keeps the version its first engine turn ran on
 * until the person picks an engine again (`/engine`); an agent that names a version keeps it. An org admin
 * turns versions on and off one by one (`kind@version` in the ceiling's engines): a change applies to the next
 * turn, and a chat whose version was turned off (or retired from the release) moves to the default, logged.
 */
import { expect } from 'chai';
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { CodingEngineService } from '../../../src/modules/agent-harness/application/coding-engine-service';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { parseEngineRef, pinnedEngineLayer } from '../../../src/modules/agent-harness/domain/coding-engines';
import { PinnedEngineProfile, RELEASE_ENGINE_PROFILES, resolveEngineProfile } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { principalRefFor, profileExecutor, runSpecResolver } from '../../../src/modules/agent-harness/domain/run-spec';
import { agentDefinition } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { testControlPlane } from '../control-plane/support/in-memory';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const ORG = 'org-1';
const USER = 'user-1';
const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);

const release = (kind: string): PinnedEngineProfile => {
  const found = RELEASE_ENGINE_PROFILES.find((profile) => profile.engine.kind === kind);
  if (found === undefined) throw new Error(`no ${kind} in the release`);
  return found;
};
const OPENCODE = release('opencode');
const OLD = OPENCODE.engine.version;
const NEW = '9.0.0';
const opencodeAt = (version: string): PinnedEngineProfile => ({
  ...OPENCODE,
  engine: { ...OPENCODE.engine, version, binaryDigest: `sha256:${(version === OLD ? 'a' : 'b').repeat(64)}` },
});
const OTHERS = RELEASE_ENGINE_PROFILES.filter((profile) => profile.engine.kind !== 'opencode');
/** Before the upgrade: one opencode version. */
const BEFORE: PinnedEngineProfile[] = [opencodeAt(OLD), ...OTHERS];
/** The upgrade adds a version beside the old one and makes it the default (first). */
const AFTER: PinnedEngineProfile[] = [opencodeAt(NEW), opencodeAt(OLD), ...OTHERS];
/** A later release retires the old version. */
const RETIRED: PinnedEngineProfile[] = [opencodeAt(NEW), ...OTHERS];

describe('UP-01 / AD-01: engine versions side by side, a chat pinned to its version, per-version admin toggles', () => {
  let now: number;
  let store: InMemoryExecutionStore;
  let execution: ExecutionService;
  let preferences: InMemoryCodingEnginePreferences;
  let settings: FakeEngineCeilings;
  let releases: PinnedEngineProfile[];
  let lines: Array<[string, Record<string, unknown> | undefined]>;

  const logger: HarnessLogger = {
    debug: () => undefined,
    info: (event, meta) => void lines.push([event, meta]),
    warn: (event, meta) => void lines.push([event, meta]),
    error: (event, meta) => void lines.push([event, meta]),
  };
  const logged = (event: string) => lines.filter(([e]) => e === event).map(([, meta]) => meta);

  const engines = () =>
    new CodingEngineService({
      ceilings: settings,
      access: testModelAccess(),
      preferences,
      transaction: testControlPlane(),
      availability: { unavailable: async () => new Set<string>() },
      releases,
    });

  const admit = async (options: { sessionId?: string; agentKey?: string; agentEngines?: string[] } = {}): Promise<HarnessRun> => {
    now += 1;
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      clock: { now: () => now },
      agentAccess: {
        authorizeRun: async (request) =>
          agentDefinition({
            agentKey: request.agentKey,
            ...(options.agentEngines ? { engineProfile: { engines: options.agentEngines } } : {}),
          }),
      },
      executors: profileExecutor,
      roles: { roleOf: async () => 'owner' } as never,
      engineProfiles: settings,
      modelAccess: testModelAccess(),
      codingEngine: engines(),
      engineReleases: releases,
      logger,
      workerFeatures: { live: async () => true, metrics: { withheld: () => undefined } } as never,
    }).submit({
      orgId: ORG,
      actorId: USER,
      ...(options.agentKey !== undefined ? { agentKey: options.agentKey } : {}),
      ...(options.sessionId !== undefined ? { sessionId: options.sessionId } : {}),
      authorization: 'Bearer t',
      durableRequested: true,
      idempotencyKey: `k-${String(now)}`,
      deadlineMs: 60_000,
      input: {
        query: 'q',
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: options.agentKey !== undefined ? 'quick' : 'agent' },
      },
    });
    if (outcome.route !== 'durable') return expect.fail(`expected a durable run, got ${JSON.stringify(outcome)}`);
    return outcome.admission.run;
  };

  /** The engine version admission gave the run's code_task; the run is then ended so the chat takes its next turn. */
  const ranOn = async (run: HarnessRun): Promise<string | undefined> => {
    const engine = (await store.readClaimSubject(ORG, run.runId))?.engineProfile?.engine;
    await execution.control({
      orgId: ORG,
      runId: run.runId,
      expectedRevision: (await store.get(ORG, run.runId))!.revision,
      action: 'cancel',
      by: principalRefFor(USER),
      commandId: `end-${run.runId}`,
      dispatchCommandId: `end-${run.runId}:dispatch`,
    });
    return engine === undefined ? undefined : `${engine.kind}@${engine.version}`;
  };

  beforeEach(async () => {
    now = 1_000;
    lines = [];
    releases = BEFORE;
    store = new InMemoryExecutionStore({ now: () => now });
    preferences = new InMemoryCodingEnginePreferences();
    await preferences.writeUser(ORG, USER, 'opencode');
    settings = new FakeEngineCeilings();
    execution = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      capabilities: { current: () => DURABLE },
      resolveSpec: runSpecResolver(profileExecutor),
    });
  });

  it('UP-01: an engine\'s first allowlist version is its default; each version resolves by `kind@version`', () => {
    expect(resolveEngineProfile({ agent: { engines: ['opencode'] } }, AFTER).profile?.engine.version).to.equal(NEW);
    expect(resolveEngineProfile({ agent: { engines: [`opencode@${OLD}`] } }, AFTER).profile?.engine.version).to.equal(OLD);
    expect(resolveEngineProfile({ agent: { engines: ['opencode@0.0.1'] } }, AFTER).refusal?.dimension).to.equal('engine');
  });

  it('UP-01: a chat holds its version across turns after a new version is added; a new chat starts on the new default', async () => {
    const first = await admit();
    expect(await ranOn(first)).to.equal(`opencode@${OLD}`);
    expect(await preferences.readSessionPin(ORG, first.sessionId)).to.equal(`opencode@${OLD}`);

    releases = AFTER;
    expect(await ranOn(await admit({ sessionId: first.sessionId })), 'existing chat stays pinned').to.equal(`opencode@${OLD}`);
    expect(await ranOn(await admit({ sessionId: first.sessionId })), 'and on every later turn').to.equal(`opencode@${OLD}`);
    const fresh = await admit();
    expect(await ranOn(fresh), 'a new chat uses the default').to.equal(`opencode@${NEW}`);
    expect(await preferences.readSessionPin(ORG, fresh.sessionId)).to.equal(`opencode@${NEW}`);
    expect(logged('run.engine_version_moved')).to.deep.equal([]);
  });

  it('UP-01: the person moves the chat by picking its engine again (`/engine`); the next turn takes the default', async () => {
    const first = await admit();
    expect(await ranOn(first)).to.equal(`opencode@${OLD}`);
    releases = AFTER;
    await engines().setSessionChoice(ORG, USER, first.sessionId, 'opencode');
    expect(await preferences.readSessionPin(ORG, first.sessionId)).to.equal(null);
    expect(await ranOn(await admit({ sessionId: first.sessionId }))).to.equal(`opencode@${NEW}`);
    expect(await preferences.readSessionPin(ORG, first.sessionId)).to.equal(`opencode@${NEW}`);
  });

  it('UP-01: an agent that names a version keeps it in a new chat, and its chat pins that version', async () => {
    releases = AFTER;
    const run = await admit({ agentKey: 'agent-1', agentEngines: [`opencode@${OLD}`, 'codex'] });
    expect(await ranOn(run)).to.equal(`opencode@${OLD}`);
    expect(await preferences.readSessionPin(ORG, run.sessionId)).to.equal(`opencode@${OLD}`);
  });

  it('UP-01: a chat pinned to a version the release retired moves to the default, logged', async () => {
    const first = await admit();
    expect(await ranOn(first)).to.equal(`opencode@${OLD}`);
    releases = RETIRED;
    expect(await ranOn(await admit({ sessionId: first.sessionId }))).to.equal(`opencode@${NEW}`);
    expect(await preferences.readSessionPin(ORG, first.sessionId)).to.equal(`opencode@${NEW}`);
    expect(logged('run.engine_version_moved')).to.deep.equal([
      { event: 'run.engine_version_moved', orgId: ORG, sessionId: first.sessionId, engine: `opencode@${OLD}`, reason: 'engine_profile_widening' },
    ]);
  });

  it('AD-01: an admin turns a version off; new chats and the next turn of a chat on it apply it at once', async () => {
    releases = AFTER;
    await settings.write(ORG, { engines: [`opencode@${OLD}`, 'codex', 'claude_code'] }, 'p_admin');
    const onOld = await admit();
    expect(await ranOn(onOld), 'the new version is off: a new chat starts on the old one').to.equal(`opencode@${OLD}`);

    await settings.write(ORG, { engines: [`opencode@${NEW}`, 'codex', 'claude_code'] }, 'p_admin');
    expect(await ranOn(await admit()), 'turned on: new chats start on it').to.equal(`opencode@${NEW}`);
    expect(await ranOn(await admit({ sessionId: onOld.sessionId })), 'the old one is off: its chat moves').to.equal(`opencode@${NEW}`);
    expect(logged('run.engine_version_moved').map((meta) => meta?.['engine'])).to.deep.equal([`opencode@${OLD}`]);
  });

  it('AD-01: the coding-engine picker lists an engine once, at the version the org allows', async () => {
    releases = AFTER;
    await settings.write(ORG, { engines: [`opencode@${OLD}`] }, 'p_admin');
    expect((await engines().engines(ORG)).map((engine) => engine.kind)).to.deep.equal(['opencode']);
    await engines().assertUsable(ORG, 'opencode');
  });

  it('a pin applies only where the narrowing admits it', () => {
    expect(parseEngineRef('opencode@1.2')).to.deep.equal({ kind: 'opencode', version: '1.2' });
    expect(parseEngineRef('opencode')).to.equal(null);
    expect(parseEngineRef('opencode@')).to.equal(null);
    expect(pinnedEngineLayer({ engines: ['opencode'] }, 'opencode@1.2')).to.deep.equal({ engines: ['opencode@1.2'] });
    expect(pinnedEngineLayer({ engines: ['codex'] }, 'opencode@1.2'), 'moved to another engine').to.equal(null);
    expect(pinnedEngineLayer({ engines: ['opencode@1.3'] }, 'opencode@1.2'), 'the agent names another version').to.equal(null);
    expect(pinnedEngineLayer({ mode: 'plan' }, 'opencode@1.2'), 'no engines listed: any').to.deep.equal({ mode: 'plan', engines: ['opencode@1.2'] });
    expect(pinnedEngineLayer({ engines: ['opencode'] }, 'opencode'), 'not a version').to.equal(null);
  });
});

