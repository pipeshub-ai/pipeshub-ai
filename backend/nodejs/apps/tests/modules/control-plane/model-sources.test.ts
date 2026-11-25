/**
 * CPD-6 CPD6-12, CPD6-17, CPD6-18: which configured model a run gets and
 * which models it may name, as pure reads of a snapshot. The pinner folds
 * these layers; `pin-complete.test.ts` holds the same rules through an
 * admission.
 */
import { expect } from 'chai';
import {
  allowedRoutes,
  definitionLayer,
  modelsCeilingLayer,
  namesUnconfiguredModel,
  requestLayer,
} from '../../../src/modules/control-plane/application/model-sources';
import { prefixSources } from '../../../src/modules/control-plane/application/layer-sources';
import { configuredRoutes, selectLlm, type ConfiguredLlm } from '../../../src/modules/control-plane/domain/llm';
import type { PolicyLayer } from '../../../src/modules/control-plane/domain/policy-alias';
import type { PinSubject } from '../../../src/modules/control-plane/domain/resolution';
import { aSnapshot, aSubject } from './support/resolution-fixtures';

const llm = (modelKey: string, provider: string, names: string[], isDefault = false): ConfiguredLlm => ({
  modelKey,
  provider,
  modelNames: names,
  isDefault,
  configDigest: `sha256:${modelKey.padEnd(64, '0')}`,
});
const MODELS = [llm('k1', 'openai', ['gpt-x', 'gpt-y']), llm('k2', 'anthropic', ['claude-x'], true), llm('k3', 'azure', [])];
const ALIAS = ['host:default'];
const request = (primary?: string): PolicyLayer => ({
  schemaVersion: 1,
  scope: { kind: 'request' },
  ...(primary !== undefined ? { models: { primary } } : {}),
});
const subject = (overrides: Partial<PinSubject> = {}): PinSubject => ({ ...aSubject(), ...overrides });

describe('CPD-6 the configured model of a run (CPD6-12)', () => {
  const cases: Array<[string, Parameters<typeof selectLlm>[1], [string, string] | null]> = [
    ['nothing named: the default entry, its first name', {}, ['k2', 'claude-x']],
    ['a key: that entry, its first name', { modelKey: 'k1' }, ['k1', 'gpt-x']],
    ['a key and a name the entry lists: exactly them', { modelKey: 'k1', modelName: 'gpt-y' }, ['k1', 'gpt-y']],
    ['a key and a name the entry does not list: nothing', { modelKey: 'k1', modelName: 'claude-x' }, null],
    ['a key nobody has: nothing, never the first entry', { modelKey: 'gone' }, null],
    ['a name alone: the entry that lists it', { modelName: 'gpt-y' }, ['k1', 'gpt-y']],
    ['a name nobody lists: nothing', { modelName: 'gpt-z' }, null],
    ['an entry that names no model: nothing', { modelKey: 'k3' }, null],
  ];
  for (const [name, choice, expected] of cases) {
    it(name, () => {
      const selected = selectLlm(MODELS, choice);
      expect(selected === null ? null : [selected.modelKey, selected.model]).to.deep.equal(expected);
    });
  }

  it('no default marked: the first entry; nothing configured: nothing', () => {
    expect(selectLlm([llm('k1', 'openai', ['gpt-x'])])?.modelKey).to.equal('k1');
    expect(selectLlm([])).to.equal(null);
    expect(selectLlm([], { modelKey: 'k1' })).to.equal(null);
  });

  it('the routes of the configured models, each once, sorted', () => {
    expect(configuredRoutes([...MODELS, llm('k4', 'openai', ['gpt-x'])])).to.deep.equal(['anthropic:claude-x', 'openai:gpt-x', 'openai:gpt-y']);
  });
});

describe('CPD-6 the deployment’s layers of a subject (CPD6-14)', () => {
  const deployment = (approvals: PolicyLayer['approvals']): PolicyLayer => ({ schemaVersion: 1, scope: { kind: 'deployment' }, approvals });
  const layers = {
    deployment: deployment({ writes: 'ask' }),
    'deployment:flow': deployment({ flow: { level: 'ask', above: 'public' } }),
    'deployment:agent:a7': deployment({ writes: 'deny' }),
    'deployment:agent:other': deployment({ classes: { read: 'deny' } }),
  };
  const folded = (overrides: Partial<PinSubject>) =>
    prefixSources()
      .filter((source) => source.kind === 'deployment')
      .map((source) => source.layer(subject(overrides), aSnapshot({ layers })));

  it('an agent’s run folds the default, the flow rule and the rules for that agent alone', () => {
    expect(folded({ agentKey: 'a7' })).to.deep.equal([layers.deployment, layers['deployment:flow'], layers['deployment:agent:a7']]);
  });

  it('a run of no agent folds no agent’s rules', () => {
    expect(folded({ agentKey: null })).to.deep.equal([layers.deployment, layers['deployment:flow'], null]);
  });
});

