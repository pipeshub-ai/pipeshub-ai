/**
 * Stand-ins for what an extension binds over the harness's org policy ports: an org's engine profile ceiling,
 * its narrowing of the vendor rules and its group access. They hold what a test sets and nothing else, so a test exercises the
 * harness's own enforcement of a bound value, not how a value is stored or checked when saved.
 */
import { DEFAULT_ORG_ACCESS_POLICY, type OrgAccessPolicy } from '../../../../src/modules/agent-harness/domain/model-access';
import type { AcpOptIn } from '../../../../src/modules/agent-harness/domain/engine-profiles';
import type { EngineProfileCeilings } from '../../../../src/modules/agent-harness/ports/engine-profiles';
import type { ConfiguredModels, ModelAccessPolicies } from '../../../../src/modules/agent-harness/ports/model-access';
import type { ActorGroupMembership, GroupAccess, GroupSubject } from '../../../../src/modules/agent-harness/ports/group-access';
import type { AccessGroupsPolicy } from '../../../../src/modules/control-plane';
import { HarnessUnavailableError } from '../../../../src/modules/agent-harness/domain/errors';

export class FakeEngineCeilings implements EngineProfileCeilings {
  /** An org sets this ceiling, so by default an ACP engine runs only where it names it. */
  constructor(readonly acpOptIn: AcpOptIn = 'org') {}

  readonly ceilings = new Map<string, unknown>();
  /** What each save named besides the value (who saved it, the revision it was edited from). */
  readonly saves: unknown[][] = [];

  read(orgId: string): Promise<unknown> {
    return Promise.resolve(this.ceilings.get(orgId) ?? null);
  }

  /** Sets (null: clears) the org's ceiling, as stored; admission parses it. What else the save names is kept in `saves`. */
  write<T>(orgId: string, ceiling: T | null, ...saved: unknown[]): Promise<T | null> {
    this.saves.push(saved);
    if (ceiling === null) this.ceilings.delete(orgId);
    else this.ceilings.set(orgId, ceiling);
    return Promise.resolve(ceiling);
  }
}

export class FakeModelAccessPolicies implements ModelAccessPolicies {
  readonly policies = new Map<string, OrgAccessPolicy>();
  readonly saves: unknown[][] = [];
  failing = false;

  read(orgId: string): Promise<OrgAccessPolicy> {
    if (this.failing) return Promise.reject(new Error('unreadable'));
    return Promise.resolve(this.policies.get(orgId) ?? { ...DEFAULT_ORG_ACCESS_POLICY, disabledRules: [] });
  }

  write(orgId: string, policy: OrgAccessPolicy, ...saved: unknown[]): Promise<OrgAccessPolicy> {
    this.saves.push(saved);
    const stored = { disabledRules: [...new Set(policy.disabledRules)].sort(), consumerPlans: policy.consumerPlans };
    this.policies.set(orgId, stored);
    return Promise.resolve(stored);
  }
}

/**
 * A group access an extension would bind (G-41), over a fixed `access` section and a membership: a subject the
 * section names is open to its groups' members only, anything else to everyone. Enough for the harness's own
 * checkpoints (routing, admission steps, spawns, pickers) to be exercised against a limit.
 */
export class FakeGroupAccess implements GroupAccess {
  constructor(
    private readonly options: {
      policies: { read(orgId: string): Promise<AccessGroupsPolicy | null> };
      membership: ActorGroupMembership;
      models?: Pick<ConfiguredModels, 'find' | 'list'>;
    },
  ) {}

  policy(orgId: string): Promise<AccessGroupsPolicy | null> {
    return this.options.policies.read(orgId).catch(() => {
      throw new HarnessUnavailableError("The organization's group access could not be read");
    });
  }

  async admits(orgId: string, userId: string, subject: GroupSubject, policy?: AccessGroupsPolicy | null): Promise<boolean> {
    const section = policy === undefined ? await this.policy(orgId) : policy;
    if (section === null) return true;
    let groups: readonly string[] | undefined;
    if (subject.kind === 'harness') groups = section.harness;
    else if (subject.kind === 'engine') groups = section.engines?.[subject.engine];
    else {
      const key = subject.modelKey ?? (await this.options.models?.find(orgId, undefined))?.modelKey;
      groups = key === undefined ? undefined : section.models?.[key];
    }
    if (groups === undefined) return true;
    return this.member(orgId, userId, groups);
  }

  async refusedModels(orgId: string, userId: string): Promise<Set<string>> {
    const refused = new Set<string>();
    for (const [key, groups] of Object.entries((await this.policy(orgId))?.models ?? {})) {
      if (!(await this.member(orgId, userId, groups))) refused.add(key);
    }
    return refused;
  }

  async allowedModelNames(orgId: string, userId: string, provider?: string): Promise<string[] | undefined> {
    const list = this.options.models?.list;
    if (list === undefined) return undefined;
    const refused = await this.refusedModels(orgId, userId);
    const names = (await list.call(this.options.models, orgId))
      .filter((entry) => !refused.has(entry.modelKey))
      .filter((entry) => provider === undefined || entry.provider.trim().toLowerCase() === provider)
      .flatMap((entry) => entry.modelNames);
    return [...new Set(names)].slice(0, 10);
  }

  private async member(orgId: string, userId: string, groups: readonly string[]): Promise<boolean> {
    try {
      return await this.options.membership.memberOfAny(orgId, userId, groups);
    } catch {
      throw new HarnessUnavailableError('Group membership could not be read');
    }
  }
}
