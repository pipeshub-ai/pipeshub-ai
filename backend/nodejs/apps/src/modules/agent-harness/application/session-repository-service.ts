import {
  actorRefOfUser,
  ControlPlaneTransaction,
  userScope,
} from '../../control-plane';
import { canonicalDigest } from '../contracts/canonical-json';
import {
  HarnessConflictError,
  HarnessUnavailableError,
} from '../domain/errors';
import {
  githubRepository,
  REPOSITORY_HEAD_PATH,
  sameRepository,
  sessionBranch,
  type SessionPullRequest,
  type SessionRepository,
  type SessionRepositoryView,
} from '../domain/repository';
import type { CodingEnginePreferences } from '../ports/coding-engines';
import type { RepositoryToolsets } from '../ports/repository-toolsets';
import type { SessionExecutionScope } from '../ports/chat-session-repository';
import type { CodingEngineService } from './coding-engine-service';
import { harnessLogMeta, type HarnessLogger } from './harness-log';
import type { WorkspaceService } from './workspace-service';

export interface SessionRepositoryServiceOptions {
  /** The same per-chat store as the chat's coding engine. */
  preferences: Pick<
    CodingEnginePreferences,
    'readSessionRepository' | 'writeSessionRepository'
  >;
  /** Every saved choice is one audited control-plane write (CPD-1). */
  transaction: ControlPlaneTransaction;
  toolsets: RepositoryToolsets;
  workspaces: Pick<WorkspaceService, 'sessionFiles'>;
  engines: Pick<CodingEngineService, 'forTurn'>;
  /** The pull request the chat's engine opened, from its runs' journals; absent: the view names none. */
  pullRequests?: (
    orgId: string,
    sessionId: string,
  ) => Promise<SessionPullRequest | null>;
  logger?: HarnessLogger;
}

/** Who asks and what their chat's engine is, as the route or admission read it with their credential. */
export interface RepositoryCaller {
  orgId: string;
  actorId: string;
  authorization: string;
  /** An agent chat's engines (its profile's list); undefined: the default chat, whose engine is the person's. */
  agentEngines?: readonly string[];
}

const refused = (
  code:
    | 'REPOSITORY_NEEDS_ENGINE'
    | 'REPOSITORY_TOOLSET_MISSING'
    | 'REPOSITORY_NOT_GITHUB'
    | 'REPOSITORY_LOCKED',
  message: string,
): HarnessConflictError => new HarnessConflictError(code, message);

/**
 * The GitHub repository a chat's coding engine works on (GIT-FLOW, WP-26 C3.79's entry point): chosen by the chat's
 * owner beside its coding engine, saved by the same per-chat store, and pinned by admission on the runs that hand
 * work to an engine. It can change until the chat's workspace holds the clone; then another repository needs a
 * new chat.
 */
export class SessionRepositoryService {
  constructor(private readonly options: SessionRepositoryServiceOptions) {}

  /** The chat's repository; null when it has none. */
  async view(
    scope: SessionExecutionScope,
  ): Promise<SessionRepositoryView | null> {
    const stored = await this.stored(scope.orgId, scope.sessionId);
    if (stored === null) return null;
    const cloned = await this.cloned(scope);
    const view = this.viewOf(scope, stored, cloned);
    // Only an engine that holds the clone can have opened one.
    const pullRequest = cloned ? await this.pullRequest(scope) : null;
    return pullRequest === null ? view : { ...view, pullRequest };
  }

