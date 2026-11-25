/**
 * MA1 (QA37-U1, U3, U7): which model a run is pinned to. The main model is the
 * nearest layer's choice inside the org's routes; an org route list narrows
 * the agent's role models and never adds one; a child's model, set by its role
 * or inherited, is held to its role's cost ceiling. The production pinner over
 * CPD-3's resolver, the agent layer built from a definition as admission does.
 */
import { expect } from 'chai';
import { roleCostBound } from '../../../src/modules/agent-harness/domain/child-model';
import { usdPerMTok } from '../../../src/modules/agent-harness/domain/model-prices-release';
import { definitionLayer as agentLayerOf, releaseLayers } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { PARENT_DIMENSIONS, PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import { prefixSources, runSources } from '../../../src/modules/control-plane/application/layer-sources';
import { definitionLayer } from '../../../src/modules/control-plane/application/model-sources';
import {
  PolicyRunConfigPinner,
  type PolicyRunConfigPinnerOptions,
} from '../../../src/modules/control-plane/application/run-config-pinner';
import { selectLlm, type ConfiguredLlm } from '../../../src/modules/control-plane/domain/llm';
import type { PolicyLayer } from '../../../src/modules/control-plane/domain/policy-alias';
import { isProblem } from '../../../src/modules/control-plane/domain/problem';
import type { PinSubject, ResolvedRunConfig, Snapshot } from '../../../src/modules/control-plane/domain/resolution';
import { agentDefinition } from '../agent-harness/support/agent-definition';
import { ORG_A } from './support/cp-objects';
import { InMemoryEffectiveSetResolver } from './support/in-memory-resolution';
import { StaticSnapshots } from './support/pinner-contract';
import { aLayer, aSnapshot, aSubject } from './support/resolution-fixtures';

const llm = (modelKey: string, provider: string, names: string[], isDefault = false): ConfiguredLlm => ({
  modelKey,
  provider,
  modelNames: names,
  isDefault,
  configDigest: `sha256:${modelKey.replace(/[^0-9a-f]/g, '0').padEnd(64, '0')}`,
});
/** A: the agent's main model (17.5 per 1M); B: a cheap one (6, Claude Haiku 4.5 at its listed rate since main #4392); C: 5.25; D: one the release does not price. */
const MODELS = [
  llm('mk-a', 'openai', ['gpt-5.4'], true),
  llm('mk-b', 'anthropic', ['claude-haiku-4-5']),
  llm('mk-c', 'openai', ['gpt-5.4-mini']),
  llm('mk-d', 'ollama', ['house-model']),
];
const A = 'openai:gpt-5.4';
const B = 'anthropic:claude-haiku-4-5';
const C = 'openai:gpt-5.4-mini';
const D = 'ollama:house-model';
const ORG = `org:${ORG_A}`;

const DEFINITION = agentDefinition({
  agentKey: 'a7',
  models: [{ modelKey: 'mk-a', modelName: 'gpt-5.4', provider: 'openAI', isReasoning: true }],
  roleModels: {
    researcher: { modelKey: 'mk-b', modelName: 'claude-haiku-4-5', provider: 'Anthropic' },
    writer: { modelKey: 'mk-c', modelName: 'gpt-5.4-mini', provider: 'openAI' },
  },
});

const world = (layers: Record<string, PolicyLayer> = {}, models: readonly ConfiguredLlm[] = MODELS): Snapshot =>
  aSnapshot({ models, layers });

const pinnerOver = (snapshot: Snapshot, options: Partial<PolicyRunConfigPinnerOptions<unknown>> = {}) =>
  new PolicyRunConfigPinner({
    snapshots: new StaticSnapshots(snapshot),
    engine: new PolicyResolver(),
    effectiveSet: new InMemoryEffectiveSetResolver(),
    releases: releaseLayers(),
    prefix: prefixSources(),
    run: runSources(PARENT_DIMENSIONS),
    parent: PARENT_DIMENSIONS,
    prices: usdPerMTok,
    roleBound: roleCostBound,
    ...options,
  } as PolicyRunConfigPinnerOptions<never>);

const pinned = (result: unknown): ResolvedRunConfig => {
  expect(isProblem(result), JSON.stringify(result)).to.equal(false);
  return result as ResolvedRunConfig;
};
const dimension = (pin: ResolvedRunConfig, path: string) => pin.effectiveConfig.dimensions[path];
const root = (overrides: Partial<PinSubject> = {}): PinSubject => aSubject({ agent: agentLayerOf(DEFINITION)!, ...overrides });
const requesting = (route: string): PolicyLayer => aLayer('request', { models: { primary: route } });

/** A child of `parent`: with `role`, its role set `route`; without, it inherits. */
const child = (
  parent: ResolvedRunConfig,
  roleKeys: string[],
  role?: { route: string; modelKey: string },
): PinSubject => {
  if (role === undefined) return aSubject({ surface: 'child', parent, roleKeys });
  const colon = role.route.indexOf(':');
  return aSubject({
    surface: 'child',
    parent,
    roleKeys,
    request: requesting(role.route),
    model: { modelKey: role.modelKey, modelName: role.route.slice(colon + 1), provider: role.route.slice(0, colon) },
  });
};

describe('MA1 the main model: org → agent → user → request (QA37-U1)', () => {
  it('QA37-U1: the agent’s first model is the choice when no nearer layer makes one', async () => {
    const pin = pinned(await pinnerOver(world()).pin(root()));
    expect(dimension(pin, 'models.primary')).to.deep.include({ value: A, setBy: 'agent' });
    expect(pin.llm).to.deep.include({ modelKey: 'mk-a', provider: 'openai', model: 'gpt-5.4' });
  });

  it('QA37-U1: the actor’s stored choice is nearer than the agent’s', async () => {
    const pin = pinned(await pinnerOver(world({ 'user:u3': aLayer('user', { models: { primary: B } }) })).pin(root()));
    expect(dimension(pin, 'models.primary')).to.deep.include({ value: B, setBy: 'user' });
  });

  it('QA37-U1: the model the turn names is nearest, and the pin’s `llm` is its configured entry', async () => {
    const pin = pinned(
      await pinnerOver(world({ 'user:u3': aLayer('user', { models: { primary: B } }) })).pin(
        root({ request: requesting(C), model: { modelKey: 'mk-c', modelName: 'gpt-5.4-mini' } }),
      ),
    );
    expect(dimension(pin, 'models.primary')).to.deep.include({ value: C, setBy: 'request' });
    expect(pin.llm).to.deep.include({ modelKey: 'mk-c', model: 'gpt-5.4-mini' });
  });

  it('QA37-U1: the org’s routes bound every layer’s choice: outside them a request is refused CP_OUTSIDE_CEILING at `models.primary`', async () => {
    const refused = await pinnerOver(world()).pin(root({ request: requesting(D) }));
    expect(refused).to.deep.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.primary', layer: 'request' });
  });

  it('QA37-U1: a stored choice outside the routes is refused too, naming the actor’s layer', async () => {
    const refused = await pinnerOver(world({ 'user:u3': aLayer('user', { models: { primary: D } }) })).pin(root());
    expect(refused).to.deep.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.primary', layer: 'user' });
  });

  it('QA37-U1: a model the org’s stored list leaves out is not a choice, although the deployment configures it', async () => {
    const snapshot = world({ [ORG]: aLayer('org', { models: { routes: [A, B] } }) });
    const refused = await pinnerOver(snapshot).pin(
      aSubject({ request: requesting(C), model: { modelKey: 'mk-c', modelName: 'gpt-5.4-mini' } }),
    );
    expect(refused).to.deep.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.primary', layer: 'request' });
    const allowed = pinned(await pinnerOver(snapshot).pin(aSubject({ request: requesting(B), model: { modelKey: 'mk-b' } })));
    expect(dimension(allowed, 'models.primary')).to.deep.include({ value: B, setBy: 'request' });
  });
});

describe('MA1 the org narrows an agent’s role models, never widens them (QA37-U3)', () => {
  it('QA37-U3: with every model allowed the pin holds each role’s model, inside the agent’s routes', async () => {
    const pin = pinned(await pinnerOver(world()).pin(root()));
    expect(dimension(pin, 'models.roles')).to.deep.include({ value: { researcher: B, writer: C }, setBy: 'agent' });
    expect(dimension(pin, 'models.routes')?.value).to.deep.equal([B, A, C].sort());
  });

  it('QA37-U3: an org list that leaves a role’s model out drops that role and keeps the others; the agent still runs', async () => {
    const pin = pinned(await pinnerOver(world({ [ORG]: aLayer('org', { models: { routes: [A, C] } }) })).pin(root()));
    expect(dimension(pin, 'models.roles')?.value).to.deep.equal({ writer: C });
    expect(dimension(pin, 'models.routes')?.value).to.deep.equal([A, C]);
    expect(dimension(pin, 'models.primary')?.value).to.equal(A);
  });

  it('QA37-U3: an org list that leaves every role’s model out leaves no role model; the agent still runs', async () => {
    const pin = pinned(await pinnerOver(world({ [ORG]: aLayer('org', { models: { routes: [A] } }) })).pin(root()));
    expect(dimension(pin, 'models.roles')).to.equal(undefined);
    expect(dimension(pin, 'models.primary')?.value).to.equal(A);
  });

  it('QA37-U3: a model the deployment no longer configures is dropped the same way', async () => {
    const pin = pinned(await pinnerOver(world({}, MODELS.filter((model) => model.modelKey !== 'mk-b'))).pin(root()));
    expect(dimension(pin, 'models.roles')?.value).to.deep.equal({ writer: C });
  });

  it('QA37-U3: an agent that lists no models keeps the org’s routes and the roles inside them', async () => {
    const definition = agentLayerOf({ ...DEFINITION, models: [] })!;
    const pin = pinned(
      await pinnerOver(world({ [ORG]: aLayer('org', { models: { routes: [A, C] } }) })).pin(aSubject({ agent: definition })),
    );
    expect(dimension(pin, 'models.roles')?.value).to.deep.equal({ writer: C });
    expect(dimension(pin, 'models.routes')?.value).to.deep.equal([A, C]);
  });

  it('QA37-U3: narrowing never adds: no role, route or main model appears that the definition did not name', () => {
    const subject = root();
    const narrowed = definitionLayer(subject, world({ [ORG]: aLayer('org', { models: { routes: [A, C, D] } }) }), []);
    expect(narrowed?.models).to.deep.equal({ routes: [A, C], primary: A, roles: { writer: C } });
  });

  it('QA37-U3: a definition nothing is dropped from is handed on as it is (no copy, no other bytes)', () => {
    const subject = root();
    expect(definitionLayer(subject, world(), [])).to.equal(subject.agent);
    const plain = aSubject({ agent: agentLayerOf(agentDefinition({ agentKey: 'a7', models: DEFINITION.models }))! });
    expect(definitionLayer(plain, world(), [])).to.equal(plain.agent);
  });

  it('QA37-U3: a definition none of whose models is allowed is left to the fold, which refuses the agent (as before)', async () => {
    const refused = await pinnerOver(world({ [ORG]: aLayer('org', { models: { routes: [D] } }) })).pin(root());
    expect(refused).to.deep.include({ code: 'CP_WIDENING', dimension: 'models.routes', layer: 'agent' });
  });
});

describe('MA1 a model named by its route is told apart by provider (QA37-U1)', () => {
  const TWO = [llm('mk-1', 'openai', ['shared-model']), llm('mk-2', 'azureopenai', ['shared-model'])];
  const cases: Array<[string, Parameters<typeof selectLlm>[1], string | null]> = [
    ['a name alone: the first entry that lists it', { modelName: 'shared-model' }, 'mk-1'],
    ['a name and a provider: that provider’s entry', { modelName: 'shared-model', provider: 'azureopenai' }, 'mk-2'],
    ['a provider nobody serves the name under: nothing', { modelName: 'shared-model', provider: 'anthropic' }, null],
    ['a key and its own provider: that entry', { modelKey: 'mk-2', modelName: 'shared-model', provider: 'azureopenai' }, 'mk-2'],
    ['a key under another provider: nothing, never the other entry', { modelKey: 'mk-1', modelName: 'shared-model', provider: 'azureopenai' }, null],
    ['a provider alone names no model: the default entry', { provider: 'azureopenai' }, 'mk-1'],
  ];
  for (const [name, choice, expected] of cases) {
    it(`QA37-U1: ${name}`, () => {
      expect(selectLlm(TWO, choice)?.modelKey ?? null).to.equal(expected);
    });
  }
});

describe('MA1 a child’s model is held to its role’s cost ceiling (QA37-U7)', () => {
  const ceilingWorld = (roleCostCeilings: Record<string, number> | undefined, models = MODELS): Snapshot =>
    world(roleCostCeilings === undefined ? {} : { [ORG]: aLayer('org', { models: { roleCostCeilings } }) }, models);
  const parentIn = async (snapshot: Snapshot): Promise<ResolvedRunConfig> => pinned(await pinnerOver(snapshot).pin(root()));
  const RESEARCHER = { route: B, modelKey: 'mk-b' };

  it('QA37-U7: a role’s model that costs less than its ceiling is pinned, set by the request, with its own `llm`', async () => {
    const snapshot = ceilingWorld({ researcher: 7 });
    const pin = pinned(await pinnerOver(snapshot).pin(child(await parentIn(snapshot), ['researcher', 'default'], RESEARCHER)));
    expect(dimension(pin, 'models.primary')).to.deep.include({ value: B, setBy: 'request' });
    expect(pin.llm).to.deep.include({ modelKey: 'mk-b', provider: 'anthropic', model: 'claude-haiku-4-5' });
    expect(dimension(pin, 'models.roleCostCeilings')?.value).to.deep.equal({ researcher: 7 });
  });

  it('QA37-U7: a model that costs exactly its ceiling is allowed', async () => {
    const snapshot = ceilingWorld({ researcher: 6 });
    pinned(await pinnerOver(snapshot).pin(child(await parentIn(snapshot), ['researcher', 'default'], RESEARCHER)));
  });

  it('QA37-U7: a model that costs more is refused CP_OUTSIDE_CEILING at `models.roleCostCeilings`, naming the request', async () => {
    const snapshot = ceilingWorld({ researcher: 5.99 });
    const refused = await pinnerOver(snapshot).pin(child(await parentIn(snapshot), ['researcher', 'default'], RESEARCHER));
    expect(refused).to.deep.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.roleCostCeilings', layer: 'request' });
  });

  it('QA37-U7: `default` bounds every role: the least of a role’s own bound and `default` decides', async () => {
    const snapshot = ceilingWorld({ researcher: 50, default: 4 });
    const refused = await pinnerOver(snapshot).pin(child(await parentIn(snapshot), ['researcher', 'default'], RESEARCHER));
    expect(refused).to.deep.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.roleCostCeilings' });
    const other = ceilingWorld({ writer: 1, default: 7 });
    pinned(await pinnerOver(other).pin(child(await parentIn(other), ['researcher', 'default'], RESEARCHER)));
  });

  it('QA37-U7: the nearest of a spawn’s role keys does not hide a lower bound on another of them', async () => {
    const snapshot = ceilingWorld({ jira_agent: 50, researcher: 4 });
    const refused = await pinnerOver(snapshot).pin(
      child(await parentIn(snapshot), ['jira_agent', 'researcher', 'default'], RESEARCHER),
    );
    expect(refused).to.deep.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.roleCostCeilings' });
  });

  it('QA37-U7: no ceiling for the role: allowed, and no price is looked up', async () => {
    let lookups = 0;
    const prices = (model: string): number | null => ((lookups += 1), usdPerMTok(model));
    for (const snapshot of [ceilingWorld(undefined), ceilingWorld({ writer: 1 })]) {
      const parent = pinned(await pinnerOver(snapshot, { prices }).pin(root()));
      pinned(await pinnerOver(snapshot, { prices }).pin(child(parent, ['researcher', 'default'], RESEARCHER)));
    }
    expect(lookups).to.equal(0);
  });

  it('QA37-U7: a model with no price cannot be shown to be within a ceiling: refused, however high the ceiling', async () => {
    const definition = agentLayerOf({
      ...DEFINITION,
      roleModels: { researcher: { modelKey: 'mk-d', modelName: 'house-model', provider: 'ollama' } },
    })!;
    const snapshot = ceilingWorld({ default: 1_000_000 });
    const parent = pinned(await pinnerOver(snapshot).pin(aSubject({ agent: definition })));
    const refused = await pinnerOver(snapshot).pin(child(parent, ['researcher', 'default'], { route: D, modelKey: 'mk-d' }));
    expect(refused).to.deep.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.roleCostCeilings', layer: 'request' });
    const free = ceilingWorld(undefined);
    const unbound = pinned(await pinnerOver(free).pin(aSubject({ agent: definition })));
    pinned(await pinnerOver(free).pin(child(unbound, ['researcher', 'default'], { route: D, modelKey: 'mk-d' })));
  });

  it('QA37-U7: an inherited model is held to the ceiling too, and the refusal names the parent', async () => {
    const snapshot = ceilingWorld({ default: 10 });
    const parent = await parentIn(snapshot);
    const refused = await pinnerOver(snapshot).pin(child(parent, ['writer', 'default']));
    expect(refused).to.deep.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.roleCostCeilings', layer: 'parent' });
    const roomy = ceilingWorld({ default: 17.5 });
    const inherited = pinned(await pinnerOver(roomy).pin(child(await parentIn(roomy), ['writer', 'default'])));
    expect(inherited.llm).to.deep.include({ modelKey: 'mk-a', model: 'gpt-5.4' });
    expect(dimension(inherited, 'models.primary')).to.deep.include({ value: A, setBy: 'parent' });
  });

  it('QA37-U7: a child whose parent has no configured model has no price to show under a ceiling', async () => {
    const snapshot = ceilingWorld({ default: 100 }, []);
    const parent = pinned(await pinnerOver(snapshot).pin(aSubject()));
    expect(parent.llm).to.equal(null);
    const refused = await pinnerOver(snapshot).pin(child(parent, ['default']));
    expect(refused).to.deep.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.roleCostCeilings', layer: 'parent' });
  });

  it('QA37-U7: the ceiling bounds sub-agents only: a root above it is pinned, and its pin looks up no price', async () => {
    let lookups = 0;
    let bounds = 0;
    const pinner = pinnerOver(ceilingWorld({ default: 1 }), {
      prices: (model) => ((lookups += 1), usdPerMTok(model)),
      roleBound: (ceilings, keys) => ((bounds += 1), roleCostBound(ceilings, keys)),
    });
    const pin = pinned(await pinner.pin(root()));
    expect(dimension(pin, 'models.roleCostCeilings')).to.deep.include({ value: { default: 1 }, setBy: 'org' });
    expect([lookups, bounds]).to.deep.equal([0, 0]);
  });

  it('QA37-U7: a pinner that was given no way to read a ceiling refuses a child under one (never unchecked)', async () => {
    const snapshot = ceilingWorld({ default: 100 });
    const parent = await parentIn(snapshot);
    const refused = await pinnerOver(snapshot, { roleBound: undefined }).pin(child(parent, ['researcher', 'default'], RESEARCHER));
    expect(refused).to.deep.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.roleCostCeilings' });
    const unpriced = await pinnerOver(snapshot, { prices: undefined }).pin(child(parent, ['researcher', 'default'], RESEARCHER));
    expect(unpriced).to.deep.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.roleCostCeilings' });
  });

  it('QA37-U7: a role’s model the parent’s routes do not hold is refused at `models.primary` before any price is read', async () => {
    const snapshot = ceilingWorld({ default: 100 });
    const parent = await parentIn(snapshot);
    const refused = await pinnerOver(snapshot).pin(child(parent, ['researcher', 'default'], { route: D, modelKey: 'mk-d' }));
    expect(refused).to.deep.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.primary', layer: 'request' });
  });

  it('QA37-U7: a role’s model the deployment dropped since the parent’s pin is refused, never another model', async () => {
    const parent = await parentIn(ceilingWorld(undefined));
    const later = ceilingWorld(undefined, MODELS.filter((model) => model.modelKey !== 'mk-b'));
    const refused = await pinnerOver(later).pin(child(parent, ['researcher', 'default'], RESEARCHER));
    expect(refused).to.deep.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.primary', layer: 'request' });
  });

  it('QA37-U5: a grandchild without a role model takes its own parent’s, so a role’s model carries down', async () => {
    const snapshot = ceilingWorld(undefined);
    const first = pinned(await pinnerOver(snapshot).pin(child(await parentIn(snapshot), ['researcher', 'default'], RESEARCHER)));
    const second = pinned(await pinnerOver(snapshot).pin(child(first, ['helper', 'default'])));
    expect(second.llm).to.deep.equal(first.llm);
    expect(dimension(second, 'models.primary')).to.deep.include({ value: B, setBy: 'parent' });
    expect(dimension(second, 'models.roles')?.value).to.deep.equal({ researcher: B, writer: C });
  });
});

describe('G-41 GRP-27: an engine role on a model outside the person’s groups calls the run’s own model', () => {
  const rolesWorld = (): Snapshot => world({ [ORG]: aLayer('org', { models: { roles: { fast: B, compaction: C } } }) });
  const rolesOf = (pin: ResolvedRunConfig) => dimension(pin, 'models.roles')?.value;

  it('GRP-27: a role the groups refuse is left out of the pin; the others stay; nothing changes without groups', async () => {
    const asked: string[] = [];
    const modelGroups = async (_orgId: string, actorId: string, modelKey: string) => (asked.push(`${actorId}:${modelKey}`), modelKey !== 'mk-b');
    expect(rolesOf(pinned(await pinnerOver(rolesWorld(), { modelGroups }).pin(aSubject({}))))).to.deep.equal({ compaction: C });
    expect(asked.map((entry) => entry.split(':')[1]).sort()).to.deep.equal(['mk-b', 'mk-c']);
    expect(rolesOf(pinned(await pinnerOver(rolesWorld()).pin(aSubject({}))))).to.deep.equal({ fast: B, compaction: C });
  });

  it('GRP-27: a read that fails leaves every engine role out (fail closed), and the run calls its own model', async () => {
    const modelGroups = async () => Promise.reject(new Error('groups unreadable'));
    const pin = pinned(await pinnerOver(rolesWorld(), { modelGroups }).pin(aSubject({})));
    expect(rolesOf(pin)).to.deep.equal({});
    expect(pin.llm).to.deep.include({ modelKey: 'mk-a' });
  });
});

describe('GA-15 MROLE-10: an engine role’s model is held to its role’s cost ceiling at every admission', () => {
  const rolesWorld = (roles: Record<string, string>, roleCostCeilings?: Record<string, number>): Snapshot =>
    world({
      [ORG]: aLayer('org', { models: { roles, ...(roleCostCeilings !== undefined ? { roleCostCeilings } : {}) } }),
    });

  // The built-in assistant: no agent layer, whose own role models would be the nearest `models.roles` choice.
  const assistant = (): PinSubject => aSubject({});

  it('MROLE-10: a fast model within its ceiling is pinned with the run, under `models.roles`', async () => {
    const pin = pinned(await pinnerOver(rolesWorld({ fast: B }, { fast: 7 })).pin(assistant()));
    expect((dimension(pin, 'models.roles')?.value as Record<string, string>)['fast']).to.equal(B);
  });

  it('MROLE-10: one above its own ceiling, or above `default`, refuses the run at `models.roleCostCeilings`', async () => {
    for (const ceilings of [{ fast: 4 }, { default: 4 }, { fast: 50, default: 4 }]) {
      const refused = await pinnerOver(rolesWorld({ fast: B }, ceilings)).pin(assistant());
      expect(refused, JSON.stringify(ceilings)).to.deep.include({
        code: 'CP_OUTSIDE_CEILING',
        dimension: 'models.roleCostCeilings',
      });
    }
  });

  it('MROLE-10: a mapped compaction model with no price cannot be shown within a ceiling: refused', async () => {
    const refused = await pinnerOver(rolesWorld({ compaction: D }, { default: 100 })).pin(assistant());
    expect(refused).to.deep.include({ code: 'CP_OUTSIDE_CEILING', dimension: 'models.roleCostCeilings' });
  });

  it('MROLE-10: no ceiling, or a ceiling only for another role: pinned, and no price is looked up', async () => {
    let lookups = 0;
    const prices = (model: string): number | null => ((lookups += 1), usdPerMTok(model));
    pinned(await pinnerOver(rolesWorld({ fast: D }), { prices }).pin(assistant()));
    pinned(await pinnerOver(rolesWorld({ fast: D }, { researcher: 1 }), { prices }).pin(assistant()));
    expect(lookups).to.equal(0);
  });
});
