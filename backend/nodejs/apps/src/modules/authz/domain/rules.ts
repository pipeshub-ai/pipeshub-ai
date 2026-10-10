import { atLeast, CanonicalRole, maxRole, minRole } from './ladder';
import { toCanonical } from './role-mapper';
import {
  AccessPath,
  ChatFacts,
  ChatRoleOptions,
  ChatRoleResult,
  CollaboratorFact,
  ProjectFacts,
  Subject,
} from './types';

const NON_SHAREABLE_ARTIFACT_KINDS: ReadonlySet<string> = new Set([
  'staging',
  'temporary',
]);

export function isUnresolved(
  result: ChatRoleResult,
): result is { unresolved: true } {
  return 'unresolved' in result;
}

export function projectRole(
  project: ProjectFacts,
  subject: Subject,
): CanonicalRole {
  if (project.orgId !== subject.orgId) {
    return 'none';
  }
  if (project.ownerId === subject.userId) {
    return 'owner';
  }
  const teamIds = subject.teamIds === 'unresolved' ? [] : subject.teamIds;
  const granted = project.members
    .filter((m) =>
      m.principalType === 'user'
        ? m.principalId === subject.userId
        : teamIds.includes(m.principalId),
    )
    .map((m) => toCanonical('project', m.role));
  const best = maxRole(...granted);
  if (best !== 'none') {
    return best;
  }
  return project.visibility === 'org' ? 'viewer' : 'none';
}

/** H2. */
export function inheritedFromProject(
  chat: ChatFacts,
  subject: Subject,
  options: Pick<ChatRoleOptions, 'collab'>,
): CanonicalRole {
  if (
    chat.projectVisibility !== 'project' ||
    chat.projectId === null ||
    !chat.project
  ) {
    return 'none';
  }
  const ceiling: CanonicalRole =
    options.collab && chat.project.projectChatAccess === 'editor'
      ? 'editor'
      : 'viewer';
  return minRole(projectRole(chat.project, subject), ceiling);
}

function isTeamRow(
  row: CollaboratorFact,
): row is CollaboratorFact & { teamId: string } {
  return row.userId === undefined && row.teamId !== undefined;
}

function rowRole(row: CollaboratorFact, collab: boolean): CanonicalRole {
  const role = toCanonical('chat', row.accessLevel);
  return collab ? role : minRole(role, 'viewer');
}

function nonTeamPaths(
  chat: ChatFacts,
  subject: Subject,
  collab: boolean,
): AccessPath[] {
  if (chat.orgId !== subject.orgId) {
    return [];
  }
  const paths: AccessPath[] = [];
  if (chat.ownerId === subject.userId) {
    paths.push({ type: 'owner', ref: chat.ownerId, role: 'owner' });
  }
  for (const row of chat.sharedWith) {
    if (row.userId === subject.userId) {
      paths.push({
        type: 'direct',
        ref: subject.userId,
        role: rowRole(row, collab),
      });
    }
  }
  const inherited = inheritedFromProject(chat, subject, { collab });
  if (inherited !== 'none' && chat.projectId !== null) {
    paths.push({ type: 'project', ref: chat.projectId, role: inherited });
  }
  return paths.filter((p) => p.role !== 'none');
}

function candidateTeamRows(
  chat: ChatFacts,
  collab: boolean,
): Array<CollaboratorFact & { teamId: string }> {
  return chat.sharedWith
    .filter(isTeamRow)
    .filter((r) => rowRole(r, collab) !== 'none');
}

function teamPaths(
  chat: ChatFacts,
  teamIds: readonly string[],
  collab: boolean,
): AccessPath[] {
  return candidateTeamRows(chat, collab)
    .filter((r) => teamIds.includes(r.teamId))
    .map((r) => ({
      type: 'team' as const,
      ref: r.teamId,
      role: rowRole(r, collab),
    }));
}

function best(paths: readonly AccessPath[]): CanonicalRole {
  return maxRole(...paths.map((p) => p.role));
}

/** A project team member row that could lift the chat to `needed` once the caller's teams are known. */
function projectTeamCouldGrant(
  chat: ChatFacts,
  needed: CanonicalRole,
  collab: boolean,
): boolean {
  const project = chat.project;
  if (
    chat.projectVisibility !== 'project' ||
    chat.projectId === null ||
    !project
  ) {
    return false;
  }
  const ceiling: CanonicalRole =
    collab && project.projectChatAccess === 'editor' ? 'editor' : 'viewer';
  return project.members.some(
    (m) =>
      m.principalType === 'team' &&
      atLeast(minRole(toCanonical('project', m.role), ceiling), needed),
  );
}

/** H1: max over owner, direct user row, team rows the subject belongs to, and H2. */
export function chatRole(
  chat: ChatFacts,
  subject: Subject,
  options: ChatRoleOptions,
): ChatRoleResult {
  const needed = options.needed ?? 'viewer';
  const base = nonTeamPaths(chat, subject, options.collab);
  if (chat.orgId !== subject.orgId || atLeast(best(base), needed)) {
    return { role: best(base), via: base };
  }
  if (subject.teamIds === 'unresolved') {
    const couldSatisfy =
      (options.collab &&
        candidateTeamRows(chat, true).some((r) =>
          atLeast(rowRole(r, true), needed),
        )) ||
      projectTeamCouldGrant(chat, needed, options.collab);
    return couldSatisfy
      ? { unresolved: true }
      : { role: best(base), via: base };
  }
  if (!options.collab) {
    return { role: best(base), via: base };
  }
  const via = [...base, ...teamPaths(chat, subject.teamIds, true)];
  return { role: best(via), via };
}

/** Every path granting access, without short-circuiting; the basis of explain. */
export function collectChatPaths(
  chat: ChatFacts,
  subject: Subject,
  collab: boolean,
): { via: readonly AccessPath[]; teamsUnresolved: boolean } {
  const base = nonTeamPaths(chat, subject, collab);
  if (!collab || chat.orgId !== subject.orgId) {
    return { via: base, teamsUnresolved: false };
  }
  if (subject.teamIds === 'unresolved') {
    return {
      via: base,
      teamsUnresolved: candidateTeamRows(chat, true).length > 0,
    };
  }
  return {
    via: [...base, ...teamPaths(chat, subject.teamIds, true)],
    teamsUnresolved: false,
  };
}

/** H3: there is no per-message ACL. */
export function messageRole(role: CanonicalRole): CanonicalRole {
  return role;
}

/** H4. */
export function canReadAttachment(input: {
  isUploader: boolean;
  chatRole: CanonicalRole;
  consent: boolean;
}): boolean {
  return (
    input.isUploader || (atLeast(input.chatRole, 'viewer') && input.consent)
  );
}

/** H5. */
export function canReadArtifact(input: {
  isCreator: boolean;
  chatRole: CanonicalRole;
  consent: boolean;
  kind: string;
}): boolean {
  if (input.isCreator) {
    return true;
  }
  if (NON_SHAREABLE_ARTIFACT_KINDS.has(input.kind.toLowerCase())) {
    return false;
  }
  return atLeast(input.chatRole, 'viewer') && input.consent;
}
