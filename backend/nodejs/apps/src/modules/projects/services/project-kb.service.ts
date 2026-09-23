import { NotFoundError, InternalServerError } from '../../../libs/errors/http.errors';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  executeConnectorCommand,
  handleBackendError,
} from '../../tokens_manager/utils/connector.utils';
import { AppConfig } from '../../tokens_manager/config/config';
import { Project } from '../schema/project.schema';
import { IProjectDocument } from '../types/project.interfaces';

type KbRole = 'OWNER' | 'WRITER' | 'READER';

/** Maps a project member's role to the KB role granted on the linked hidden Collection — mirrors Collection sharing's own OWNER/WRITER/READER ladder. */
const KB_ROLE_BY_PROJECT_ROLE: Record<'editor' | 'viewer', KbRole> = {
  editor: 'WRITER',
  viewer: 'READER',
};

/** Deterministic id of the synthetic "every org member" team — see `ensure_all_team_with_users` (neo4j_provider.py ~6168). No lookup call needed; the id is a fixed `all_{orgId}` string. */
function allOrgTeamId(orgId: string): string {
  return `all_${orgId}`;
}

/**
 * Owns the lifecycle and graph-permission sync of a project's hidden,
 * lazily-created linked Collection (`IProject.linkedKnowledgeBaseId`) —
 * the file store behind a project's Knowledge card, reusing the existing
 * Collections upload/index/search stack end to end. `ProjectService` stays
 * Mongo-only; this is the only place in the projects module that talks to
 * the Python `/api/v1/kb` HTTP surface.
 *
 * Every method that mutates graph permissions propagates failures instead
 * of swallowing them (a silently-failed revoke leaves a stale READER/WRITER
 * edge that this codebase has been bitten by before — see
 * `project.controller.ts`'s removed `syncFilePermissions` helper). Callers
 * that want best-effort behavior should catch explicitly at the call site.
 */
export class ProjectKnowledgeBaseService {
  private static async grantUsers(
    appConfig: AppConfig,
    headers: Record<string, string>,
    kbId: string,
    userIds: string[],
    role: KbRole,
  ): Promise<void> {
    if (userIds.length === 0) return;
    const response = await executeConnectorCommand(
      `${appConfig.connectorBackend}/api/v1/kb/${kbId}/permissions`,
      HttpMethod.POST,
      headers,
      { userIds, teamIds: [], role },
    );
    if (response.statusCode !== 200 && response.statusCode !== 201) {
      throw handleBackendError(response, 'sync project knowledge base user permissions');
    }
  }

  private static async grantTeams(
    appConfig: AppConfig,
    headers: Record<string, string>,
    kbId: string,
    teamIds: string[],
  ): Promise<void> {
    if (teamIds.length === 0) return;
    const response = await executeConnectorCommand(
      `${appConfig.connectorBackend}/api/v1/kb/${kbId}/permissions`,
      HttpMethod.POST,
      headers,
      // No `role` — the KB permission model has no concept of a team role
      // (`update_kb_permission` rejects one outright); every team
      // PERMISSION edge grants the same read access via
      // `_get_kb_virtual_ids`, regardless of the project's editor/viewer
      // distinction for that team. A known platform-level limitation, not
      // something this service can fix.
      { userIds: [], teamIds },
    );
    if (response.statusCode !== 200 && response.statusCode !== 201) {
      throw handleBackendError(response, 'sync project knowledge base team permissions');
    }
  }

  /**
   * Lazily creates the project's hidden linked Collection on first use and
   * returns its id — a no-op returning the existing id on every call after
   * the first. Race-safe: concurrent callers (e.g. two members uploading a
   * first file at once) each create a candidate KB, but only one wins the
   * `findOneAndUpdate` guarded on the *previously read* value of
   * `linkedKnowledgeBaseId`; the loser deletes its now-orphaned KB and
   * defers to the winner's id. Self-heals a 404 (Mongo points at a KB that
   * was hard-deleted upstream) by treating it as "not linked" and
   * recreating.
   */
  static async ensureLinkedKb(
    appConfig: AppConfig,
    headers: Record<string, string>,
    orgId: string,
    projectId: string,
  ): Promise<string> {
    const project = await Project.findOne({ _id: projectId, isDeleted: false });
    // Tenant isolation defense-in-depth — callers normally reach this only
    // after `ProjectService.assertAccess`, but a caller-supplied `orgId`
    // must never be trusted to already match without a check here too.
    if (!project || project.orgId.toString() !== orgId) {
      throw new NotFoundError('Project not found');
    }

    const previousKbId = project.linkedKnowledgeBaseId ?? null;
    if (previousKbId) {
      const check = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${previousKbId}`,
        HttpMethod.GET,
        headers,
      );
      if (check.statusCode !== 404) {
        return previousKbId;
      }
      // Self-heal: Mongo points at a KB that no longer exists upstream —
      // fall through and recreate, racing on the same stale value below.
    }

    const createResponse = await executeConnectorCommand(
      `${appConfig.connectorBackend}/api/v1/kb/`,
      HttpMethod.POST,
      headers,
      { name: `project:${projectId}`, isHidden: true },
    );
    if (createResponse.statusCode !== 200 && createResponse.statusCode !== 201) {
      throw handleBackendError(createResponse, 'create project knowledge base');
    }
    const kbId = (createResponse.data as { id?: string } | undefined)?.id;
    if (!kbId) {
      throw new InternalServerError('Knowledge base creation did not return an id');
    }

    const won = await Project.findOneAndUpdate(
      { _id: projectId, linkedKnowledgeBaseId: previousKbId },
      { $set: { linkedKnowledgeBaseId: kbId } },
      { new: true },
    );

    if (!won) {
      // Lost the race — someone else linked a KB first. Delete the orphan
      // and defer to whatever the winner actually set.
      await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${kbId}`,
        HttpMethod.DELETE,
        headers,
      ).catch(() => undefined);
      const winner = await Project.findOne({ _id: projectId, isDeleted: false });
      if (!winner?.linkedKnowledgeBaseId) {
        throw new InternalServerError('Failed to link project knowledge base');
      }
      return winner.linkedKnowledgeBaseId;
    }

