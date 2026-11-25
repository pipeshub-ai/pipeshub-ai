import { administers, teamOf } from '../domain/team-scope';
import type {
  Authorizer,
  AuthzActor,
  AuthzDecision,
  CpAction,
  ResourceRef,
} from '../ports/authorizer';
import type { AgentDirectory, MembershipPort } from '../ports/directory';
import type { DelegationSource, OrgRoles } from '../ports/org-roles';

/** Actions every member of the org may take (CP-SKEL AZ-1); the audit log is the admins'. */
const MEMBER_ACTIONS: ReadonlySet<CpAction> = new Set<CpAction>([
  'catalog.read',
  'installation.read',
  'grant.read',
  'binding.read',
  'approval.read',
  'explain',
]);

/** What a person may do to objects they own themselves (a user-scope installation, their bindings and grant). */
const OWNER_ACTIONS: ReadonlySet<CpAction> = new Set<CpAction>([
  'installation.create',
  'installation.update',
  'installation.delete',
  'grant.write',
  'binding.create',
  'binding.update',
]);

/** What an agent's editors may do to the agent's own grant, and read its spend (WP-56). */
const isAgentEditorAction = (action: CpAction): boolean =>
  action === 'grant.write' || action === 'usage.read';

const ALLOW: AuthzDecision = { allow: true };
const deny = (reason: string): AuthzDecision => ({ allow: false, reason });

/** No action is delegated to team admins: only org admins and owners act on team-owned objects. */
export const NO_DELEGATION: DelegationSource = {
  delegatedActions: () => Promise.resolve([]),
};

/**
 * The one admin check of the control plane (03-interfaces §5.1), composed of
 * small rules tried in order: org admin; member reads; owner; agent editor;
 * delegated team admin. Every rule reads live facts: the org role on each
 * check, team membership and agent edit rights through their ports (whose
 * caches are invalidated by `entity-events`), and the delegation source.
 * An unreadable membership denies the team rule only (fail closed).
 */
export class RoleAuthorizer implements Authorizer {
  constructor(
    private readonly roles: OrgRoles,
    private readonly membership: MembershipPort,
    private readonly agents: AgentDirectory,
    private readonly delegation: DelegationSource,
  ) {}

  async check(
    actor: AuthzActor,
    action: CpAction,
    resource: ResourceRef,
  ): Promise<AuthzDecision> {
    if (resource.orgId !== actor.orgId) return deny('wrong_org');
    const role = await this.roles.roleOf(actor.orgId, actor.userId);
    if (role === null) return deny('not_in_org');
    if (role === 'admin') return ALLOW;
    if (MEMBER_ACTIONS.has(action)) return ALLOW;
    const owner = resource.owner ?? 'org';
    if (owner === `user:${actor.userId}` && OWNER_ACTIONS.has(action))
      return ALLOW;
    if (owner.startsWith('agent:') && isAgentEditorAction(action)) {
      // An unreadable agent directory denies its rules only (fail closed), as membership does.
      const editor = await this.agents
        .canEdit(actor.orgId, owner.slice(6), actor)
        .catch(() => false);
      if (editor) return ALLOW;
    }
    let team: string | null;
    try {
      team = await this.owningTeam(actor.orgId, owner);
    } catch {
      return deny('directory_unavailable');
    }
    if (team === null) return deny('not_authorized');
    return this.teamAdmin(actor, action, team);
  }

  /** The team that owns the object: a team-owned object, or an agent its owner team holds. */
  private async owningTeam(
    orgId: string,
    owner: string,
  ): Promise<string | null> {
    if (owner.startsWith('agent:'))
      return this.agents.ownerTeam(orgId, owner.slice(6));
    return teamOf(owner);
  }

  private async teamAdmin(
    actor: AuthzActor,
    action: CpAction,
    team: string,
  ): Promise<AuthzDecision> {
    const delegated = await this.delegation.delegatedActions(actor.orgId);
    if (!delegated.includes(action)) return deny('not_delegated');
    let memberships;
    try {
      memberships = await this.membership.teamsOf(actor.orgId, actor.userId);
    } catch {
      return deny('membership_unavailable');
    }
    return administers(memberships, team) ? ALLOW : deny('not_team_admin');
  }
}
