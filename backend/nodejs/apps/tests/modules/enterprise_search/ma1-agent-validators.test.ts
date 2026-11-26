/**
 * MA1 (QA37-U10, Node validator half): the agent create and update bodies
 * accept `roleModels` and hand it on as it was sent (Python checks each entry
 * is a configured LLM); a bad key, a bad entry or too many roles is refused.
 */
import { expect } from 'chai';
import { createAgentSchema, updateAgentSchema } from '../../../src/modules/enterprise_search/validators/es_validators';

const HAIKU = { modelKey: 'mk-b', modelName: 'claude-haiku-4-5', provider: 'anthropic' };
const ROLES = { researcher: HAIKU, default: { modelKey: 'mk-c', modelName: 'gpt-5.4-mini', provider: 'openAI' } };

const SCHEMAS = [
  ['create', (roleModels: unknown) => createAgentSchema.safeParse({ body: { name: 'Agent', ...(roleModels !== undefined ? { roleModels } : {}) } })],
  ['update', (roleModels: unknown) => updateAgentSchema.safeParse({ params: { agentKey: 'agent-1' }, body: { ...(roleModels !== undefined ? { roleModels } : {}) } })],
] as const;

describe('MA1 agent save: `roleModels` (QA37-U10)', () => {
  for (const [name, parse] of SCHEMAS) {
    it(`QA37-U10 ${name}: a valid map passes through untouched`, () => {
      const parsed = parse(ROLES);
      expect(parsed.success).to.equal(true);
      expect(parsed.success && (parsed.data.body as { roleModels?: unknown }).roleModels).to.deep.equal(ROLES);
    });

    it(`QA37-U10 ${name}: absent stays absent; an empty map (no roles) is kept`, () => {
      const absent = parse(undefined);
      expect(absent.success && 'roleModels' in absent.data.body).to.equal(false);
      const empty = parse({});
      expect(empty.success && (empty.data.body as { roleModels?: unknown }).roleModels).to.deep.equal({});
    });

    const refused: Array<[string, unknown]> = [
      ['an upper-case role key', { Researcher: HAIKU }],
      ['a role key with a space', { 'researcher one': HAIKU }],
      ['a role key that starts with a dash', { '-researcher': HAIKU }],
      ['a role key longer than 64', { ['r'.repeat(65)]: HAIKU }],
      ['an entry without its model key', { researcher: { modelName: 'claude-haiku-4-5', provider: 'anthropic' } }],
      ['an entry without its provider', { researcher: { modelKey: 'mk-b', modelName: 'claude-haiku-4-5' } }],
      ['an entry with an unknown member', { researcher: { ...HAIKU, apiKey: 'x' } }],
      ['a model named by a string', { researcher: 'anthropic:claude-haiku-4-5' }],
      ['more than 64 roles', Object.fromEntries(Array.from({ length: 65 }, (_, i) => [`role-${i}`, HAIKU]))],
      ['a list', [HAIKU]],
    ];
    for (const [what, roleModels] of refused) {
      it(`QA37-U10 ${name}: ${what} is refused`, () => {
        expect(parse(roleModels).success).to.equal(false);
      });
    }

    it(`QA37-U10 ${name}: exactly 64 roles are accepted`, () => {
      const roles = Object.fromEntries(Array.from({ length: 64 }, (_, i) => [`role-${i}`, HAIKU]));
      expect(parse(roles).success).to.equal(true);
    });
  }
});
