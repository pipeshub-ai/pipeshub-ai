import { CanonicalRole, maxRole } from '../../../src/modules/authz/domain/ladder';
import { toCanonical } from '../../../src/modules/authz/domain/role-mapper';
import {
  decide,
  resolveRole,
} from '../../../src/modules/enterprise_search/services/collaboration/access/conversation-access.policy';
import {
  ConversationAccessFields,
  ConversationOperation,
} from '../../../src/modules/enterprise_search/services/collaboration/domain/types';
import {
  canReadChatArtifact,
  canReadChatAttachment,
} from '../../../src/modules/authz/domain/chat-content.rules';
import {
  chatRole,
  isUnresolved,
  projectRole,
} from '../../../src/modules/authz/domain/rules';
import {
  ChatFacts,
  CollaboratorFact,
  ProjectFacts,
  ProjectMemberFact,
  Subject,
  TeamIds,
} from '../../../src/modules/authz/domain/types';

export const CURRENT_PHASE = 'PH-03';

export interface MatrixRow {
  id: string;
  decider: string;
  since: string;
  langs?: Array<'ts' | 'py' | 'fe'>;
  given: {
    principal: string;
    resource: string;
    grants: Array<Record<string, unknown>>;
  };
  action: string;
  expect: { allow: boolean; role?: string; code?: string };
}

export type MatrixDecision = MatrixRow['expect'];
export type MatrixDecider = (row: MatrixRow) => MatrixDecision;

type Grant = Record<string, unknown>;

const ORG = 'org1';
const OTHER_ORG = 'org2';
const OWNER = 'owner1';

const ofKind = (grants: Grant[], kind: string): Grant[] => grants.filter((g) => g.kind === kind);

const verdict = (role: CanonicalRole): MatrixDecision => ({ allow: role !== 'none', role });

function subjectOf(row: MatrixRow, teamIds: TeamIds): Subject {
  return { userId: row.given.principal, orgId: ORG, teamIds };
}

function teamsOf(grants: Grant[]): TeamIds {
  const g = ofKind(grants, 'subject')[0] ?? ofKind(grants, 'teams')[0];
  if (!g) {
    return [];
  }
  const teams = g.teams ?? g.ids;
  return teams === 'unresolved' ? 'unresolved' : (teams as string[]);
}

function projectFacts(grants: Grant[], subjectId: string): ProjectFacts {
  const members: ProjectMemberFact[] = ofKind(grants, 'member').map((g) => ({
    principalType: g.principalType as 'user' | 'team',
    principalId: g.id as string,
    role: g.role as string,
  }));
  return {
    orgId: ofKind(grants, 'otherOrg').length ? OTHER_ORG : ORG,
    ownerId: ofKind(grants, 'owner').length ? subjectId : OWNER,
    visibility: ofKind(grants, 'visibility')[0]?.value === 'org' ? 'org' : 'private',
    members,
  };
}

const roleMapper: MatrixDecider = (row) => {
  const roles = row.given.grants.map((g) => toCanonical(row.given.resource, g.stored as string));
  return verdict(maxRole(...roles));
};

const projectRoleDecider: MatrixDecider = (row) => {
  const grants = row.given.grants as Grant[];
  return verdict(projectRole(projectFacts(grants, row.given.principal), subjectOf(row, teamsOf(grants))));
};

function chatSetup(row: MatrixRow): { facts: ChatFacts; subject: Subject; collab: boolean } {
  const grants = row.given.grants as Grant[];
  const u = row.given.principal;
  const sharedWith: CollaboratorFact[] = [
    ...ofKind(grants, 'direct').map((g) => ({
      userId: (g.userId as string | undefined) ?? u,
      accessLevel: g.level as string,
    })),
    ...ofKind(grants, 'team').map((g) => ({ teamId: g.teamId as string, accessLevel: g.level as string })),
  ];
  const projectGrant = ofKind(grants, 'project')[0];
  const cleared = ofKind(grants, 'clearedProject').length > 0;
  let project: ProjectFacts | null = null;
  if (projectGrant && !cleared) {
    const role = projectGrant.projectRole as string;
    project = {
      orgId: ORG,
      ownerId: role === 'owner' ? u : OWNER,
      visibility: 'private',
      members: role === 'owner' ? [] : [{ principalType: 'user', principalId: u, role }],
      projectChatAccess: (projectGrant.ceiling as string | undefined) ?? null,
    };
  }
  const facts: ChatFacts = {
    orgId: ofKind(grants, 'otherOrg').length ? OTHER_ORG : ORG,
    ownerId: ofKind(grants, 'owner').length ? u : OWNER,
    sharedWith,
    projectId: project ? 'p1' : null,
    projectVisibility: projectGrant && projectGrant.visibility === 'project' ? 'project' : 'private',
    project,
  };
  return {
    facts,
    subject: subjectOf(row, teamsOf(grants)),
    collab: ofKind(grants, 'collab')[0]?.value !== false,
  };
}

