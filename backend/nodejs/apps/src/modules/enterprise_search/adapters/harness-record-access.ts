import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  PipeshubRecordAccessAnswer,
  pipeshubRecordAccessAnswerSchema,
} from '../../agent-harness/contracts/generated/ext/pipeshub';
import {
  ArtifactReadAccess,
  ArtifactReadQuery,
} from '../../agent-harness/ports/artifact-access';
import {
  RecordAccessUnavailableError,
  RecordReadAccess,
  SourceReadQuery,
  SourceStatus,
} from '../../agent-harness/ports/record-access';
import {
  CheckedSource,
  sourceKey,
} from '../../agent-harness/projection/citation-source';
import {
  CircuitBreaker,
  timeoutOutcome,
  type CheckOutcome,
} from '../utils/access-check-breaker';

/** The query service's per-call cap (`record_access.MAX_RECORD_IDS`). */
export const RECORD_ACCESS_BATCH = 1000;
const ACCESS_CHECK_TIMEOUT_MS = 10_000;
const REFUSED = new Set([401, 403]);

/**
 * The breaker of this process's calls to the record authorization: while it keeps failing, every check that asks it
 * (a shared view's citations and files, a run's admission) fails closed at once instead of waiting out its budget, and
 * the service gets room to recover.
 */
export const recordAccessBreaker = new CircuitBreaker();

/**
 * Asks the query service (`POST /api/v1/records/access`, the P0.1 tiered
 * record authorization; live items through their indexed copy, C-18) with
 * the viewer's own credential. Node never mints a credential for this. More
 * sources than one call takes go in consecutive calls; a shared view rarely
 * cites more than a page of them.
 */
export class HarnessRecordReadAccess implements RecordReadAccess {
  constructor(private readonly aiBackend: () => string) {}

  async decide(
    query: SourceReadQuery,
  ): Promise<ReadonlyMap<string, SourceStatus>> {
    const distinct = new Map<string, CheckedSource>();
    for (const source of query.sources) {
      if (!distinct.has(sourceKey(source)))
        distinct.set(sourceKey(source), source);
    }
    const sources = [...distinct.values()];
    const decided = new Map<string, SourceStatus>();
    for (let at = 0; at < sources.length; at += RECORD_ACCESS_BATCH) {
      const batch = sources.slice(at, at + RECORD_ACCESS_BATCH);
      const asked = new Set(batch.map(sourceKey));
      for (const { source, status } of await this.ask(
        query.authorization,
        batch,
        query.timeoutMs,
      )) {
        // The authority answers only for what it was asked.
        if (asked.has(sourceKey(source)))
          decided.set(sourceKey(source), status);
      }
    }
    return decided;
  }

  private async ask(
    authorization: string,
    references: CheckedSource[],
    timeoutMs?: number,
  ): Promise<Array<{ source: CheckedSource; status: SourceStatus }>> {
    const answer = await askRecordAccess(
      this.aiBackend(),
      authorization,
      { references },
      timeoutMs,
    );
    if (answer.decisions === undefined) throw malformed();
    return answer.decisions;
  }
}

/**
 * The artifact half of the same authority (SS0): each artifact decided by its
 * provenance, with the viewer's own credential.
 */
export class HarnessArtifactReadAccess implements ArtifactReadAccess {
  constructor(private readonly aiBackend: () => string) {}

  async decide(
    query: ArtifactReadQuery,
  ): Promise<ReadonlyMap<string, SourceStatus>> {
    const distinct = [...new Set(query.artifactIds)];
    const decided = new Map<string, SourceStatus>();
    for (let at = 0; at < distinct.length; at += RECORD_ACCESS_BATCH) {
      const batch = distinct.slice(at, at + RECORD_ACCESS_BATCH);
      const asked = new Set(batch);
      const answer = await askRecordAccess(
        this.aiBackend(),
        query.authorization,
        { artifactIds: batch },
        query.timeoutMs,
      );
      if (answer.artifacts === undefined) throw malformed();
      for (const { artifactId, status } of answer.artifacts) {
        if (asked.has(artifactId)) decided.set(artifactId, status);
      }
    }
    return decided;
  }
}

const malformed = (): RecordAccessUnavailableError =>
  new RecordAccessUnavailableError(
    'invalid_response',
    'malformed access answer',
  );

const isTimeout = (error: unknown): boolean => {
  const name = (error as { name?: unknown } | null)?.name;
  return name === 'TimeoutError' || name === 'AbortError';
};

/**
 * One call to the record authorization, unless its breaker is open: then it fails closed at once. `timeoutMs` is the
 * caller's budget for one try (a read's); without it the call gets the authority's own budget and a second try.
 */
async function askRecordAccess(
  aiBackend: string,
  authorization: string,
  body: { references: CheckedSource[] } | { artifactIds: string[] },
  timeoutMs?: number,
): Promise<PipeshubRecordAccessAnswer> {
  const ticket = recordAccessBreaker.allow();
  if (ticket === undefined) {
    throw new RecordAccessUnavailableError(
      'unavailable',
      'the record authorization keeps failing; not asked',
    );
  }
  const budget = timeoutMs ?? ACCESS_CHECK_TIMEOUT_MS;
  const sent = Date.now();
  let outcome: CheckOutcome = 'failed';
  let response;
  try {
    response = await new AIServiceCommand({
      uri: `${aiBackend}/api/v1/records/access`,
      method: HttpMethod.POST,
      headers: {
        Authorization: authorization,
        'Content-Type': 'application/json',
      },
      body,
      timeoutMs: budget,
      maxAttempts: timeoutMs === undefined ? 2 : 1,
    }).execute();
    // Any answer the service gave, a refusal included, says it is up.
    outcome = response.statusCode >= 500 ? 'failed' : 'ok';
  } catch (error) {
    if (isTimeout(error)) {
      outcome = timeoutOutcome(budget, Date.now() - sent - budget);
    }
    throw new RecordAccessUnavailableError(
      'unavailable',
      error instanceof Error ? error.name : 'request failed',
    );
  } finally {
    recordAccessBreaker.record(ticket, outcome);
  }
  if (REFUSED.has(response.statusCode)) {
    throw new RecordAccessUnavailableError(
      'refused',
      `status ${String(response.statusCode)}`,
    );
  }
  if (response.statusCode !== 200) {
    throw new RecordAccessUnavailableError(
      'unavailable',
      `status ${String(response.statusCode)}`,
    );
  }
  const parsed = pipeshubRecordAccessAnswerSchema.safeParse(response.data);
  if (!parsed.success) throw malformed();
  return parsed.data;
}
