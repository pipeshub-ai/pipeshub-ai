import { performance } from 'perf_hooks';
import {
  HarnessConflictError,
  HarnessNotFoundError,
  HarnessUnavailableError,
} from '../domain/errors';
import { TERMINAL_RUN_STATUSES } from '../domain/execution';
import {
  ForkCitationLink,
  ForkCounts,
  ForkedMessage,
  ForkKey,
  ForkOutcome,
  ForkResult,
  foreignAnswers,
  forkTranscript,
  SESSION_FORK_ARTIFACT_PAGE,
  SESSION_FORK_MAX_MESSAGES,
} from '../domain/session-forks';
import { SessionRole, sessionRoleOf } from '../domain/session-roles';
import { WorkspaceError } from '../domain/workspace';
import { JournalStore } from '../ports/journal-store';
import { SessionRunLookup } from '../ports/session-erasure';
import {
  ArtifactCopier,
  ForkAlreadyCommittedError,
  SessionForkStore,
} from '../ports/session-forks';
import { SessionParticipantRecorder } from '../ports/session-participants';
import {
  ArtifactCopyRef,
  artifactVersionKey,
  MarkerArtifactRef,
  markerArtifactRefs,
  rewriteArtifactMarkers,
} from '../projection/artifact-access';
import { checkedSources } from '../projection/citation-source';
import {
  elapsedMs,
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import { CitationViewer, SharedCitationService } from './shared-citations';
import { SharedArtifactService } from './shared-artifacts';
import { WorkspaceObjectResealer } from './workspace-object-resealer';
import { WorkspaceService } from './workspace-service';

export interface ForkRequest {
  orgId: string;
  /** The source session. */
  sessionId: string;
  /** The verified caller; never a body field. */
  actorId: string;
  agentKey?: string;
  requestKey: string;
  /** The caller's own credential, for the checks of their own turns. */
  authorization?: string;
  requestId?: string;
}

export interface SessionForkServiceOptions<Tx> {
  store: SessionForkStore<Tx>;
  runs: SessionRunLookup;
  journal: Pick<JournalStore<Tx>, 'lastSeq'>;
  recorder: SessionParticipantRecorder<Tx>;
  citations: SharedCitationService;
  artifacts: SharedArtifactService;
  /** Absent: a fork with artifacts to copy is refused (503). */
  copier: ArtifactCopier | null;
  /** Absent: an owner's fork is refused (503): their workspace would be left behind. */
  workspaces: {
    service: Pick<WorkspaceService, 'forkSession'>;
    objects: WorkspaceObjectResealer;
  } | null;
  transaction: <T>(work: (tx: Tx) => Promise<T>) => Promise<T>;
  newSessionId: () => string;
  logger?: HarnessLogger;
  now?: () => number;
}

/**
 * "Continue privately" (SS3): a participant copies a shared session into a
 * new one of their own. Their own turns as they see them (an owner's
 * unchecked, anyone else's citations and artifacts re-checked with their
 * credential), another participant's turn whose label passes for them
 * (P2.5a, checked with their credential whoever owns the session), every
 * other participant's turn a stub; citations, artifacts
 * and workspace objects copied, never shared; one transaction commits the
 * conversation and `session.forked` on both streams.
 */
export class SessionForkService<Tx> {
  private readonly logger: HarnessLogger;
  private readonly now: () => number;

  constructor(private readonly options: SessionForkServiceOptions<Tx>) {
    this.logger = options.logger ?? silentHarnessLogger;
    this.now = options.now ?? Date.now;
  }

  async fork(request: ForkRequest): Promise<ForkOutcome> {
    const started = performance.now();
    const { store } = this.options;
    const source = await store.source({
      orgId: request.orgId,
      sessionId: request.sessionId,
      actorId: request.actorId,
      ...(request.agentKey !== undefined ? { agentKey: request.agentKey } : {}),
    });
    const role =
      source === null
        ? null
        : sessionRoleOf(source.participants, request.actorId);
    if (source === null || role === null) {
      throw this.refused(request, 'not_found', new HarnessNotFoundError());
    }
    const key: ForkKey = {
      orgId: request.orgId,
      sourceSessionId: request.sessionId,
      actorId: request.actorId,
      requestKey: request.requestKey,
    };
    const done = await store.find(key);
    if (done?.result) return this.replayed(request, done.result, started);
    await this.assertNoActiveRun(request);
    if (source.messages.length > SESSION_FORK_MAX_MESSAGES) {
      throw this.refused(
        request,
        'too_long',
        new HarnessConflictError(
          'SESSION_HISTORY_TOO_LARGE',
          'This conversation is too long to copy',
        ),
      );
    }
    const record = await store.begin(
      key,
      this.options.newSessionId(),
      this.now(),
    );
    if (record.result) return this.replayed(request, record.result, started);
    const forkSessionId = record.forkSessionId;

    const forker: CitationViewer = {
      authorization: request.authorization,
      orgId: request.orgId,
      sessionId: request.sessionId,
      ...(request.requestId !== undefined
        ? { requestId: request.requestId }
        : {}),
      surface: 'conversation',
    };
    // Another participant's turn is copied only when its label passes for the
    // forker, decided now with the forker's own credential (P2.5a).
    const passing = await this.options.citations.passingTurns(
      forker,
      foreignAnswers(
        source.messages,
        request.actorId,
        source.participants.ownerId,
      ),
    );
    const transcript = forkTranscript(
      source.messages,
      request.actorId,
      source.participants.ownerId,
      passing,
    );
    const viewer: CitationViewer | null = role === 'owner' ? null : forker;
    const own = transcript.filter(
      (message) => !message.foreignTurn && message.passed !== true,
    );
    const passed = transcript.filter((message) => message.passed === true);
    const shownOwn = await this.citationCheck(viewer, own);
    const shownPassed = await this.citationCheck(forker, passed);
    const copies = new Map([
      ...(await this.copyArtifacts(
        request,
        forkSessionId,
        viewer,
        refsOf(own),
      )),
      ...(await this.copyArtifacts(
        request,
        forkSessionId,
        forker,
        refsOf(passed),
      )),
    ]);
    const refs = dedupe([...refsOf(own), ...refsOf(passed)]);
    const files = await this.forkWorkspace(request, role, forkSessionId);

    let citations = 0;
    let citationsHidden = 0;
    const messages: ForkedMessage[] = transcript.map((message) => {
      if (message.foreignTurn) return message;
      const kept = message.citations.filter(
        message.passed === true ? shownPassed : shownOwn,
      );
      citations += kept.length;
      citationsHidden += message.citations.length - kept.length;
      return {
        ...message,
        content: rewriteArtifactMarkers(message.content, copies),
        citations: kept,
      };
    });
    const counts: ForkCounts = {
      messages: own.length + passed.length,
      stubs: transcript.length - own.length - passed.length,
      citations,
      citationsHidden,
      artifacts: copies.size,
      artifactsHidden: refs.length - copies.size,
      files,
    };
    let result: ForkResult;
    try {
      result = await this.options.transaction(async (tx) => {
        const atSeq =
          (
            await this.options.journal.lastSeq(
              request.orgId,
              [request.sessionId],
              tx,
            )
          )[request.sessionId] ?? 0;
        const committed: ForkResult = {
          sessionId: forkSessionId,
          fromSessionId: request.sessionId,
          atSeq,
          role,
          counts,
        };
        await store.commit(
          {
            key,
            forkSessionId,
            source: {
              sessionType: source.sessionType,
              agentKey: source.agentKey,
              // The copy sits next to its source in Recents, where one title twice reads as one chat listed twice.
              title: source.title ? `${source.title} (copy)` : source.title,
              modelInfo: source.modelInfo,
              durable: source.durable,
            },
            messages,
            result: committed,
            now: this.now(),
          },
          tx,
        );
        await this.options.recorder.forked(
          request.orgId,
          forkSessionId,
          { actorId: request.actorId, fromSessionId: request.sessionId, atSeq },
          tx,
        );
        return committed;
      });
    } catch (error) {
      if (error instanceof ForkAlreadyCommittedError) {
        const winner = await store.find(key);
        if (winner?.result)
          return this.replayed(request, winner.result, started);
      }
      throw error;
    }
    this.logger.info(
      'session.fork_completed',
      harnessLogMeta('session.fork_completed', {
        orgId: request.orgId,
        sessionId: request.sessionId,
        forkSessionId,
        actorId: request.actorId,
        requestId: request.requestId,
        role,
        atSeq: result.atSeq,
        replayed: false,
        ...counts,
        ms: elapsedMs(started),
      }),
    );
    return { ...result, replayed: false };
  }

  private async assertNoActiveRun(request: ForkRequest): Promise<void> {
    const runs = await this.options.runs.runsOf(
      request.orgId,
      request.sessionId,
    );
    if (runs.some((run) => !TERMINAL_RUN_STATUSES.has(run.status))) {
      throw this.refused(
        request,
        'run_active',
        new HarnessConflictError(
          'RUN_ACTIVE',
          'A task is running in this conversation; continue privately once it ends',
        ),
      );
    }
  }

  /** Which of the own turns' citations the fork keeps: all of an owner's, the readable ones otherwise. */
  private async citationCheck(
    viewer: CitationViewer | null,
    own: readonly ForkedMessage[],
  ): Promise<(citation: ForkCitationLink) => boolean> {
    if (viewer === null) return () => true;
    const data = own.flatMap((message) =>
      message.citations.map((citation) => citation.data),
    );
    if (data.length === 0) return () => false;
    const access = await this.options.citations.decide(
      viewer,
      checkedSources(data),
    );
    return (citation) => access.shows(citation.data);
  }

  private async copyArtifacts(
    request: ForkRequest,
    forkSessionId: string,
    viewer: CitationViewer | null,
    refs: readonly MarkerArtifactRef[],
  ): Promise<Map<string, ArtifactCopyRef>> {
    const copies = new Map<string, ArtifactCopyRef>();
    if (refs.length === 0) return copies;
    let wanted = refs;
    if (viewer !== null) {
      const access = await this.options.artifacts.decide(
        viewer,
        refs.map((ref) => ref.artifactId),
      );
      wanted = refs.filter((ref) => access.shows(ref.artifactId));
    }
    if (wanted.length === 0) return copies;
    const copier = this.options.copier;
    if (copier === null) {
      throw this.refused(
        request,
        'unavailable',
        new HarnessUnavailableError('Artifacts cannot be copied right now'),
      );
    }
    for (let at = 0; at < wanted.length; at += SESSION_FORK_ARTIFACT_PAGE) {
      const outcomes = await copier.copy({
        orgId: request.orgId,
        fromConversationId: request.sessionId,
        toConversationId: forkSessionId,
        actorId: request.actorId,
        artifacts: wanted.slice(at, at + SESSION_FORK_ARTIFACT_PAGE),
      });
      for (const outcome of outcomes) {
        if (outcome.copy !== null) {
          copies.set(artifactVersionKey(outcome), outcome.copy);
        }
      }
    }
    return copies;
  }

  /** The owner's files under the fork's key; nobody else sees the source workspace (U3, v1). */
  private async forkWorkspace(
    request: ForkRequest,
    role: SessionRole,
    forkSessionId: string,
  ): Promise<number> {
    if (role !== 'owner') return 0;
    const workspaces = this.options.workspaces;
    if (workspaces === null) {
      throw this.refused(
        request,
        'unavailable',
        new HarnessUnavailableError('The workspace cannot be copied right now'),
      );
    }
    try {
      const forked = await workspaces.service.forkSession(
        {
          orgId: request.orgId,
          fromSessionId: request.sessionId,
          toSessionId: forkSessionId,
          actorId: request.actorId,
        },
        () =>
          workspaces.objects.between(
            request.orgId,
            request.sessionId,
            forkSessionId,
          ),
      );
      return forked?.files ?? 0;
    } catch (error) {
      if (error instanceof WorkspaceError) {
        throw this.refused(request, error.code.toLowerCase(), error);
      }
      throw error;
    }
  }

  private replayed(
    request: ForkRequest,
    result: ForkResult,
    started: number,
  ): ForkOutcome {
    this.logger.info(
      'session.fork_completed',
      harnessLogMeta('session.fork_completed', {
        orgId: request.orgId,
        sessionId: request.sessionId,
        forkSessionId: result.sessionId,
        actorId: request.actorId,
        requestId: request.requestId,
        role: result.role,
        atSeq: result.atSeq,
        replayed: true,
        ms: elapsedMs(started),
      }),
    );
    return { ...result, replayed: true };
  }

  private refused<E extends Error>(
    request: ForkRequest,
    reason: string,
    error: E,
  ): E {
    this.logger.warn(
      'session.fork_refused',
      harnessLogMeta('session.fork_refused', {
        orgId: request.orgId,
        sessionId: request.sessionId,
        actorId: request.actorId,
        requestId: request.requestId,
        reason,
      }),
    );
    return error;
  }
}

const dedupe = (refs: readonly MarkerArtifactRef[]): MarkerArtifactRef[] => [
  ...new Map(refs.map((ref) => [artifactVersionKey(ref), ref])).values(),
];

const refsOf = (messages: readonly ForkedMessage[]): MarkerArtifactRef[] =>
  dedupe(messages.flatMap((message) => markerArtifactRefs(message.content)));
