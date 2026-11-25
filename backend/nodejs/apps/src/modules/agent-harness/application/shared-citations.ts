import { performance } from 'perf_hooks';
import {
  RecordAccessUnavailableError,
  RecordReadAccess,
  SourceStatus,
} from '../ports/record-access';
import {
  CitationAccess,
  CitationDecision,
} from '../projection/citation-access';
import {
  CheckedSource,
  messageSources,
  sourceKey,
} from '../projection/citation-source';
import { LabelledTurn, labelPasses } from '../domain/turn-labels';
import {
  elapsedMs,
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';

/** A viewer of a conversation they do not own, and where they are looking. */
export interface CitationViewer {
  /** Their own bearer credential, as their request carried it. */
  authorization: string | undefined;
  orgId: string;
  /** Absent where no conversation is looked at (a memory list, P7.3). */
  sessionId?: string;
  runId?: string;
  requestId?: string;
  surface: 'stream' | 'conversation' | 'admission' | 'memory';
}

type Tally = { allowed: number; denied: number; unverifiable: number };

const viewerFields = (
  viewer: CitationViewer,
): Pick<
  CitationViewer,
  'orgId' | 'sessionId' | 'runId' | 'requestId' | 'surface'
> => ({
  orgId: viewer.orgId,
  sessionId: viewer.sessionId,
  runId: viewer.runId,
  requestId: viewer.requestId,
  surface: viewer.surface,
});

const DECISION_OF: Record<SourceStatus, CitationDecision> = {
  readable: 'shown',
  denied: 'denied',
  unresolved: 'unverifiable',
};

/** The suffix of a per-kind count field: `liveUnverifiable`, `indexedAllowed`. */
const KIND_FIELD: Record<keyof Tally, string> = {
  allowed: 'Allowed',
  denied: 'Denied',
  unverifiable: 'Unverifiable',
};

const TALLY_OF: Record<CitationDecision, keyof Tally> = {
  shown: 'allowed',
  denied: 'denied',
  unverifiable: 'unverifiable',
};

/**
 * The one batched permission check per shared view (P1b C-6, C-18): a
 * stream subscription or a conversation read asks once for every source its
 * citations name, with the viewer's own credential. Fails closed: when the
 * check cannot be made every citation but a web page's is hidden and the
 * failure is logged; a source the answer leaves out is hidden too. Log
 * lines carry counts per reason and, flat, per kind and reason
 * (`liveUnverifiable`), never a hidden source's id.
 */
export class SharedCitationService {
  constructor(
    private readonly records: RecordReadAccess | null,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  /** False without a record-access port: such a service can only hide every record citation. */
  checksRecords(): boolean {
    return this.records !== null;
  }

  async decide(
    viewer: CitationViewer,
    sources: Iterable<CheckedSource>,
  ): Promise<CitationAccess> {
    const distinct = new Map<string, CheckedSource>();
    for (const source of sources) {
      if (!distinct.has(sourceKey(source)))
        distinct.set(sourceKey(source), source);
    }
    if (distinct.size === 0) return CitationAccess.denyRecords();
    const started = performance.now();
    const fields = { ...viewerFields(viewer), records: distinct.size };
    const statuses = await this.lookup(viewer, distinct);
    if (typeof statuses === 'string') {
      this.logger.warn(
        'citations.access_check_failed',
        harnessLogMeta('citations.access_check_failed', {
          ...fields,
          reason: statuses,
          ms: elapsedMs(started),
          choice: 'hide_record_citations',
        }),
      );
      return CitationAccess.denyRecords();
    }
    const decisions = new Map<string, CitationDecision>();
    const total: Tally = { allowed: 0, denied: 0, unverifiable: 0 };
    const byKind: Record<string, number> = {};
    for (const [key, source] of distinct) {
      const status = statuses.get(key);
      const decision = status === undefined ? 'denied' : DECISION_OF[status];
      decisions.set(key, decision);
      total[TALLY_OF[decision]] += 1;
      for (const [tally, suffix] of Object.entries(KIND_FIELD)) {
        const field = `${source.kind}${suffix}`;
        byKind[field] =
          (byKind[field] ?? 0) + (TALLY_OF[decision] === tally ? 1 : 0);
      }
    }
    this.logger.info(
      'citations.access_checked',
      harnessLogMeta('citations.access_checked', {
        ...fields,
        ...total,
        ...byKind,
        ms: elapsedMs(started),
      }),
    );
    return CitationAccess.from(decisions);
  }

  /**
   * The turns whose label passes for the viewer (P2.5a): complete, and every
   * source readable with the viewer's own credential, in one batch over every
   * label. Nothing is remembered between calls, so a revoked permission is
   * seen by the next one. Fails closed: when the check cannot be made no turn
   * passes, and the failure is logged once.
   */
  async passingTurns(
    viewer: CitationViewer,
    turns: readonly LabelledTurn[],
  ): Promise<Set<string>> {
    const passed = new Set<string>();
    if (turns.length === 0) return passed;
    const complete = turns.filter((turn) => turn.label?.complete === true);
    const distinct = new Map<string, CheckedSource>();
    for (const turn of complete) {
      for (const source of turn.label?.sources ?? []) {
        distinct.set(sourceKey(source), source);
      }
    }
    const started = performance.now();
    const fields = {
      ...viewerFields(viewer),
      labels: turns.length,
      incomplete: turns.length - complete.length,
      sources: distinct.size,
    };
    let statuses: ReadonlyMap<string, SourceStatus> = new Map();
    if (distinct.size > 0) {
      const looked = await this.lookup(viewer, distinct);
      if (typeof looked === 'string') {
        this.logger.warn(
          'turn_labels.access_check_failed',
          harnessLogMeta('turn_labels.access_check_failed', {
            ...fields,
            reason: looked,
            ms: elapsedMs(started),
            choice: 'stub_turns',
          }),
        );
        return passed;
      }
      statuses = looked;
    }
    for (const turn of complete) {
      if (labelPasses(turn.label, statuses)) passed.add(turn.id);
    }
    this.logger.info(
      'turn_labels.access_checked',
      harnessLogMeta('turn_labels.access_checked', {
        ...fields,
        passed: passed.size,
        stubbed: turns.length - passed.size,
        ms: elapsedMs(started),
      }),
    );
    return passed;
  }

  /** The record authorization's answer with the viewer's own credential, or why it could not be asked. */
  private async lookup(
    viewer: CitationViewer,
    sources: ReadonlyMap<string, CheckedSource>,
  ): Promise<ReadonlyMap<string, SourceStatus> | string> {
    if (this.records === null) return 'not_configured';
    if (viewer.authorization === undefined || viewer.authorization === '') {
      return 'no_credential';
    }
    try {
      return await this.records.decide({
        authorization: viewer.authorization,
        sources: [...sources.values()],
      });
    } catch (error) {
      return error instanceof RecordAccessUnavailableError
        ? error.reason
        : 'unavailable';
    }
  }

  /** Conversation messages as a viewer who does not own them may see them. */
  async messagesFor<T>(viewer: CitationViewer, messages: T): Promise<T> {
    const access = await this.decide(viewer, messageSources(messages));
    return access.messages(messages);
  }
}
