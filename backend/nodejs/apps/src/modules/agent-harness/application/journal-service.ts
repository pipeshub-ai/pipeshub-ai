import { createHash } from 'node:crypto';
import { canonicalJsonBytes } from '../contracts/canonical-json';
import { JournalPayloadMissingError } from '../domain/errors';
import {
  isClaimChecked,
  JOURNAL_INLINE_PAYLOAD_MAX_BYTES,
  JournalAppend,
  JournalEvent,
  journalPayloadSealContext,
  PreparedJournalAppend,
  PreparedJournalEvent,
  validateJournalAppend,
} from '../domain/journal';
import { PayloadStore } from '../ports/payload-store';
import { payloadsWithin } from './payloads-within';
import { EnvelopeCipher } from './envelope-cipher';
import { journalContentFailure } from './sealed-journal-store';
import { auditProjection } from '../domain/audit';
import { contentRefsIn } from '../domain/payload-references';
import type { JournalAppendRecorder } from '../ports/journal-append-recorder';

const sha256 = (bytes: Buffer): string =>
  `sha256:${createHash('sha256').update(bytes).digest('hex')}`;

/**
 * The write and read edges of the journal: validation against the generated
 * catalog, claim-check of large payloads, and verified reads. Prepared
 * outside the store transaction, a payload is stored first (an orphan on
 * abort is collected); prepared inside one (`tx`), it is stored in it when
 * the store can, so the append's reference mark sees it.
 * A claim-checked payload is stored sealed; its ref, hash and size describe
 * the stored bytes, so the object is addressed by ciphertext and a shredded
 * key never leaves a reusable object behind.
 */
export class JournalService {
  constructor(
    private readonly payloads: PayloadStore,
    private readonly cipher: EnvelopeCipher,
    private readonly inlineMaxBytes = JOURNAL_INLINE_PAYLOAD_MAX_BYTES,
    /** The journal's recorders (`HarnessEdition.journalRecorders`): each takes its part of a claim-checked event here. */
    private readonly recorders: readonly Pick<
      JournalAppendRecorder<unknown>,
      'name' | 'take'
    >[] = [],
  ) {}

  async prepare(
    append: JournalAppend,
    tx?: unknown,
  ): Promise<PreparedJournalAppend> {
    validateJournalAppend(append);
    const payloads = payloadsWithin(this.payloads, tx);
    const events = await Promise.all(
      append.events.map(async (event): Promise<PreparedJournalEvent> => {
        if (!isClaimChecked(event.data, this.inlineMaxBytes)) {
          return structuredClone(event);
        }
        const bytes = canonicalJsonBytes(event.data);
        const sealed = await this.cipher.sealBlob(
          append.orgId,
          'journal.payload',
          journalPayloadSealContext(append, event.type),
          bytes,
        );
        const hash = sha256(sealed);
        const ref = await payloads.put(append.orgId, hash, sealed, {
          sessionId: append.sessionId,
          ...(append.runId !== undefined ? { runId: append.runId } : {}),
        });
        const { data, ...envelope } = event;
        const audit = auditProjection(event.type, data, event.auditFields);
        const references = contentRefsIn(data);
        const taken = this.takenOf(event.type, data);
        return {
          ...structuredClone(envelope),
          payloadRef: { ref, hash, size: sealed.length },
          ...(audit !== null ? { audit } : {}),
          ...(references.length > 0 ? { references } : {}),
          ...(taken !== undefined ? { taken } : {}),
        };
      }),
    );
    return { ...append, events };
  }

  private takenOf(
    type: string,
    data: Record<string, unknown>,
  ): Record<string, unknown> | undefined {
    let taken: Record<string, unknown> | undefined;
    for (const recorder of this.recorders) {
      const item = recorder.take(type, data);
      if (item !== null && item !== undefined)
        taken = { ...taken, [recorder.name]: item };
    }
    return taken;
  }

  /** Inlines claim-checked payloads; throws JournalPayloadMissingError. */
  async resolve(events: JournalEvent[]): Promise<JournalEvent[]> {
    return Promise.all(
      events.map(async (event) => {
        if (event.payloadRef === undefined) return event;
        const { payloadRef, ...envelope } = event;
        const bytes = await this.payloads.get(event.orgId, payloadRef.ref);
        if (bytes === null) {
          throw new JournalPayloadMissingError(payloadRef.ref, 'missing');
        }
        if (
          bytes.length !== payloadRef.size ||
          sha256(bytes) !== payloadRef.hash
        ) {
          throw new JournalPayloadMissingError(payloadRef.ref, 'corrupt');
        }
        let plaintext: Buffer;
        try {
          plaintext = await this.cipher.openBlob(
            event.orgId,
            'journal.payload',
            journalPayloadSealContext(event, event.type),
            bytes,
          );
        } catch (error) {
          throw journalContentFailure(error, payloadRef.ref);
        }
        let data: unknown;
        try {
          data = JSON.parse(plaintext.toString('utf8'));
        } catch {
          throw new JournalPayloadMissingError(payloadRef.ref, 'corrupt');
        }
        return { ...envelope, data: data as Record<string, unknown> };
      }),
    );
  }
}
