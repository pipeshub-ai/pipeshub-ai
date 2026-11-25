/**
 * GA-23 (G-43, C20.44; OWD-17 as amended 2026-10-26) on Node: vendor subscription logins are refused, but
 * Claude Code's own, which is admin-gated and off by default. Model access never routes any other engine to a
 * person's plan, and the signed rules refuse every ChatGPT/Codex, Gemini or other plan login even for an org that
 * opted into everything. OWD-8: no row sends a key straight to a vendor.
 */
import { expect } from 'chai';
import type { ModelAccessService } from '../../../src/modules/agent-harness/application/model-access-service';
import { RELEASE_ENGINE_PROFILES } from '../../../src/modules/agent-harness/domain/engine-profiles';
import type { PinnedEngineProfile } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { CLAUDE_PLAN_ROW } from '../../../src/modules/agent-harness/domain/engine-signin';
import { HarnessModelAccessRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import {
  type AccessCandidate,
  DEFAULT_ORG_ACCESS_POLICY,
  decideAccess,
  GATEWAY_BYPASS_ROUTES,
  ModelAccessRefusal,
  OFFERINGS,
  releaseVendorRules,
} from '../../../src/modules/agent-harness/domain/model-access';
import { testModelAccess } from './support/model-access';

const ENGINES = ['claude_code', 'codex', 'opencode', 'gemini_cli', 'loop', 'acp-opencode', 'acp-gemini'];
const PROVIDERS = ['anthropic', 'openai', 'gemini', 'azureopenai', 'openaicompatible', 'bedrock', 'vertexai', 'foundry'];
const LOGIN_KINDS = ['user_plan_sandbox_login', 'user_plan_injected'] as const;
const LOGIN_ROUTES = ['vendor_sandbox_login', 'vendor_injected'] as const;
const EVERYTHING_ALLOWED = { disabledRules: [], consumerPlans: true };
const CLAUDE_OWN: AccessCandidate = {
  provider: 'anthropic',
  accountKind: 'user_plan_sandbox_login',
  route: 'vendor_sandbox_login',
  engine: 'claude_code',
};

const decided = (candidate: AccessCandidate, offering: (typeof OFFERINGS)[number], policy = EVERYTHING_ALLOWED): string => {
  try {
    return `allowed:${decideAccess(releaseVendorRules(), candidate, offering, policy).id}`;
  } catch (error) {
    if (!(error instanceof ModelAccessRefusal)) throw error;
    return error.code;
  }
};

describe('GA-23: vendor subscription logins are refused, but Claude Code\'s own', () => {
  for (const offering of OFFERINGS) {
    it(`GA-23-1 (${offering}): every plan login but Claude Code's own is refused, fully opted in`, () => {
      const allowed: string[] = [];
      for (const provider of PROVIDERS) {
        for (const accountKind of LOGIN_KINDS) {
          for (const route of LOGIN_ROUTES) {
            for (const engine of ENGINES) {
              const candidate = { provider, accountKind, route, engine } as AccessCandidate;
              if (decided(candidate, offering).startsWith('allowed:')) allowed.push(JSON.stringify(candidate));
            }
          }
        }
      }
      expect(allowed.every((c) => c === JSON.stringify(CLAUDE_OWN)), allowed.join('\n')).to.equal(true);
    });
  }

  it('GA-23-2: Claude Code\'s own login is off until the org opts in', () => {
    expect(decided(CLAUDE_OWN, 'oss', { ...DEFAULT_ORG_ACCESS_POLICY, disabledRules: [] })).to.equal(
      'org_policy_refused',
    );
    expect(decided(CLAUDE_OWN, 'oss')).to.equal(`allowed:${CLAUDE_PLAN_ROW}`);
    expect(decided(CLAUDE_OWN, 'oss', { disabledRules: [CLAUDE_PLAN_ROW] as never[], consumerPlans: true })).to.equal(
      'org_policy_refused',
    );
  });

  it('GA-23-3 (OWD-8): no row sends a key straight to a vendor', () => {
    const bypassing = releaseVendorRules().rules.filter((rule) => GATEWAY_BYPASS_ROUTES.has(rule.route));
    expect(bypassing.length).to.be.greaterThan(0);
    expect(bypassing.map((rule) => [rule.id, rule.status])).to.deep.equal(bypassing.map((rule) => [rule.id, 'prohibited']));
  });
});

describe('GA-23: model access never routes another engine to a person\'s plan', () => {
  let access: ModelAccessService;
  let asked: number;

  const subject = (profile: PinnedEngineProfile) => ({
    orgId: 'org-1',
    actorId: 'user-1',
    definition: null,
    model: { chatMode: 'auto' as const },
    engineProfile: profile,
    signIn: () => {
      asked += 1;
      return Promise.resolve({ via: 'session', surface: 'web', shared: false });
    },
  });

  beforeEach(() => {
    asked = 0;
    // Sign-in switched on and allowed for the org: the most permissive setting an admin can choose.
    access = testModelAccess({ signIns: () => ({ usable: () => Promise.resolve({ enabled: true, allowed: true }) }) });
  });

  it('GA-23-4: with sign-in on, only Claude Code reaches vendor_sandbox_login; no other engine reads the plan', async () => {
    const routes: Record<string, string> = {};
    for (const profile of RELEASE_ENGINE_PROFILES) {
      try {
        routes[profile.engine.kind] = (await access.decide(subject(profile)))?.access.access.route ?? 'none';
      } catch (error) {
        routes[profile.engine.kind] =
          error instanceof HarnessModelAccessRefusedError ? error.code : (error as Error).name;
      }
    }
    const kinds = Object.keys(routes);
    expect(kinds).to.include('claude_code');
    expect(kinds.length).to.be.greaterThan(1);
    for (const [kind, route] of Object.entries(routes)) {
      expect(route === 'vendor_sandbox_login', `${kind}: ${route}`).to.equal(kind === 'claude_code');
    }
    expect(asked).to.equal(1);
  });
});