  /** A journal that cannot be read leaves the pull request out; the repository is still the chat's. */
  private async pullRequest(
    scope: SessionExecutionScope,
  ): Promise<SessionPullRequest | null> {
    if (this.options.pullRequests === undefined) return null;
    try {
      return await this.options.pullRequests(scope.orgId, scope.sessionId);
    } catch (error) {
      this.options.logger?.warn(
        'session.pull_request_unread',
        harnessLogMeta('session.pull_request_unread', {
          orgId: scope.orgId,
          sessionId: scope.sessionId,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      return null;
    }
  }

  /** The owner's choice for this chat (`PUT …/repository`), checked, then saved as one audited write. */
  async set(
    scope: SessionExecutionScope,
    caller: RepositoryCaller,
    repository: SessionRepository,
  ): Promise<SessionRepositoryView> {
    const [stored, cloned] = await Promise.all([
      this.stored(scope.orgId, scope.sessionId),
      this.cloned(scope),
    ]);
    if (cloned && stored !== null) {
      if (sameRepository(stored, repository)) {
        return this.viewOf(scope, stored, cloned);
      }
      throw this.locked(stored);
    }
    await this.assertEngine(caller, scope.sessionId);
    await this.assertUsable(caller, repository);
    await this.persist(
      caller.orgId,
      caller.actorId,
      scope.sessionId,
      stored,
      repository,
    );
    return this.viewOf(scope, repository, cloned);
  }

  /** `DELETE …/repository`: the chat works on none from its next message, unless its workspace holds the clone. */
  async clear(scope: SessionExecutionScope): Promise<void> {
    const [stored, cloned] = await Promise.all([
      this.stored(scope.orgId, scope.sessionId),
      this.cloned(scope),
    ]);
    if (stored === null) return;
    if (cloned) throw this.locked(stored);
    await this.persist(
      scope.orgId,
      scope.actorId,
      scope.sessionId,
      stored,
      null,
    );
  }

  /**
   * A new chat's first message names its repository (the composer chip): checked as `PUT` checks it, except the
   * engine, which admission decides for the turn itself. Refused with the catalogue code; nothing is saved.
   */
  async assertNewChat(
    caller: RepositoryCaller,
    repository: SessionRepository,
  ): Promise<void> {
    await this.assertUsable(caller, repository);
  }

  /** The new chat's repository, saved once admission created the chat (the run is already pinned to it). */
  async saveNewChat(
    orgId: string,
    actorId: string,
    sessionId: string,
    repository: SessionRepository,
  ): Promise<void> {
    await this.persist(orgId, actorId, sessionId, null, repository);
  }

  /** What admission pins on a turn of an existing chat; null when it has none. Throws when it cannot be read. */
  stored(orgId: string, sessionId: string): Promise<SessionRepository | null> {
    return this.options.preferences
      .readSessionRepository(orgId, sessionId)
      .catch(() => {
        throw new HarnessUnavailableError(
          "The chat's repository could not be read",
        );
      });
  }

  private async assertEngine(
    caller: RepositoryCaller,
    sessionId: string,
  ): Promise<void> {
    const hasEngine =
      caller.agentEngines !== undefined
        ? caller.agentEngines.length > 0
        : (
            await this.options.engines.forTurn(
              caller.orgId,
              caller.actorId,
              sessionId,
            )
          ).engine !== null;
    if (!hasEngine) {
      throw refused(
        'REPOSITORY_NEEDS_ENGINE',
        'This chat has no coding engine to work on a repository',
      );
    }
  }

  private async assertUsable(
    caller: RepositoryCaller,
    repository: SessionRepository,
  ): Promise<void> {
    if (githubRepository(repository.url) === null) {
      throw refused(
        'REPOSITORY_NOT_GITHUB',
        'Only a GitHub repository (https://github.com/owner/name) can be used',
      );
    }
    let usable: boolean;
    try {
      usable = await this.options.toolsets.usableGithubInstance({
        orgId: caller.orgId,
        userId: caller.actorId,
        instanceId: repository.toolsetInstanceId,
        authorization: caller.authorization,
      });
    } catch {
      throw new HarnessUnavailableError(
        'Your GitHub connections could not be read',
      );
    }
    if (!usable) {
      throw refused(
        'REPOSITORY_TOOLSET_MISSING',
        'That GitHub connection is not one you can use, or it is not signed in',
      );
    }
  }

  private async persist(
    orgId: string,
    actorId: string,
    sessionId: string,
    before: SessionRepository | null,
    after: SessionRepository | null,
  ): Promise<void> {
    await this.options.transaction.run(
      { orgId, actor: actorRefOfUser(actorId) },
      async (tx) => {
        tx.external(() =>
          this.options.preferences.writeSessionRepository(
            orgId,
            sessionId,
            after,
          ),
        );
        return Promise.resolve({
          result: null,
          change: {
            scope: userScope(actorId),
            object: { type: 'repository', id: `session/${sessionId}` },
            revisionFrom: null,
            revisionTo: null,
            changeDigest: canonicalDigest({ before, after }),
            changed: ['repository'],
          },
        });
      },
    );
    this.options.logger?.info(
      'session.repository_set',
      harnessLogMeta('session.repository_set', {
        orgId,
        sessionId,
        actorId,
        repository:
          after === null ? null : (githubRepository(after.url) ?? 'invalid'),
      }),
    );
  }

  /** The chat's workspace holds the clone: the setup phase's `repo/.git/HEAD` is at its head. */
  private async cloned(scope: SessionExecutionScope): Promise<boolean> {
    try {
      const head = await this.options.workspaces.sessionFiles(scope);
      return head.files.some((file) => file.path === REPOSITORY_HEAD_PATH);
    } catch {
      throw new HarnessUnavailableError(
        "The chat's workspace could not be read",
      );
    }
  }

  private locked(stored: SessionRepository): HarnessConflictError {
    return refused(
      'REPOSITORY_LOCKED',
      `This chat already works on ${githubRepository(stored.url) ?? stored.url}`,
    );
  }

  private viewOf(
    scope: SessionExecutionScope,
    repository: SessionRepository,
    cloned: boolean,
  ): SessionRepositoryView {
    return {
      url: repository.url,
      baseRef: repository.baseRef ?? null,
      sessionBranch: sessionBranch(scope.sessionId),
      cloned,
    };
  }
}
