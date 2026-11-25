/**
 * MA1 (QA37-U2): the agent's definition as an `agent` layer. `roleModels`
 * become `models.roles`, each inside the routes the layer grants; a definition
 * without them gives the layer it gave before this release, byte for byte.
 */
import { expect } from 'chai';
import { canonicalJson } from '../../../src/libs/utils/canonical-json';
import { agentRunDefinitionSchema, type AgentRunDefinition } from '../../../src/modules/agent-harness/domain/agent-definition';
import { definitionLayer } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { agentDefinition } from './support/agent-definition';

const HAIKU = { modelKey: 'model-3', modelName: 'claude-haiku-4-5', provider: 'Anthropic' };
const MINI = { modelKey: 'model-4', modelName: 'gpt-5.4-mini', provider: 'openAI' };

/** The layer as it was built before MA1 (the expected bytes of a definition without role models). */
const before = (definition: AgentRunDefinition): unknown => {
  const routes = definition.models.map((model) => `${model.provider.trim().toLowerCase()}:${model.modelName}`);
  return {
    schemaVersion: 1,
    scope: { kind: 'agent' },
    ...(definition.engineProfile != null ? { engineProfile: definition.engineProfile } : {}),
    ...(routes.length > 0 ? { models: { routes: [...new Set(routes)].sort(), primary: routes[0] } } : {}),
  };
};

describe('MA1 the definition as an agent layer (QA37-U2)', () => {
  it('QA37-U2: role models become `models.roles`, named as the deployment configures the provider, inside the granted routes', () => {
    const layer = definitionLayer(agentDefinition({ roleModels: { researcher: HAIKU, default: MINI } }));
    expect(layer?.models).to.deep.equal({
      routes: ['anthropic:claude-haiku-4-5', 'anthropic:claude-x', 'openai:gpt-5.4-mini', 'openai:gpt-x'],
      primary: 'openai:gpt-x',
      roles: { researcher: 'anthropic:claude-haiku-4-5', default: 'openai:gpt-5.4-mini' },
    });
  });

  it('QA37-U2: a role on a model the agent already grants adds no route', () => {
    const layer = definitionLayer(
      agentDefinition({ roleModels: { researcher: { modelKey: 'model-2', modelName: 'claude-x', provider: 'anthropic' } } }),
    );
    expect(layer?.models?.routes).to.deep.equal(['anthropic:claude-x', 'openai:gpt-x']);
    expect(layer?.models?.roles).to.deep.equal({ researcher: 'anthropic:claude-x' });
  });

  it('QA37-U2: an agent that lists no models grants no routes (the org’s ceiling stays) and still names its roles', () => {
    const layer = definitionLayer(agentDefinition({ models: [], roleModels: { default: MINI } }));
    expect(layer?.models).to.deep.equal({ roles: { default: 'openai:gpt-5.4-mini' } });
  });

  const fixtures: Array<[string, AgentRunDefinition]> = [
    ['the fixture agent', agentDefinition()],
    ['no models', agentDefinition({ models: [] })],
    ['an engine agent', agentDefinition({ engineProfile: { limits: { maxSteps: 30 } } })],
    ['a model listed twice', agentDefinition({ models: [...agentDefinition().models, ...agentDefinition().models] })],
    ['an empty role map', agentDefinition({ roleModels: {} })],
  ];
  for (const [name, definition] of fixtures) {
    it(`QA37-U2: without role models the layer is byte-identical to before (${name})`, () => {
      expect(canonicalJson(definitionLayer(definition))).to.equal(canonicalJson(before(definition)));
    });
  }

  it('QA37-U2: no definition is no layer', () => {
    expect(definitionLayer(null)).to.equal(undefined);
  });
});

describe('MA1 `roleModels` in the run definition (QA37-U10, what Node accepts from the authority)', () => {
  const parse = (roleModels: unknown) => agentRunDefinitionSchema.safeParse({ ...agentDefinition(), roleModels });

  it('QA37-U10: a valid map is kept as the authority sent it; an older authority’s definition has none', () => {
    const parsed = parse({ researcher: HAIKU, default: MINI });
    expect(parsed.success && parsed.data.roleModels).to.deep.equal({ researcher: HAIKU, default: MINI });
    const older = agentRunDefinitionSchema.parse(agentDefinition());
    expect('roleModels' in older).to.equal(false);
  });

  it('QA37-U10: a bad role key, an entry without its model, or more than 64 roles is refused', () => {
    expect(parse({ 'Researcher One': HAIKU }).success).to.equal(false);
    expect(parse({ researcher: { modelKey: 'model-3', provider: 'anthropic' } }).success).to.equal(false);
    const many = Object.fromEntries(Array.from({ length: 65 }, (_, i) => [`role-${i}`, HAIKU]));
    expect(parse(many).success).to.equal(false);
    expect(parse(Object.fromEntries(Object.entries(many).slice(0, 64))).success).to.equal(true);
  });
});
