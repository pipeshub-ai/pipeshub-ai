import { ClientSession } from 'mongoose';
import { memberPrincipalKey } from '../../authz/loaders/project.loader';
import { fromCanonical, toCanonical } from '../../authz/domain/role-mapper';
import { NotFoundError, InternalServerError } from '../../../libs/errors/http.errors';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { OutboxEvent } from '../../../libs/services/outbox/outbox.schema';
import {
  executeConnectorCommand,
  handleBackendError,
} from '../../tokens_manager/utils/connector.utils';
import { AppConfig } from '../../tokens_manager/config/config';
import { Project } from '../schema/project.schema';
import { IProjectDocument } from '../types/project.interfaces';

const ENTITY_EVENTS_TOPIC = 'entity-events';
export const PROJECT_KB_SYNC_EVENT = 'projectKbSync';

/** Wire contract of the `projectKbSync` entity event; the Python consumer is `ProjectKbSyncPayload`. Ids are Mongo user ids and graph team keys. */
export interface ProjectKbSyncPayload {
  orgId: string;
  projectId: string;
  kbId: string;
  ownerUserId: string;
  editorUserIds: string[];
  viewerUserIds: string[];
  teams: { teamId: string; role: 'WRITER' | 'READER' }[];
  orgVisible: boolean;
}

/** The KB membership a project implies, with roles taken from the shared role table so Node and Python agree. */
export function desiredKbState(
  project: IProjectDocument,
  kbId: string,
  excludeUserId?: string,
): ProjectKbSyncPayload {
  const ownerUserId = project.userId.toString();
  const payload: ProjectKbSyncPayload = {
    orgId: project.orgId.toString(),
    projectId: project._id.toString(),
    kbId,
    ownerUserId,
    editorUserIds: [],
    viewerUserIds: [],
    teams: [],
    orgVisible: project.visibility === 'org',
  };
  for (const member of project.members) {
    const kbRole = fromCanonical('kb', toCanonical('project', member.role));
    const principalId = memberPrincipalKey(member);
    if (principalId === '') continue;
    if (kbRole !== 'WRITER' && kbRole !== 'READER') continue;
    if (member.principalType === 'team') {
      payload.teams.push({ teamId: principalId, role: kbRole });
    } else if (principalId !== ownerUserId && principalId !== excludeUserId) {
      (kbRole === 'WRITER'
        ? payload.editorUserIds
        : payload.viewerUserIds
      ).push(principalId);
    }
  }
  return payload;
}

/**
 * Owns the lifecycle and graph-permission sync of a project's hidden,
 * lazily-created linked Collection (`IProject.linkedKnowledgeBaseId`) —
 * the file store behind a project's Knowledge card, reusing the existing
 * Collections upload/index/search stack end to end. `ProjectService` stays
 * Mongo-only; this is the only place in the projects module that talks to
 * the Python `/api/v1/kb` HTTP surface.
 *
 * Permission changes are not sent to the graph from the request: they
 * are queued as a `projectKbSync` outbox event carrying the full desired
 * membership, and the Python reconcile makes the graph match it (adds,
 * removals and role changes alike), retrying until it succeeds.
 */
export class ProjectKnowledgeBaseService {
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
        `${appConfig.connectorBackend}/api/v1/kb/${encodeURIComponent(previousKbId)}`,
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
        `${appConfig.connectorBackend}/api/v1/kb/${encodeURIComponent(kbId)}`,
        HttpMethod.DELETE,
        headers,
      ).catch(() => undefined);
      const winner = await Project.findOne({ _id: projectId, isDeleted: false });
      if (!winner?.linkedKnowledgeBaseId) {
        throw new InternalServerError('Failed to link project knowledge base');
      }
      return winner.linkedKnowledgeBaseId;
    }

    // The reconcile makes the project owner the KB's owner and demotes the
    // lazy creator (possibly an editor) to the role their membership implies.
    await this.enqueueSync(won);
    return kbId;
  }

  /**
   * Queues a reconcile of the project's linked KB to the project's *current*
   * members, visibility and owner. No-op without a linked KB. The row is
   * written in `session` when the caller has a transaction, so it commits or
   * rolls back with the membership change. Call it after the Mongo change it
   * reflects; a removed member is revoked simply by no longer being in the
   * desired state. `excludeUserId` is for a caller that queues the sync
   * before pulling that user from `members`.
   */
  static async enqueueSync(
    project: IProjectDocument,
    session?: ClientSession,
    excludeUserId?: string,
  ): Promise<void> {
    const kbId = project.linkedKnowledgeBaseId;
    if (!kbId) return;
    const payload = desiredKbState(project, kbId, excludeUserId);
    await OutboxEvent.create(
      [
        {
          topic: ENTITY_EVENTS_TOPIC,
          key: PROJECT_KB_SYNC_EVENT,
          orderingKey: `project:${payload.projectId}`,
          value: JSON.stringify({
            eventType: PROJECT_KB_SYNC_EVENT,
            timestamp: Date.now(),
            payload,
          }),
          headers: { eventType: PROJECT_KB_SYNC_EVENT },
          status: 'pending' as const,
          attempts: 0,
          nextAttemptAt: new Date(),
        },
      ],
      session ? { session } : {},
    );
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
      `${appConfig.connectorBackend}/api/v1/kb/${encodeURIComponent(kbId)}`,
      HttpMethod.DELETE,
      headers,
    );
    if (response.statusCode !== 200 && response.statusCode !== 404) {
      throw handleBackendError(response, 'delete project knowledge base');
    }
  }
}

export default ProjectKnowledgeBaseService;
