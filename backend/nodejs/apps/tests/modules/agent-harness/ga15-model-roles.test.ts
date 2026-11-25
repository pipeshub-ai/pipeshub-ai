/**
 * GA-15 (G-27, WP-73): a run's model roles. The org's `models.roles` maps
 * the reserved keys fast, subagent and compaction to routes; the run's grant
 * hands the model gateway those routes (`roleRoutes`), each served under its
 * role's neutral id, and every charge names its role. The fold is the table
 * Python runs too (`contracts/harness/vectors/model-roles.json`).
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { pipeshub } from '../../../src/modules/agent-harness/domain/child-runs';
import { grantedRoutes } from '../../../src/modules/agent-harness/domain/gateway-token';
import {
  ENGINE_ROLES,
  engineRoleRoutes,
  ROLE_MODEL_IDS,
} from '../../../src/modules/agent-harness/domain/model-roles';
import { pinRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import type { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { ENGINE_MODEL_ROLES } from '../../../src/modules/control-plane/domain/llm';
import { goldenSpecSubject } from './support/golden-run-spec';

const VECTOR = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/model-roles.json'), 'utf8'),
) as {
  neutralIds: Record<string, string>;
  cases: Array<{
    id: string;
    behaviour: string;
    roles: unknown;
    primaryAccount: boolean;
    expect: Record<string, string>;
  }>;
};

describe('GA-15 MROLE-1: which route each engine role calls (model-roles.json)', () => {
  for (const c of VECTOR.cases) {
    it(`MROLE-1 ${c.id}: ${c.behaviour}`, () => {
      const folded = engineRoleRoutes(c.roles, c.primaryAccount);
      const got = Object.fromEntries(
        ENGINE_ROLES.map((role) => {
          const route = folded[role];
          return [role, route === undefined ? 'primary' : `${route.provider}:${route.model}`];
        }),
      );
      expect(got).to.deep.equal(c.expect);
    });
  }

  it("MROLE-1: the neutral ids are the table's, and the control plane's engine roles are the harness's", () => {
    expect(ROLE_MODEL_IDS).to.deep.equal(VECTOR.neutralIds);
    expect([...ENGINE_MODEL_ROLES]).to.deep.equal([...ENGINE_ROLES]);
  });
});

const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);

/** A pinned spec whose control-plane pin holds `roles` as `models.roles`; null: pinned without the control plane. */
const specWith = (roles: Record<string, string> | null, accountId?: string): RunSpec => {
  const { spec } = pinRunSpec(goldenSpecSubject(), DURABLE);
  const extension = { ...pipeshub(spec)! } as Record<string, unknown>;
  if (roles !== null) {
    extension['resolved'] = { effectiveConfig: { dimensions: { 'models.roles': { value: roles, setBy: 'org' } } } };
  }
  const [first, ...rest] = spec.model.policy;
  const primary =
    accountId === undefined
      ? first!
      : { ...first!, access: { accountKind: 'user_api_key', accountId, pays: 'user' } };
  return {
    ...spec,
    model: { ...spec.model, policy: [primary, ...rest] as RunSpec['model']['policy'] },
    extensions: { ...spec.extensions, 'x.pipeshub': extension },
  } as RunSpec;
};

describe('GA-15 MROLE-2: the model grant carries the pin’s engine role routes', () => {
  const ROLES = { fast: 'anthropic:claude-haiku-4-5', researcher: 'openai:gpt-5.4-mini', compaction: 'openai:gpt-5.4-nano' };

  it('MROLE-2: the mapped engine roles, by provider and model; sub-agent roles are not engine roles', () => {
    expect(grantedRoutes(specWith(ROLES)).roleRoutes).to.deep.equal({
      fast: { provider: 'anthropic', model: 'claude-haiku-4-5' },
      compaction: { provider: 'openai', model: 'gpt-5.4-nano' },
    });
  });

  it('MROLE-2: none for a run pinned without the control plane, or with no engine role mapped', () => {
    expect(grantedRoutes(specWith(null))).not.to.have.property('roleRoutes');
    expect(grantedRoutes(specWith({ researcher: 'openai:gpt-5.4-mini' }))).not.to.have.property('roleRoutes');
  });

  it('MROLE-2: none for a run whose first route is pinned to a model account (A1)', () => {
    expect(grantedRoutes(specWith(ROLES, 'users/user-1/openai'))).not.to.have.property('roleRoutes');
  });
});