describe('CPD-6 the configured-models ceiling (CPD6-17, CPD6-18)', () => {
  const snapshot = aSnapshot({ models: MODELS });

  it('allows every configured model, the host’s aliases and what the host verified elsewhere', () => {
    expect(allowedRoutes(subject({ verifiedRoutes: ['vendor:own-key'] }), snapshot, ALIAS)).to.deep.equal([
      'anthropic:claude-x',
      'host:default',
      'openai:gpt-x',
      'openai:gpt-y',
      'vendor:own-key',
    ]);
  });

  it('is narrowed by the org’s stored routes and never widened by them', () => {
    const narrowed = aSnapshot({
      models: MODELS,
      layers: { [`org:${aSubject().orgId}`]: { schemaVersion: 1, scope: { kind: 'org' }, models: { routes: ['openai:gpt-y', 'vendor:other'] } } },
    });
    expect(allowedRoutes(subject(), narrowed, ALIAS)).to.deep.equal(['openai:gpt-y']);
    expect(modelsCeilingLayer(subject(), narrowed, ALIAS)).to.deep.equal({
      schemaVersion: 1,
      scope: { kind: 'org' },
      models: { routes: ['openai:gpt-y'] },
    });
  });

  it('is absent while no model is configured', () => {
    expect(allowedRoutes(subject(), aSnapshot(), ALIAS)).to.equal(null);
    expect(modelsCeilingLayer(subject(), aSnapshot(), ALIAS)).to.equal(null);
  });

  it('an agent’s definition keeps the models still allowed, its default moved to one of them', () => {
    const agent: PolicyLayer = {
      schemaVersion: 1,
      scope: { kind: 'agent' },
      models: { routes: ['openai:gone', 'anthropic:claude-x', 'openai:gpt-y'], primary: 'openai:gone' },
    };
    expect(definitionLayer(subject({ agent }), snapshot, ALIAS)?.models).to.deep.equal({
      routes: ['anthropic:claude-x', 'openai:gpt-y'],
      primary: 'anthropic:claude-x',
    });
    const kept: PolicyLayer = { ...agent, models: { routes: ['openai:gpt-x'], primary: 'openai:gpt-x' } };
    expect(definitionLayer(subject({ agent: kept }), snapshot, ALIAS)).to.equal(kept);
    const none: PolicyLayer = { ...agent, models: { routes: ['openai:gone'] } };
    expect(definitionLayer(subject({ agent: none }), snapshot, ALIAS), 'left for the fold to refuse').to.equal(none);
    expect(definitionLayer(subject({ agent }), aSnapshot(), ALIAS)).to.equal(agent);
    const plain: PolicyLayer = { schemaVersion: 1, scope: { kind: 'agent' } };
    expect(definitionLayer(subject({ agent: plain }), snapshot, ALIAS)).to.equal(plain);
    expect(definitionLayer(subject(), snapshot, ALIAS)).to.equal(null);
  });

  it('a request’s model is named as the deployment configures it', () => {
    const named = subject({ request: request('unknown:k1'), model: { modelKey: 'k1' } });
    expect(requestLayer(named, snapshot).models?.primary).to.equal('openai:gpt-x');
    const unnamed = subject({ request: request('host:default') });
    expect(requestLayer(unnamed, snapshot)).to.equal(unnamed.request);
    const verified = subject({ request: request('vendor:own-key'), model: { modelKey: 'k1' }, verifiedRoutes: ['vendor:own-key'] });
    expect(requestLayer(verified, snapshot)).to.equal(verified.request);
    const noChoice = subject({ request: request(), model: { modelKey: 'k1' } });
    expect(requestLayer(noChoice, snapshot)).to.equal(noChoice.request);
  });

  it('a named model that is not configured is told apart from a route that reads as one', () => {
    const stale = subject({ request: request('openai:gpt-x'), model: { modelKey: 'gone', modelName: 'gpt-x' } });
    expect(namesUnconfiguredModel(stale, snapshot)).to.equal(true);
    expect(requestLayer(stale, snapshot)).to.equal(stale.request);
    expect(namesUnconfiguredModel(subject({ request: request('openai:gpt-x'), model: { modelKey: 'k1' } }), snapshot)).to.equal(false);
    expect(namesUnconfiguredModel(subject({ request: request('host:default') }), snapshot)).to.equal(false);
    expect(namesUnconfiguredModel(stale, aSnapshot()), 'nothing configured: the worker says so').to.equal(false);
    const verified = subject({ request: request('vendor:own-key'), model: { modelKey: 'gone' }, verifiedRoutes: ['vendor:own-key'] });
    expect(namesUnconfiguredModel(verified, snapshot)).to.equal(false);
  });
});