    // The KB creator (whoever's headers/identity made the call above) is
    // its implicit OWNER — grant the project's real owner and every
    // current member their role too, in the same call that just won the
    // race, so a lazily-created-by-an-editor KB still ends up owned by
    // the project owner as well (documented in the plan as acceptable).
    await this.syncMemberPermissions(appConfig, headers, won);
    return kbId;
  }

  /**
   * Grants (never revokes) the KB permissions implied by a project's
   * *current* member list — the project owner as KB `OWNER`, editor/viewer
   * users as `WRITER`/`READER`, every team member (regardless of role, see
   * `grantTeams`) as a role-less team PERMISSION, and the synthetic
   * `all_{orgId}` team as `READER` when `visibility === 'org'`. Idempotent:
   * the underlying graph write is a `MERGE`, so calling this repeatedly
   * with the same members is a safe no-op. Does not revoke a since-removed
   * member's stale edge — callers must pair a removal with
   * `revokePrincipalPermission`.
   */
  static async syncMemberPermissions(
    appConfig: AppConfig,
    headers: Record<string, string>,
    project: IProjectDocument,
  ): Promise<void> {
    const kbId = project.linkedKnowledgeBaseId;
    if (!kbId) return;

    await this.grantUsers(appConfig, headers, kbId, [project.userId.toString()], 'OWNER');

    const editorUserIds: string[] = [];
    const viewerUserIds: string[] = [];
    const teamIds: string[] = [];
    for (const member of project.members) {
      const principalId = member.principalId.toString();
      if (member.principalType === 'team') {
        teamIds.push(principalId);
        continue;
      }
      (member.role === 'editor' ? editorUserIds : viewerUserIds).push(principalId);
    }

    await this.grantUsers(appConfig, headers, kbId, editorUserIds, KB_ROLE_BY_PROJECT_ROLE.editor);
    await this.grantUsers(appConfig, headers, kbId, viewerUserIds, KB_ROLE_BY_PROJECT_ROLE.viewer);
    await this.grantTeams(appConfig, headers, kbId, teamIds);

    if (project.visibility === 'org') {
      await this.grantTeams(appConfig, headers, kbId, [allOrgTeamId(project.orgId.toString())]);
    }
  }

  /** Revokes one principal's KB permission — pair with every `ProjectService.removeMember` call so a removed member doesn't keep a stale READER/WRITER edge. No-op if the project has no linked KB yet, or if the principal never had a KB-level permission (upstream 404). */
  static async revokePrincipalPermission(
    appConfig: AppConfig,
    headers: Record<string, string>,
    project: IProjectDocument,
    principalId: string,
    principalType: 'user' | 'team',
  ): Promise<void> {
    const kbId = project.linkedKnowledgeBaseId;
    if (!kbId) return;
    const body =
      principalType === 'team'
        ? { userIds: [], teamIds: [principalId] }
        : { userIds: [principalId], teamIds: [] };
    const response = await executeConnectorCommand(
      `${appConfig.connectorBackend}/api/v1/kb/${kbId}/permissions`,
      HttpMethod.DELETE,
      headers,
      body,
    );
    if (response.statusCode !== 200 && response.statusCode !== 404) {
      throw handleBackendError(response, 'revoke project knowledge base permission');
    }
  }

  /** Revokes the `all_{orgId}` team's READER edge — call when a project's `visibility` changes away from `'org'`. No-op without a linked KB, or if the edge was never granted (upstream 404). */
  static async revokeOrgVisibility(
    appConfig: AppConfig,
    headers: Record<string, string>,
    project: IProjectDocument,
  ): Promise<void> {
    const kbId = project.linkedKnowledgeBaseId;
    if (!kbId) return;
    const response = await executeConnectorCommand(
      `${appConfig.connectorBackend}/api/v1/kb/${kbId}/permissions`,
      HttpMethod.DELETE,
      headers,
      { userIds: [], teamIds: [allOrgTeamId(project.orgId.toString())] },
    );
    if (response.statusCode !== 200 && response.statusCode !== 404) {
      throw handleBackendError(response, 'revoke project knowledge base org visibility');
    }
  }

  /** Deletes the project's linked KB (cascades its records/vectors upstream) — idempotent; a 404 (already gone) is treated as success so a retried `softDelete` never fails on this step. No-op without a linked KB. */
  static async deleteLinkedKb(
    appConfig: AppConfig,
    headers: Record<string, string>,
    project: IProjectDocument,
  ): Promise<void> {
    const kbId = project.linkedKnowledgeBaseId;
    if (!kbId) return;
    const response = await executeConnectorCommand(
      `${appConfig.connectorBackend}/api/v1/kb/${kbId}`,
      HttpMethod.DELETE,
      headers,
    );
    if (response.statusCode !== 200 && response.statusCode !== 404) {
      throw handleBackendError(response, 'delete project knowledge base');
    }
  }
}

export default ProjectKnowledgeBaseService;
