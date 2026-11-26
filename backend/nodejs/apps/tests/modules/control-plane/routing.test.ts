/**
 * CPD-10 §2.1: `routingOf`, the one function a pin's `routing` and the
 * resolved `RoutingDecider` are computed by, over rows resolved by CPD-3's
 * resolver from the release layer and an org's.
 */
import { expect } from 'chai';
import fc from 'fast-check';
import { RELEASE_DEFAULTS } from '../../../src/modules/agent-harness/contracts/generated/core/policy-data';
import { PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import { releaseLayers } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { PARENT_DIMENSIONS } from '../../../src/modules/agent-harness/domain/policy';
import { prefixSources, runSources } from '../../../src/modules/control-plane/application/layer-sources';
import { PolicyRunConfigPinner } from '../../../src/modules/control-plane/application/run-config-pinner';
import type { EffectiveRunConfig } from '../../../src/modules/control-plane/domain/policy-alias';
import { isProblem } from '../../../src/modules/control-plane/domain/problem';
import { ORG_A } from './support/cp-objects';
import { InMemoryEffectiveSetResolver } from './support/in-memory-resolution';
import { StaticSnapshots } from './support/pinner-contract';
import { aLayer, aSnapshot, aSubject } from './support/resolution-fixtures';
import {
  familyOf,
  LEGACY_REASONS,
  NO_FAMILIES,
  routingOf,
  type ConfiguredFamilies,
  type LegacyReason,
  type RoutedTurn,
  type RoutingFacts,
} from '../../../src/modules/control-plane/domain/routing';

interface World {
  rollout: Record<string, unknown>;
  families: ConfiguredFamilies;
  turn: RoutedTurn;
  facts: RoutingFacts;
}

const FAMILIES: ConfiguredFamilies = { byKey: new Map([['mk-claude', 'anthropic'], ['mk-bare', null]]), ofDefault: 'openai' };

/** Nothing stands in the way: an agent's `quick` turn in a new conversation, the org on, a worker live. */
const open = (): World => ({
  rollout: { ENABLE_AGENT_HARNESS: true, AGENT_HARNESS_KILL_SWITCH: false },
  families: FAMILIES,
  turn: { agentKey: 'a7', chatMode: 'quick', projectId: null },
  facts: { infrastructureReady: true, workerLive: true, scheduled: false, placement: null, modelKey: null, existing: false, requested: null },
});

const resolved = (rollout: Record<string, unknown>): EffectiveRunConfig => {
  const result = new PolicyResolver().resolve([
    { kind: 'release', values: RELEASE_DEFAULTS },
    { kind: 'org', values: { rollout } },
  ]);
  if (result.refusal !== null) throw new Error(`${result.refusal.code} ${result.refusal.dimension}`);
  return result.config;
};

const route = (world: World) => routingOf(resolved(world.rollout), world.families, world.turn, world.facts);

const durable = (world: World): World => ({ ...world, rollout: { ...world.rollout, durable: { ...(world.rollout['durable'] as object | undefined) } } });
const withFamilies = (world: World, modelFamilies: string[]): World => {
  const next = durable(world);
  (next.rollout['durable'] as Record<string, unknown>)['modelFamilies'] = modelFamilies;
  return next;
};

/** §2.1's rows in order: what makes each one apply. */
const ROWS: Array<{ reason: LegacyReason; apply: (world: World) => World; excludes?: LegacyReason[] }> = [
  { reason: 'infrastructure_unavailable', apply: (w) => ({ ...w, facts: { ...w.facts, infrastructureReady: false } }) },
  { reason: 'kill_switch', apply: (w) => ({ ...w, rollout: { ...w.rollout, AGENT_HARNESS_KILL_SWITCH: true } }) },
  { reason: 'org_disabled', apply: (w) => ({ ...w, rollout: { ...w.rollout, ENABLE_AGENT_HARNESS: false } }) },
  { reason: 'model_family_not_allowed', apply: (w) => withFamilies(w, []), excludes: ['model_family_unknown'] },
  { reason: 'chat_mode_not_supported', apply: (w) => ({ ...w, turn: { ...w.turn, chatMode: 'planExecute' } }) },
  { reason: 'model_family_unknown', apply: (w) => ({ ...withFamilies(w, ['anthropic']), facts: { ...w.facts, modelKey: 'mk-gone' } }) },
  { reason: 'project_scope', apply: (w) => ({ ...w, turn: { ...w.turn, projectId: 'p-9' } }) },
  { reason: 'no_live_worker', apply: (w) => ({ ...w, facts: { ...w.facts, workerLive: false } }) },
  { reason: 'user_chose_classic', apply: (w) => ({ ...w, facts: { ...w.facts, requested: 'legacy', classicChosen: true } }) },
];

describe('CPD-10 routing in the pin: routingOf (CPD10-2)', () => {
  it('nothing in the way: durable, with no reason', () => {
    expect(route(open())).to.deep.equal({ path: 'durable', reason: null });
  });

  it('the rows are decided in the order the contract lists its reasons', () => {
    expect(ROWS.map((row) => row.reason)).to.deep.equal(LEGACY_REASONS.filter((reason, index) => LEGACY_REASONS.indexOf(reason) === index));
  });

  for (const [index, row] of ROWS.entries()) {
    it(`row ${String(index + 1)}: ${row.reason} alone`, () => {
      expect(route(row.apply(open()))).to.deep.equal({ path: 'legacy', reason: row.reason });
    });

    it(`row ${String(index + 1)}: ${row.reason} wins over every row after it`, () => {
      const world = ROWS.slice(index + 1)
        .filter((later) => !(row.excludes ?? []).includes(later.reason))
        .reduce((w, later) => later.apply(w), row.apply(open()));
      expect(route(world)).to.deep.equal({ path: 'legacy', reason: row.reason });
    });
  }

  describe('model families (rows 4, 6, 7)', () => {
    const listed = (modelKey: string | null, list = ['anthropic']) =>
      route({ ...withFamilies(open(), list), facts: { ...open().facts, modelKey } });

    it('a family on the list is durable; one off it is model_family_not_allowed', () => {
      expect(listed('mk-claude')).to.deep.equal({ path: 'durable', reason: null });
      expect(listed(null)).to.deep.equal({ path: 'legacy', reason: 'model_family_not_allowed' });
      expect(listed(null, ['anthropic', 'openai'])).to.deep.equal({ path: 'durable', reason: null });
    });

    it('a key that names no configured model, or an entry without a provider, is model_family_unknown', () => {
      expect(listed('mk-gone').reason).to.equal('model_family_unknown');
      expect(listed('mk-bare').reason).to.equal('model_family_unknown');
      expect(route({ ...withFamilies(open(), ['openai']), families: NO_FAMILIES }).reason).to.equal('model_family_unknown');
    });

    it('with no list (the release: any family) the model is never looked up', () => {
      expect(route({ ...open(), families: NO_FAMILIES, facts: { ...open().facts, modelKey: 'mk-gone' } }).path).to.equal('durable');
    });

    it('familyOf: an empty key is the default LLM, as the query service reads it', () => {
      expect([familyOf(FAMILIES, ''), familyOf(FAMILIES, null), familyOf(FAMILIES, 'mk-claude')]).to.deep.equal(['openai', 'openai', 'anthropic']);
    });
  });

  describe('chat modes (row 5)', () => {
    const mode = (chatMode: string, agentKey: string | null, rollout: Record<string, unknown> = {}) =>
      route({ ...open(), rollout: { ...open().rollout, ...rollout }, turn: { agentKey, chatMode, projectId: null } });

    it('an agent takes the release’s agent modes and the assistant its own, whatever the case', () => {
      for (const chatMode of RELEASE_DEFAULTS.rollout.durable.modes) expect(mode(chatMode, 'a7').path, chatMode).to.equal('durable');
      for (const chatMode of RELEASE_DEFAULTS.rollout.durable.assistantModes) expect(mode(chatMode, null).path, chatMode).to.equal('durable');
      expect(mode(' Quick ', 'a7').path).to.equal('durable');
      expect(mode('quick', null).reason).to.equal('chat_mode_not_supported');
      expect(mode('web_search', 'a7').reason).to.equal('chat_mode_not_supported');
    });

    it('ASSIST1: the assistant’s Agent mode is durable where its conversation asks, a new one by its turn and an existing one by its record', () => {
      const agentMode = (chatMode: string, facts: Partial<RoutingFacts>) =>
        route({ ...open(), turn: { agentKey: null, chatMode, projectId: null }, facts: { ...open().facts, ...facts } });
      expect(agentMode('agent', {}).reason).to.equal('chat_mode_not_supported');
      expect(agentMode('agent', { requested: 'legacy' }).reason).to.equal('chat_mode_not_supported');
      expect(agentMode('agent', { requested: 'durable' }).path).to.equal('durable');
      expect(agentMode('agent:deep', { requested: 'durable' }).path).to.equal('durable');
      expect(agentMode('agent:planExecute', { requested: 'durable' }).reason).to.equal('chat_mode_not_supported');
      expect(agentMode('agent', { existing: true, requested: 'durable', placement: { durable: false, projectId: null } }).reason).to.equal('chat_mode_not_supported');
      expect(agentMode('agent', { existing: true, placement: { durable: true, projectId: null } }).path).to.equal('durable');
      expect(agentMode('agent', { existing: true, requested: 'durable', placement: null }).reason).to.equal('chat_mode_not_supported');
    });

    it('an org layer that narrows rollout.durable.modes moves that mode to legacy and no other', () => {
      const narrowed = { durable: { modes: ['auto', 'quick', 'react'] } };
      expect(mode('deep', 'a7', narrowed)).to.deep.equal({ path: 'legacy', reason: 'chat_mode_not_supported' });
      for (const chatMode of ['auto', 'quick', 'react']) expect(mode(chatMode, 'a7', narrowed).path, chatMode).to.equal('durable');
      for (const chatMode of RELEASE_DEFAULTS.rollout.durable.assistantModes) expect(mode(chatMode, null, narrowed).path, chatMode).to.equal('durable');
    });

    it('an org layer that names a mode the release does not is refused by the resolver, never routed durable', () => {
      expect(() => mode('planexecute', 'a7', { durable: { modes: ['planexecute', 'quick'] } })).to.throw(/widening rollout\.durable\.modes/);
    });
  });

  describe('the conversation (rows 8, 9)', () => {
    const placed = (placement: RoutingFacts['placement'], facts: Partial<RoutingFacts> = {}) =>
      route({ ...open(), facts: { ...open().facts, placement, ...facts } });

    it('a record in a project is project_scope, durable turns or not', () => {
      expect(placed({ durable: true, projectId: 'p-9' }).reason).to.equal('project_scope');
      expect(placed({ durable: false, projectId: 'p-9' }).reason).to.equal('project_scope');
    });

    it('with no worker seen, a durable conversation and a schedule’s firing stay durable', () => {
      expect(placed({ durable: true, projectId: null }, { workerLive: false }).path).to.equal('durable');
      expect(placed(null, { workerLive: false, scheduled: true }).path).to.equal('durable');
      expect(placed({ durable: false, projectId: null }, { workerLive: false }).reason).to.equal('no_live_worker');
    });
  });

  it('a config the rollout was never folded into routes nothing durable', () => {
    const bare: EffectiveRunConfig = { schemaVersion: 1, dimensions: {} };
    expect(routingOf(bare, FAMILIES, open().turn, open().facts)).to.deep.equal({ path: 'legacy', reason: 'org_disabled' });
    const noModes = resolved(open().rollout);
    const rest = { ...noModes.dimensions };
    delete rest['rollout.durable.modes'];
    expect(routingOf({ ...noModes, dimensions: rest }, FAMILIES, open().turn, open().facts).reason).to.equal('chat_mode_not_supported');
  });
});

describe('CPD-10 routing in the pin: the production pinner (CPD10-3)', () => {
  const MODES = ['quick', 'auto', 'react', 'deep', 'planExecute', 'internal_search', 'web_search', 'image'];
  const world = fc.record({
    killSwitch: fc.boolean(),
    orgEnabled: fc.boolean(),
    modes: fc.option(fc.subarray(['auto', 'deep', 'quick', 'react']), { nil: undefined }),
    modelFamilies: fc.option(fc.subarray(['anthropic', 'openai']), { nil: undefined }),
    agentKey: fc.constantFrom('a7', null),
    chatMode: fc.constantFrom(...MODES),
    projectId: fc.constantFrom(null, 'p-9'),
    facts: fc.record<RoutingFacts>({
      infrastructureReady: fc.boolean(),
      workerLive: fc.boolean(),
      scheduled: fc.boolean(),
      placement: fc.constantFrom(null, { durable: true, projectId: null }, { durable: false, projectId: null }, { durable: false, projectId: 'p-1' }),
      modelKey: fc.constantFrom(null, 'mk-claude', 'mk-gone'),
    }),
  });
  let head = 0;

  it('pin().routing and pin().scope equal route()’s for the same subject and snapshot, and are the table’s', async () => {
    await fc.assert(
      fc.asyncProperty(world, async (w) => {
        const rollout = {
          ENABLE_AGENT_HARNESS: w.orgEnabled,
          AGENT_HARNESS_KILL_SWITCH: w.killSwitch,
          ...(w.modes !== undefined || w.modelFamilies !== undefined
            ? { durable: { ...(w.modes !== undefined ? { modes: w.modes } : {}), ...(w.modelFamilies !== undefined ? { modelFamilies: w.modelFamilies } : {}) } }
            : {}),
        };
        const snapshot = aSnapshot({
          // A head of its own: the pinner keeps what it folded for a head.
          headRevision: (head += 1),
          agentKey: w.agentKey,
          families: FAMILIES,
          layers: { [`org:${ORG_A}`]: aLayer('org', { rollout }) },
        });
        const pinner = new PolicyRunConfigPinner({
          snapshots: new StaticSnapshots(snapshot),
          engine: new PolicyResolver(),
          effectiveSet: new InMemoryEffectiveSetResolver(),
          releases: releaseLayers(),
          prefix: prefixSources(),
          run: runSources(PARENT_DIMENSIONS),
          parent: PARENT_DIMENSIONS,
        });
        const subject = aSubject({ agentKey: w.agentKey, chatMode: w.chatMode, projectId: w.projectId, routingFacts: w.facts });
        const route = await pinner.route(subject);
        const pin = await pinner.pin(subject, route.basis);
        if (isProblem(pin)) return expect.fail(pin.code);
        expect(pin.routing).to.deep.equal(route.routing);
        expect(pin.scope).to.deep.equal(route.scope);
        expect(route.routing).to.deep.equal(routingOf(resolved(rollout), FAMILIES, { agentKey: w.agentKey, chatMode: w.chatMode, projectId: w.projectId }, w.facts));
        expect(Object.keys(route.config.dimensions).every((path) => path.startsWith('rollout.'))).to.equal(true);
        const child = await pinner.pin(aSubject({ agentKey: w.agentKey, surface: 'child', parent: pin, chatMode: 'deep', projectId: 'p-other' }));
        if (isProblem(child)) return expect.fail(child.code);
        expect(child.routing).to.deep.equal(pin.routing);
      }),
      { numRuns: 300, seed: 20261012 },
    );
  });

  it('a host that states no facts is routed on policy alone', async () => {
    const pinner = (rollout: Record<string, unknown>) =>
      new PolicyRunConfigPinner({
        snapshots: new StaticSnapshots(aSnapshot({ headRevision: (head += 1), layers: { [`org:${ORG_A}`]: aLayer('org', { rollout }) } })),
        engine: new PolicyResolver(),
        effectiveSet: new InMemoryEffectiveSetResolver(),
        releases: releaseLayers(),
        prefix: prefixSources(),
        run: runSources(PARENT_DIMENSIONS),
        parent: PARENT_DIMENSIONS,
      });
    expect((await pinner(open().rollout).route(aSubject())).routing).to.deep.equal({ path: 'durable', reason: null });
    expect((await pinner({ ...open().rollout, AGENT_HARNESS_KILL_SWITCH: true }).route(aSubject())).routing.reason).to.equal('kill_switch');
  });

  it('rows the org’s layer widens cannot be resolved: the route rejects, it is not guessed', async () => {
    const pinner = new PolicyRunConfigPinner({
      snapshots: new StaticSnapshots(
        aSnapshot({ headRevision: (head += 1), layers: { [`org:${ORG_A}`]: aLayer('org', { rollout: { ...open().rollout, durable: { modes: ['planexecute'] } } }) } }),
      ),
      engine: new PolicyResolver(),
      effectiveSet: new InMemoryEffectiveSetResolver(),
      releases: releaseLayers(),
      prefix: prefixSources(),
      run: runSources(PARENT_DIMENSIONS),
      parent: PARENT_DIMENSIONS,
    });
    const error = await pinner.route(aSubject({ routingFacts: open().facts })).catch((e: unknown) => e as { code?: string; message?: string });
    expect(error).to.deep.include({ code: 'CP_UNAVAILABLE' });
    expect((error as Error).message).to.match(/org layer's rollout\.durable\.modes/);
  });

  it('a deployment layer’s rollout rows are folded above the org’s', async () => {
    const pinner = new PolicyRunConfigPinner({
      snapshots: new StaticSnapshots(
        aSnapshot({
          headRevision: (head += 1),
          layers: {
            deployment: aLayer('deployment', { rollout: { durable: { modes: ['auto', 'quick'] } } }),
            [`org:${ORG_A}`]: aLayer('org', { rollout: { ...open().rollout, durable: { modes: ['quick'] } } }),
          },
        }),
      ),
      engine: new PolicyResolver(),
      effectiveSet: new InMemoryEffectiveSetResolver(),
      releases: releaseLayers(),
      prefix: prefixSources(),
      run: runSources(PARENT_DIMENSIONS),
      parent: PARENT_DIMENSIONS,
    });
    const route = await pinner.route(aSubject({ chatMode: 'auto', routingFacts: open().facts }));
    expect(route.routing).to.deep.equal({ path: 'legacy', reason: 'chat_mode_not_supported' });
    expect(route.config.dimensions['rollout.durable.modes']).to.deep.include({ value: ['quick'], setBy: 'org' });
  });
});
