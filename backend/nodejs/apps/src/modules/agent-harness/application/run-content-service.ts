import { createHash } from 'node:crypto';
import { AttemptClaims } from '../domain/authority';
import {
  HarnessConflictError,
  HarnessNotFoundError,
  JournalPayloadMissingError,
  PayloadStoreUnavailableError,
  SealedContentError,
} from '../domain/errors';
import {
  attemptMayReadRun,
  attemptOwnsRun,
  HarnessRun,
  journalStreamId,
} from '../domain/execution';
import {
  CONTENT_MAX_BYTES,
  contentSealContext,
  ContentRefusal,
  formatContentRef,
  parseContentRef,
} from '../domain/run-content';
import { PayloadStore } from '../ports/payload-store';
import { EnvelopeCipher } from './envelope-cipher';
import { RunReader } from './harness-authority';
import {
  harnessLogMeta,
  HarnessLogger,
  runLogFields,
  silentHarnessLogger,
} from './harness-log';
import { journalContentFailure } from './sealed-journal-store';

export interface StoredContent {
  ref: string;
  /** Plaintext bytes; the worker checks its own hash and size on read. */
  size: number;
}

/** The content does not fit the content route; nothing was stored. */
export class ContentTooLargeError extends Error {
  constructor() {
    super(`Content over ${String(CONTENT_MAX_BYTES)} bytes is refused`);
    this.name = 'ContentTooLargeError';
  }
}

export interface SealedRunContent {
  ref: string;
  storedSize: number;
}

/**
 * `plaintext` sealed as content `run` wrote: the org's key, the writer run
 * and its stream in the AAD, so only that session's runs read it back.
 */
export async function sealRunContent(
  cipher: EnvelopeCipher,
  blobs: PayloadStore,
  run: Pick<HarnessRun, 'orgId' | 'sessionId' | 'runId'>,
  plaintext: Buffer,
): Promise<SealedRunContent> {
  const sealed = await cipher.sealBlob(
    run.orgId,
    'content',
    contentSealContext(journalStreamId(run), run.runId),
    plaintext,
  );
  const digest = `sha256:${createHash('sha256').update(sealed).digest('hex')}`;
  const storeRef = await blobs.put(run.orgId, digest, sealed, {
    sessionId: run.sessionId,
    runId: run.runId,
  });
  return {
    ref: formatContentRef({ writerRunId: run.runId, storeRef }),
    storedSize: sealed.length,
  };
}

/**
 * The plaintext of content `writer` sealed, the reverse of `sealRunContent`;
 * null when the store holds no such object.
 */
export async function openRunContent(
  cipher: EnvelopeCipher,
  blobs: PayloadStore,
  writer: Parameters<typeof journalStreamId>[0] & Pick<HarnessRun, 'orgId'>,
  storeRef: string,
): Promise<Buffer | null> {
  const stored = await blobs.get(writer.orgId, storeRef);
  if (stored === null) return null;
  return cipher.openBlob(
    writer.orgId,
    'content',
    contentSealContext(journalStreamId(writer), writer.runId),
    stored,
  );
}

/**
 * Worker content sealed at rest (DP-1). The attempt token decides who asks;
 * the run store decides whether that attempt may still write (it owns the
 * run) or read (it may read the run's journal). A ref is readable by the
 * runs of the writer's session, which render the whole session's transcript,
 * and by no one else: the writer run and stream are in the AAD, the org is
 * in the key and the payload store's namespace.
 */
export class RunContentService {
  constructor(
    private readonly runs: RunReader,
    private readonly cipher: EnvelopeCipher,
    private readonly blobs: PayloadStore,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  async put(attempt: AttemptClaims, plaintext: Buffer): Promise<StoredContent> {
    const run = await this.run(attempt);
    if (!attemptOwnsRun(run, attempt)) {
      this.refused(run, 'not_owner');
      throw new HarnessConflictError(
        'STALE_ATTEMPT',
        'Worker no longer owns this run',
      );
    }
    if (plaintext.length > CONTENT_MAX_BYTES) {
      this.refused(run, 'too_large', { size: plaintext.length });
      throw new ContentTooLargeError();
    }
    let sealed: SealedRunContent;
    try {
      sealed = await sealRunContent(this.cipher, this.blobs, run, plaintext);
    } catch (error) {
      if (error instanceof PayloadStoreUnavailableError) {
        this.refused(run, 'store_unavailable');
      }
      throw error;
    }
    this.logger.debug(
      'content.sealed',
      harnessLogMeta('content.sealed', {
        ...runLogFields(run),
        ref: sealed.ref,
        size: plaintext.length,
        storedSize: sealed.storedSize,
      }),
    );
    return { ref: sealed.ref, size: plaintext.length };
  }

  async get(attempt: AttemptClaims, raw: string): Promise<Buffer> {
    const reader = await this.run(attempt);
    if (!attemptMayReadRun(reader, attempt)) {
      this.refused(reader, 'not_owner');
      throw new HarnessConflictError(
        'STALE_ATTEMPT',
        'Worker no longer owns this run',
      );
    }
    return this.read(reader, raw);
  }

  /**
   * Content as `reader`'s session may read it, for a caller that already
   * authorized `reader` (the activity panel's command log, UX5).
   */
  async read(reader: HarnessRun, raw: string): Promise<Buffer> {
    const ref = parseContentRef(raw);
    if (ref === null) throw this.missing(reader, raw, 'malformed_ref');
    let writer: HarnessRun | null = reader;
    if (ref.writerRunId !== reader.runId) {
      writer = await this.runs.get(reader.orgId, ref.writerRunId);
      // Same answer as an absent object: a foreign ref reveals nothing. A
      // child writes to its own stream; its parent reads its result (P6.1).
      if (writer?.sessionId !== reader.sessionId) {
        throw this.missing(reader, raw, 'foreign_run');
      }
    }
    let plaintext: Buffer | null;
    try {
      plaintext = await openRunContent(
        this.cipher,
        this.blobs,
        writer,
        ref.storeRef,
      );
    } catch (error) {
      if (error instanceof PayloadStoreUnavailableError) {
        this.refused(reader, 'store_unavailable', { ref: raw });
      }
      if (error instanceof SealedContentError) {
        this.refused(reader, error.reason, { ref: raw });
      }
      throw journalContentFailure(error, raw);
    }
    if (plaintext === null) throw this.missing(reader, raw, 'missing');
    this.logger.debug(
      'content.opened',
      harnessLogMeta('content.opened', {
        ...runLogFields(reader),
        ref: raw,
        writerRunId: writer.runId,
        size: plaintext.length,
      }),
    );
    return plaintext;
  }

  private async run(attempt: AttemptClaims): Promise<HarnessRun> {
    const run = await this.runs.get(attempt.orgId, attempt.runId);
    if (run === null) throw new HarnessNotFoundError();
    return run;
  }

  private missing(
    run: HarnessRun,
    ref: string,
    reason: ContentRefusal,
  ): JournalPayloadMissingError {
    this.refused(run, reason, { ref });
    return new JournalPayloadMissingError(ref, 'missing');
  }

  private refused(
    run: HarnessRun,
    reason: ContentRefusal,
    extra: { ref?: string; size?: number } = {},
  ): void {
    this.logger.warn(
      'content.refused',
      harnessLogMeta('content.refused', {
        ...runLogFields(run),
        reason,
        ...extra,
      }),
    );
  }
}
