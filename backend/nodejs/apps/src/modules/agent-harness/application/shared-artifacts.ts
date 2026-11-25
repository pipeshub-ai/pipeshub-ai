import { performance } from 'perf_hooks';
import { ArtifactReadAccess } from '../ports/artifact-access';
import {
  RecordAccessUnavailableError,
  SourceStatus,
} from '../ports/record-access';
import {
  ArtifactAccess,
  messageArtifactIds,
} from '../projection/artifact-access';
import {
  elapsedMs,
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import { CitationViewer } from './shared-citations';

type Tally = Record<'allowed' | 'denied' | 'unverifiable', number>;

const TALLY_OF: Record<SourceStatus, keyof Tally> = {
  readable: 'allowed',
  denied: 'denied',
  unresolved: 'unverifiable',
};

/**
 * The one batched artifact check per shared view (SS0), the artifact
 * counterpart of `SharedCitationService`: a stream subscription or a
 * conversation read asks once for the artifacts it shows, with the viewer's
 * own credential. Fails closed: when the check cannot be made every artifact
 * is hidden and the failure is logged. Log lines carry counts, never an
 * artifact id or name.
 */
export class SharedArtifactService {
  constructor(
    private readonly artifacts: ArtifactReadAccess | null,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  async decide(
    viewer: CitationViewer,
    artifactIds: Iterable<string>,
  ): Promise<ArtifactAccess> {
    const distinct = [...new Set(artifactIds)];
    if (distinct.length === 0) return ArtifactAccess.hideAll();
    const started = performance.now();
    const fields = {
      orgId: viewer.orgId,
      sessionId: viewer.sessionId,
      runId: viewer.runId,
      requestId: viewer.requestId,
      surface: viewer.surface,
      artifacts: distinct.length,
    };
    const fail = (reason: string): ArtifactAccess => {
      this.logger.warn(
        'artifacts.access_check_failed',
        harnessLogMeta('artifacts.access_check_failed', {
          ...fields,
          reason,
          ms: elapsedMs(started),
          choice: 'hide_artifacts',
        }),
      );
      return ArtifactAccess.hideAll();
    };
    if (this.artifacts === null) return fail('not_configured');
    if (viewer.authorization === undefined || viewer.authorization === '') {
      return fail('no_credential');
    }
    let statuses: ReadonlyMap<string, SourceStatus>;
    try {
      statuses = await this.artifacts.decide({
        authorization: viewer.authorization,
        artifactIds: distinct,
      });
    } catch (error) {
      return fail(
        error instanceof RecordAccessUnavailableError
          ? error.reason
          : 'unavailable',
      );
    }
    const tally: Tally = { allowed: 0, denied: 0, unverifiable: 0 };
    const shown: string[] = [];
    for (const artifactId of distinct) {
      const status = statuses.get(artifactId) ?? 'denied';
      tally[TALLY_OF[status]] += 1;
      if (status === 'readable') shown.push(artifactId);
    }
    this.logger.info(
      'artifacts.access_checked',
      harnessLogMeta('artifacts.access_checked', {
        ...fields,
        ...tally,
        ms: elapsedMs(started),
      }),
    );
    return ArtifactAccess.showing(shown);
  }

  /** Conversation messages as a reader who does not own them may see their artifacts. */
  async messagesFor<T>(viewer: CitationViewer, messages: T): Promise<T> {
    const access = await this.decide(viewer, messageArtifactIds(messages));
    return access.messages(messages);
  }
}
