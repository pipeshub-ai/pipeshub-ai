/**
 * R19-FU-3 found (UI-ENV-1 on the lane): the agent create and update bodies dropped `environment` (C20.18) and
 * `gates` (G-03..G-05), so a builder save stored neither. Both now pass through as sent (the agents module parses
 * them); `{}` or null clears a stored one; anything else that is not an object is refused here.
 */
import { expect } from 'chai';
import { createAgentSchema, updateAgentSchema } from '../../../src/modules/enterprise_search/validators/es_validators';

const ENVIRONMENT = { environmentId: 'agent', level: 'agent', setup: 'pip install -r requirements.txt', env: { LANG: 'C.UTF-8' } };
const GATES = { planApproval: true };

const SCHEMAS = [
  ['create', (body: Record<string, unknown>) => createAgentSchema.safeParse({ body: { name: 'Agent', ...body } })],
  ['update', (body: Record<string, unknown>) => updateAgentSchema.safeParse({ params: { agentKey: 'agent-1' }, body })],
] as const;

describe('WP-100 agent save: `environment` and `gates` reach the agents module (R19-FU-3)', () => {
  for (const [name, parse] of SCHEMAS) {
    it(`AHF-1 ${name}: both pass through as sent`, () => {
      const parsed = parse({ environment: ENVIRONMENT, gates: GATES });
      expect(parsed.success).to.equal(true);
      const body = parsed.success ? (parsed.data.body as Record<string, unknown>) : {};
      expect(body.environment).to.deep.equal(ENVIRONMENT);
      expect(body.gates).to.deep.equal(GATES);
    });

    it(`AHF-2 ${name}: {} and null (clear a stored one) are kept; absent stays absent`, () => {
      const cleared = parse({ environment: {}, gates: {} });
      expect(cleared.success && (cleared.data.body as Record<string, unknown>).environment).to.deep.equal({});
      expect(cleared.success && (cleared.data.body as Record<string, unknown>).gates).to.deep.equal({});
      const nulled = parse({ environment: null, gates: null });
      expect(nulled.success && (nulled.data.body as Record<string, unknown>).environment).to.equal(null);
      expect(nulled.success && (nulled.data.body as Record<string, unknown>).gates).to.equal(null);
      const absent = parse({});
      expect(absent.success && 'environment' in absent.data.body).to.equal(false);
      expect(absent.success && 'gates' in absent.data.body).to.equal(false);
    });

    for (const bad of ['pip install', ['a'], 42, true]) {
      it(`AHF-3 ${name}: ${JSON.stringify(bad)} is refused`, () => {
        expect(parse({ environment: bad }).success).to.equal(false);
        expect(parse({ gates: bad }).success).to.equal(false);
      });
    }
  }
});
