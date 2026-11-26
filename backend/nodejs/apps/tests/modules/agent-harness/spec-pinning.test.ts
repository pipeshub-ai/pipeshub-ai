import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { canonicalDigest } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import {
  capabilitiesDigest,
  CapabilitySnapshot,
  capabilitySnapshot,
  LEAST_CAPABILITIES,
} from '../../../src/modules/agent-harness/domain/capabilities';
import {
  HarnessConflictError,
  HarnessForbiddenError,
  HarnessUnavailableError,
} from '../../../src/modules/agent-harness/domain/errors';
import {
  HarnessRun,
  RunInputSnapshot,
} from '../../../src/modules/agent-harness/domain/execution';
import {
  MAX_PINNED_SPEC_BYTES,
  pinRunSpec,
  readPinnedSpec,
  resolveRunSpec,
  RUN_SPEC_SCHEMA_VERSION,
  RUN_SPEC_UPCASTERS,
  runSpecResolver,
  SpecSubject,
  upcastRunSpecV2,
} from '../../../src/modules/agent-harness/domain/run-spec';
import { AgentRunDefinition } from '../../../src/modules/agent-harness/domain/agent-definition';
import { validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import {
  runClaimedEvent,
  runSpecPinnedEvent,
} from '../../../src/modules/agent-harness/domain/execution';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { goldenSpecSubject } from './support/golden-run-spec';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { HARNESS_ENABLED } from './support/rollout';
import { testCipher } from './support/data-protection';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot(
  { ...LEAST_CAPABILITIES, 'journal.durable': true },
  1,
);

const input = (
  overrides: Partial<RunInputSnapshot> = {},
): RunInputSnapshot => ({
  query: 'q',
  recordIds: [],
  filters: {},
  attachments: [],
  model: { chatMode: 'auto' },
  ...overrides,
});

const run = (overrides: Partial<HarnessRun> = {}): HarnessRun => ({
  runId: 'run-1',
  orgId: 'org-1',
  sessionId: 'session-1',
  actorId: 'user-1',
  agentKey: 'agent-1',
  userMessageKey: 'message-1',
  idempotencyKey: 'idem-1',
  payloadHash: 'hash',
  harnessMajor: 2,
  profile: 'dispatched',
  status: 'queued',
  outcome: null,
  revision: 0,
  fence: 0,
  attemptId: null,
  workerId: null,
  leaseExpiresAt: null,
  absoluteDeadlineAt: Date.UTC(2026, 9, 1, 12),
  checkpoint: null,
  input: input(),
  createdAt: Date.UTC(2026, 9, 1, 11),
  updatedAt: Date.UTC(2026, 9, 1, 11),
  ...overrides,
});

const subject = (
  overrides: {
    run?: Partial<HarnessRun>;
    input?: Partial<RunInputSnapshot>;
    definition?: AgentRunDefinition | null;
  } = {},
): SpecSubject => ({
  run: run({ ...overrides.run, input: input(overrides.input) }),
  agentDefinition:
    overrides.definition === undefined
      ? agentDefinition()
      : overrides.definition,
  via: 'pat',
});

const toolIds = (value: SpecSubject): string[] =>
  resolveRunSpec(value, DURABLE).grants.tools.map((grant) => grant.toolId);

const pipeshub = (value: SpecSubject): Record<string, any> =>
  resolveRunSpec(value, DURABLE).extensions?.['x.pipeshub'] as Record<
    string,
    any
  >;

describe('RunSpec resolution (P1.2)', () => {
  it('resolves a valid core spec with the x.pipeshub extension', () => {
    const spec = resolveRunSpec(subject(), DURABLE);
    expect(runSpecSchema.safeParse(spec).success).to.equal(true);
    expect(spec).to.deep.include({
      schemaVersion: 3,
      harnessMajor: 2,
      runKind: 'interactive',
      executor: { kind: 'loop' },
      principalRef: 'p_user-1',
      capabilitiesDigest: DURABLE.digest,
    });
    expect(spec.model.policy).to.deep.equal([
      { provider: 'openai', model: 'gpt-x' },
    ]);
    expect(spec.model.reasoningEffort).to.equal('medium');
    expect(spec.grants).to.deep.include({
      sandboxProfile: 'none',
      egress: 'none',
    });
    expect(spec.budgets).to.deep.include({
      activeTimeS: 3_600,
      calendarDeadline: '2026-10-01T12:00:00.000Z',
    });
    expect(toolIds(subject())).to.deep.equal([
      'jira@inst-42/create_issue',
      'jira@inst-42/search_issues',
      'mcp@mcp-9/list_prs',
      'web@serper/web_search',
    ]);
    expect(pipeshub(subject())).to.deep.include({
      agent: { agentKey: 'agent-1', definitionVersion: 7, mode: 'auto' },
      model: { modelKey: 'model-1', modelName: 'gpt-x' },
      knowledge: { apps: ['app-drive'], kb: ['kb-1'], strictScope: true },
      via: 'pat',
    });
    expect(pipeshub(subject())['skills'][0].name).to.equal('xlsx');
  });

  describe('P-2: client options only narrow', () => {
    it('keeps only requested tools the agent grants', () => {
      expect(
        toolIds(
          subject({
            input: { tools: ['jira.search_issues', 'slack.send_message', 'x'] },
          }),
        ),
      ).to.deep.equal(['jira@inst-42/search_issues']);
    });

    it('an empty tools list grants no tools', () => {
      expect(toolIds(subject({ input: { tools: [] } }))).to.deep.equal([]);
      expect(pipeshub(subject({ input: { tools: [] } }))['mcp']).to.deep.equal(
        [],
      );
    });

    it('drops MCP servers whose tools were all narrowed away', () => {
      const narrowed = subject({ input: { tools: ['mcp_github_list_prs'] } });
      expect(toolIds(narrowed)).to.deep.equal(['mcp@mcp-9/list_prs']);
      expect(pipeshub(narrowed)['mcp']).to.have.length(1);
    });

    it('web search follows tools and agentCapabilities, never the other way', () => {
      expect(
        toolIds(subject({ input: { tools: ['web_search'] } })),
      ).to.deep.equal(['web@serper/web_search']);
      expect(
        toolIds(
          subject({
            input: {
              agentCapabilities: { internalSearch: true, webSearch: false },
            },
          }),
        ),
      ).not.to.include('web@serper/web_search');
      expect(
        toolIds(
          subject({
            definition: agentDefinition({ webSearch: null }),
            input: { tools: ['web_search'] },
          }),
        ),
      ).to.deep.equal([]);
    });

    it('knowledge narrows by filters and turns off with internalSearch=false', () => {
      expect(
        pipeshub(
          subject({ input: { filters: { apps: ['app-drive', 'app-other'] } } }),
        )['knowledge'],
      ).to.deep.equal({ apps: ['app-drive'], kb: ['kb-1'], strictScope: true });
      expect(
        pipeshub(subject({ input: { filters: { apps: [], kb: ['kb-1'] } } }))[
          'knowledge'
        ],
      ).to.deep.equal({ apps: [], kb: ['kb-1'], strictScope: true });
      expect(
        pipeshub(
          subject({
            input: {
              agentCapabilities: { internalSearch: false, webSearch: true },
            },
          }),
        )['knowledge'],
      ).to.deep.equal({ apps: [], kb: [], strictScope: true });
    });

    it('the client may choose among the granted models, never add one', () => {
      const chosen = subject({
        input: { model: { chatMode: 'react', modelKey: 'model-2' } },
      });
      expect(resolveRunSpec(chosen, DURABLE).model.policy[0]).to.deep.equal({
        provider: 'anthropic',
        model: 'claude-x',
      });
      expect(pipeshub(chosen)['agent'].mode).to.equal('react');
      expect(() =>
        resolveRunSpec(
          subject({
            input: { model: { chatMode: 'auto', modelKey: 'model-9' } },
          }),
          DURABLE,
        ),
      ).to.throw(HarnessForbiddenError, /does not grant this model/);
    });

    it('an agent without models runs on the org model the client picked', () => {
      const spec = resolveRunSpec(
        subject({
          definition: agentDefinition({ models: [] }),
          input: {
            model: {
              chatMode: 'auto',
              modelKey: 'org-model',
              modelName: 'gemini-x',
              modelProvider: 'Gemini',
            },
          },
        }),
        DURABLE,
      );
      expect(spec.model.policy[0]).to.deep.equal({
        provider: 'gemini',
        model: 'gemini-x',
      });
      const orgDefault = resolveRunSpec(
        subject({ definition: agentDefinition({ models: [] }) }),
        DURABLE,
      );
      expect(orgDefault.model.policy[0]).to.deep.equal({
        provider: 'pipeshub',
        model: 'org-default',
      });
      expect(orgDefault.extensions?.['x.pipeshub']).not.to.have.property(
        'model',
      );
    });

    it('leaves out a tool whose identity is not a valid ToolId', () => {
      const definition = agentDefinition({
        toolsets: [
          {
            name: 'Jira Cloud',
            instanceId: 'inst-1',
            tools: [{ name: 'search', fullName: 'jira.search' }],
          },
          {
            name: 'slack',
            instanceId: null,
            tools: [{ name: 'send message', fullName: 'slack.send' }],
          },
        ],
        mcpServers: [],
        webSearch: null,
      });
      expect(toolIds(subject({ definition }))).to.deep.equal([]);
    });

    it('built-in chat in a pre-P1b mode grants no tools and scopes knowledge to the client filters', () => {
      const chat = subject({
        definition: null,
        run: { agentKey: undefined },
        input: { filters: { apps: ['app-1'], kb: ['kb-2', 'bad id'] } },
      });
      expect(toolIds(chat)).to.deep.equal([]);
      expect(pipeshub(chat)).not.to.have.property('agent');
      expect(pipeshub(chat)).not.to.have.property('chat');
      expect(pipeshub(chat)['knowledge']).to.deep.equal({
        apps: ['app-1'],
        kb: ['kb-2'],
        strictScope: false,
      });
    });
  });

  describe('PC-2: the built-in assistant grant (knowledge and the platform tools it has today)', () => {
    const assistant = (
      chatMode: string,
      extra: Partial<RunInputSnapshot> = {},
    ): SpecSubject =>
      subject({
        definition: null,
        run: { agentKey: undefined },
        input: { model: { chatMode }, ...extra },
      });
    const WEB = 'web@default/web_search';

    const cases: Array<[string, Partial<RunInputSnapshot>, boolean, string[]]> = [
      ['internal_search', {}, true, []],
      ['web_search', {}, false, [WEB]],
      // `chatbot.py` applies capabilities in agent mode only.
      ['internal_search', { agentCapabilities: { internalSearch: false, webSearch: true } }, true, []],
      ['web_search', { agentCapabilities: { internalSearch: true, webSearch: true } }, false, [WEB]],
    ];
    for (const [mode, extra, internalSearch, tools] of cases) {
      it(`${mode} ${JSON.stringify(extra.agentCapabilities ?? {})}: knowledge ${String(internalSearch)}, tools ${JSON.stringify(tools)}`, () => {
        const value = assistant(mode, extra);
        expect(toolIds(value)).to.deep.equal(tools);
        expect(pipeshub(value)['chat']).to.deep.equal({ mode, internalSearch });
        expect(pipeshub(value)).not.to.have.property('agent');
        expect(pipeshub(value)['mcp']).to.deep.equal([]);
        expect(pipeshub(value)['skills']).to.deep.equal([]);
      });
    }

    it('Universal Agent Mode is not an assistant grant: it keeps the empty pre-P1b grant', () => {
      const universal = assistant('agent');
      expect(toolIds(universal)).to.deep.equal([]);
      expect(pipeshub(universal)).not.to.have.property('chat');
    });

    it('the client can only narrow: tools and knowledge follow the mode, never the request', () => {
      const asked = assistant('internal_search', {
        tools: ['web_search', 'jira.search_issues'],
        filters: { apps: ['app-1'] },
      });
      expect(toolIds(asked)).to.deep.equal([]);
      expect(pipeshub(asked)['knowledge']).to.deep.equal({ apps: ['app-1'], kb: [], strictScope: false });
      const off = assistant('web_search', { filters: { apps: ['app-1'], kb: ['kb-1'] } });
      expect(pipeshub(off)['knowledge']).to.deep.equal({ apps: [], kb: [], strictScope: false });
    });

    it('the mode is canonical and the spec digest is stable across claims', () => {
      const first = pinRunSpec(assistant(' Internal_Search '), DURABLE);
      const again = pinRunSpec(assistant(' Internal_Search '), DURABLE);
      expect(first.specDigest).to.equal(again.specDigest);
      expect((first.spec.extensions?.['x.pipeshub'] as Record<string, any>)['chat']['mode']).to.equal('internal_search');
      expect(pinRunSpec(assistant('web_search'), DURABLE).specDigest).to.not.equal(first.specDigest);
      expect(runSpecSchema.safeParse(first.spec).success).to.equal(true);
    });
  });

  describe('P-4: the digest is stable and canonical', () => {
    it('is the canonical digest of the spec and does not depend on definition order', () => {
      const pinned = pinRunSpec(subject(), DURABLE);
      expect(pinned.specDigest).to.equal(canonicalDigest(pinned.spec));
      const base = agentDefinition();
      const shuffled = agentDefinition({
        toolsets: base.toolsets.map((toolset) => ({
          ...toolset,
          tools: [...toolset.tools].reverse(),
        })),
        knowledge: [...base.knowledge].reverse(),
        models: base.models,
      });
      expect(
        pinRunSpec(subject({ definition: shuffled }), DURABLE).specDigest,
      ).to.equal(pinned.specDigest);
      expect(pinRunSpec(subject(), DURABLE).specDigest).to.equal(
        pinned.specDigest,
      );
    });

    it('changes when a grant or the capability set changes', () => {
      const pinned = pinRunSpec(subject(), DURABLE);
      expect(
        pinRunSpec(
          subject({ input: { tools: ['jira.search_issues'] } }),
          DURABLE,
        ).specDigest,
      ).not.to.equal(pinned.specDigest);
      const redis = capabilitySnapshot(
        { ...DURABLE.capabilities, livebus: 'redis' },
        2,
      );
      const moved = pinRunSpec(subject(), redis);
      expect(moved.specDigest).not.to.equal(pinned.specDigest);
      expect(moved.capabilitiesDigest).to.equal(
        capabilitiesDigest(redis.capabilities),
      );
    });
  });

  it('P-4: matches the cross-language golden pin (Python reads the same digest)', () => {
    const golden = JSON.parse(
      readFileSync(
        join(
          __dirname,
          '../../../../../../contracts/harness/vectors/pinned-run-spec.json',
        ),
        'utf8',
      ),
    ) as { pinned: unknown };
    expect(pinRunSpec(goldenSpecSubject(), DURABLE)).to.deep.equal(
      golden.pinned,
    );
  });

  it('refuses a spec larger than the pinned size limit', () => {
    const tools = Array.from({ length: 40_000 }, (_, index) => ({
      name: `tool_${String(index)}`,
      fullName: `bulk.tool_${String(index)}`,
    }));
    const huge = agentDefinition({
      toolsets: [{ name: 'bulk', instanceId: 'inst-1', tools }],
    });
    try {
      resolveRunSpec(subject({ definition: huge }), DURABLE);
      expect.fail(
        `expected a spec over ${String(MAX_PINNED_SPEC_BYTES)} bytes to be refused`,
      );
    } catch (error) {
      expect((error as HarnessConflictError).code).to.equal(
        'SPEC_UNRESOLVABLE',
      );
    }
  });

  it('builds claim events the catalog accepts', () => {
    const pinned = pinRunSpec(subject(), DURABLE);
    expect(() =>
      validateJournalAppend({
        orgId: 'org-1',
        sessionId: 'session-1',
        streamId: 'session-1',
        runId: 'run-1',
        attemptId: 'attempt-1',
        fence: 1,
        writer: 'node',
        events: [runClaimedEvent(1, 1, 30_000), runSpecPinnedEvent(pinned)],
      }),
    ).not.to.throw();
  });
});

describe('P-8: pinned spec upcasters', () => {
  const current = () =>
    JSON.parse(JSON.stringify(pinRunSpec(subject(), DURABLE).spec)) as Record<
      string,
      unknown
    >;
  /** The same run as a v2 Node pinned it: no executor. */
  const v2 = () => {
    const { executor: _executor, ...rest } = current();
    _executor;
    return { ...rest, schemaVersion: 2 };
  };

  it('reads the current version as is', () => {
    expect(readPinnedSpec(current())).to.deep.equal(current());
  });

  it('RS3-6: Node pins v3 with the loop executor', () => {
    expect(current()).to.deep.include({
      schemaVersion: RUN_SPEC_SCHEMA_VERSION,
      executor: { kind: 'loop' },
    });
    expect(RUN_SPEC_SCHEMA_VERSION).to.equal(3);
  });

  it('EO-5 (SS1): the claim pins what the executor resolver answers; by default the loop, as before', () => {
    const engine = {
      kind: 'engine' as const,
      engine: {
        kind: 'coder',
        version: '1.0.0',
        binaryDigest: `sha256:${'a'.repeat(64)}`,
        profileDigest: `sha256:${'b'.repeat(64)}`,
      },
    };
    const asked: unknown[] = [];
    const pinned = runSpecResolver((subject) => {
      asked.push(subject.agentDefinition?.agentKey ?? null);
      return engine;
    })(goldenSpecSubject(), DURABLE);
    expect(pinned.spec.executor).to.deep.equal(engine);
    expect(runSpecSchema.safeParse(pinned.spec).success).to.equal(true);
    expect(asked).to.have.length(1);
    expect(pinned.specDigest).to.not.equal(pinRunSpec(goldenSpecSubject(), DURABLE).specDigest);
    expect(runSpecResolver(() => ({ kind: 'loop' }))(goldenSpecSubject(), DURABLE)).to.deep.equal(
      pinRunSpec(goldenSpecSubject(), DURABLE),
    );
  });

  it('RS3-5: a v2 pin reads as v3 on the loop, nothing else changed', () => {
    expect(readPinnedSpec(v2())).to.deep.equal(current());
    expect(upcastRunSpecV2(v2())).to.deep.equal(current());
  });

  it('upcasts an older version one step at a time', () => {
    const older = { ...v2(), schemaVersion: 1, legacyBudget: 3 };
    const upcast = readPinnedSpec(older, {
      ...RUN_SPEC_UPCASTERS,
      1: (spec) => {
        const { legacyBudget: _dropped, ...rest } = spec;
        _dropped;
        return { ...rest, schemaVersion: 2 };
      },
    });
    expect(upcast).to.deep.equal(current());
  });

  for (const [label, stored, upcasters] of [
    ['no upcaster for an older version', { schemaVersion: 1 }, {}],
    ['a newer version (RS3-7)', { ...{ schemaVersion: 4 } }, {}],
    ['a v4 spec otherwise current (RS3-7)', 'v4', RUN_SPEC_UPCASTERS],
    [
      'an upcaster that does not advance',
      { schemaVersion: 1 },
      { 1: (spec: Record<string, unknown>) => spec },
    ],
    ['a non-object', 'spec', {}],
  ] as const) {
    it(`refuses ${label} (fail closed)`, () => {
      const pinned =
        stored === 'v4' ? { ...current(), schemaVersion: 4 } : stored;
      try {
        readPinnedSpec(pinned, upcasters as never);
        expect.fail('expected SPEC_UNSUPPORTED');
      } catch (error) {
        expect((error as HarnessConflictError).code).to.equal(
          'SPEC_UNSUPPORTED',
        );
      }
    });
  }

  const serving = (stored: Record<string, unknown>) => {
    const store = {
      readClaimSubject: async () => ({ ...subject(), pinned: { spec: stored } }),
      claim: async () => ({
        run: run(),
        journal: { lastSeq: {} },
        pinned: { spec: stored, specDigest: 'sha256:pinned' },
      }),
    };
    return new ExecutionService({
      store: store as never,
      journal: {} as never,
      journals: {} as never,
      capabilities: { current: () => DURABLE },
      resolveSpec: pinRunSpec,
    }).claim({
      orgId: 'org-1',
      runId: 'run-1',
      workerId: 'w',
      attemptId: 'a',
      leaseDurationMs: 1_000,
    });
  };

  it('RS3-8: the claim hands the worker the pin as stored, keeping its digest', async () => {
    const claimed = await serving(v2());
    expect(claimed.pinned?.spec).to.deep.equal(v2());
    expect(claimed.pinned?.specDigest).to.equal('sha256:pinned');
  });

  it('RS3-8: a pin this Node cannot read is refused, not served', async () => {
    try {
      await serving({ ...current(), schemaVersion: 4 });
      expect.fail('expected SPEC_UNSUPPORTED');
    } catch (error) {
      expect((error as HarnessConflictError).code).to.equal('SPEC_UNSUPPORTED');
    }
  });
});

describe('spec pinning through ExecutionService', () => {
  let now: number;
  let store: InMemoryExecutionStore;
  let capabilities: CapabilitySnapshot | null;
  let resolver: sinon.SinonSpy;
  let service: ExecutionService;

  const admit = async (runInput: Partial<RunInputSnapshot> = {}) => {
    const outcome = await new RunAdmissionService({
      store,
      rollout: {
        decide: async () => V2,
        decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }),
      },
      clock: { now: () => now },
      agentAccess: grantingAccess(),
    }).submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: `k-${String(now)}-${Math.random()}`,
      deadlineMs: 60_000,
      input: input(runInput),
    });
    if (outcome.route !== 'durable') expect.fail('expected a durable run');
    return outcome.admission.run;
  };

  const claim = (target: HarnessRun, worker: string) =>
    service.claim({
      orgId: target.orgId,
      runId: target.runId,
      workerId: worker,
      attemptId: `attempt-${worker}`,
      leaseDurationMs: 1_000,
    });

  beforeEach(() => {
    now = 1_000;
    store = new InMemoryExecutionStore({ now: () => now });
    capabilities = DURABLE;
    resolver = sinon.spy(pinRunSpec);
    service = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      capabilities: { current: () => capabilities },
      resolveSpec: resolver,
    });
  });

  it('P-1: resolves exactly once however often the run is claimed', async () => {
    const target = await admit();
    const first = await claim(target, 'a');
    for (const worker of ['b', 'c']) {
      now += 1_001;
      const again = await claim(target, worker);
      expect(again.pinned).to.deep.equal(first.pinned);
    }
    expect(resolver.callCount).to.equal(1);
  });

  it('P-6: pins the capability set of the first claim; later changes leave the run alone', async () => {
    const target = await admit();
    const first = await claim(target, 'a');
    expect(first.pinned?.capabilities).to.deep.equal(DURABLE.capabilities);
    expect(first.pinned?.capabilitiesDigest).to.equal(DURABLE.digest);
    capabilities = capabilitySnapshot(
      { ...DURABLE.capabilities, livebus: 'redis', 'workers.dispatched': true },
      2,
    );
    now += 1_001;
    const again = await claim(target, 'b');
    expect(again.pinned?.capabilitiesDigest).to.equal(DURABLE.digest);
    const fresh = await claim(await admit(), 'c');
    expect(fresh.pinned?.capabilitiesDigest).to.equal(capabilities.digest);
  });

  it('O-3: a first claim on a non-durable journal (or before probing) is refused and changes nothing', async () => {
    const target = await admit();
    for (const probed of [
      null,
      capabilitySnapshot({ ...LEAST_CAPABILITIES }, 1),
    ]) {
      capabilities = probed;
      try {
        await claim(target, 'a');
        expect.fail('expected the claim to be refused');
      } catch (error) {
        expect(error).to.be.instanceOf(HarnessUnavailableError);
      }
    }
    const current = await store.get(target.orgId, target.runId);
    expect([current?.status, current?.fence]).to.deep.equal(['queued', 0]);
    expect(
      (await store.journal.readStream('org-1', target.sessionId, 0, 10)).map(
        (event) => event.type,
      ),
    ).to.deep.equal(['run.admitted']);
  });

  it('P-9: a claim never asks the agent authority again', async () => {
    const authorizeRun = sinon.stub().resolves(agentDefinition());
    const outcome = await new RunAdmissionService({
      store,
      rollout: {
        decide: async () => V2,
        decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }),
      },
      clock: { now: () => now },
      agentAccess: { authorizeRun },
    }).submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: 'once',
      deadlineMs: 60_000,
      input: input(),
    });
    if (outcome.route !== 'durable') expect.fail('expected a durable run');
    // The agent is deleted after admission: the authority now denies.
    authorizeRun.resolves(null);
    const claimed = await claim(outcome.admission.run, 'a');
    expect(claimed.pinned?.spec.grants.tools).to.have.length(4);
    expect(authorizeRun.callCount).to.equal(1);
  });

  it('P-14: admission refuses a model the agent does not grant and writes nothing', async () => {
    try {
      await admit({ model: { chatMode: 'auto', modelKey: 'not-granted' } });
      expect.fail('expected a refusal');
    } catch (error) {
      expect(error).to.be.instanceOf(HarnessForbiddenError);
    }
    expect(store.dispatches).to.have.length(0);
  });
});