const chatRoleDecider: MatrixDecider = (row) => {
  const { facts, subject, collab } = chatSetup(row);
  const result = chatRole(facts, subject, { collab, needed: row.action as CanonicalRole });
  if (isUnresolved(result)) {
    return { allow: false, code: 'TEAM_RESOLUTION_UNAVAILABLE' };
  }
  return verdict(result.role);
};

/** The 51 section 2 operation table; `action` is the operation. */
const chatOp: MatrixDecider = (row) => {
  const grants = row.given.grants as Grant[];
  const { facts, subject, collab } = chatSetup(row);
  const session: ConversationAccessFields = {
    orgId: facts.orgId,
    userId: facts.ownerId,
    sharedWith: facts.sharedWith.map((r) =>
      r.userId !== undefined
        ? { userId: r.userId, accessLevel: r.accessLevel }
        : { teamId: r.teamId, accessLevel: r.accessLevel },
    ),
    projectId: facts.projectId,
    projectVisibility: facts.projectVisibility,
    settings: { editorsCanInvite: ofKind(grants, 'settings')[0]?.editorsCanInvite === true },
  };
  const op = row.action as ConversationOperation;
  const resolved = resolveRole(session, subject, { collab, op, project: facts.project });
  const d = decide(op, 'unresolved' in resolved ? 'unresolved' : resolved.role, {
    editorsCanInvite: session.settings?.editorsCanInvite,
    ownerActive: ofKind(grants, 'ownerInactive').length ? false : undefined,
    isAuthorOfAnsweredQuestion: ofKind(grants, 'asker').length > 0,
    isRequester: ofKind(grants, 'requester').length > 0,
  });
  return d.allowed ? { allow: true, role: d.role } : { allow: false, code: d.code };
};

// The uploader/creator reads through their OWNER edge and is never sent to the PDP;
// everyone else gets the production rule with the flag on.
const h4: MatrixDecider = (row) => {
  const g = row.given.grants[0] as { isUploader: boolean; chatRole: CanonicalRole; consent: boolean };
  const owner = 'uploader';
  return {
    allow:
      g.isUploader ||
      canReadChatAttachment(
        g.chatRole,
        { authorUserId: owner, sessionOwnerId: owner, filesShared: g.consent },
        true,
      ),
  };
};

const h5: MatrixDecider = (row) => {
  const g = row.given.grants[0] as {
    isCreator: boolean;
    chatRole: CanonicalRole;
    consent: boolean;
    kind: string;
  };
  const kind = g.kind.toUpperCase();
  return {
    allow:
      g.isCreator ||
      canReadChatArtifact(
        g.chatRole,
        { shareToolResults: g.consent },
        { visibility: kind === 'STAGING' ? 'STAGING' : 'VISIBLE', isTemporary: kind === 'TEMPORARY' },
        true,
      ),
  };
};

export const deciders: Record<string, MatrixDecider> = {
  roleMapper,
  projectRole: projectRoleDecider,
  chatRole: chatRoleDecider,
  chatOp,
  h4,
  h5,
};



export function phaseNumber(phase: string): number {
  const match = /^PH-(\d+)$/.exec(phase);
  if (!match) {
    throw new Error(`Invalid phase "${phase}", expected PH-NN`);
  }
  return Number(match[1]);
}

export function isPhaseActive(since: string, current: string = CURRENT_PHASE): boolean {
  return phaseNumber(since) <= phaseNumber(current);
}
