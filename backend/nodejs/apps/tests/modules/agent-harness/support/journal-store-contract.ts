/**
 * Behaviour every JournalStore (with JournalService in front of it) must
 * share: J-1..J-4. Runs against the in-memory adapter (unit suite) and MongoDB
 * (`journal-store.mongo.itest.ts`).
 */
import { expect } from 'chai';
import { randomBytes } from 'crypto';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import {
  JournalPayloadMissingError,
  JournalRejectedError,
} from '../../../../src/modules/agent-harness/domain/errors';
import {
  JOURNAL_INLINE_PAYLOAD_MAX_BYTES,
  JournalAppend,
  JournalEventInput,
} from '../../../../src/modules/agent-harness/domain/journal';
import { JournalStore } from '../../../../src/modules/agent-harness/ports/journal-store';
import { PayloadStore } from '../../../../src/modules/agent-harness/ports/payload-store';
import {
  bulkyOutcome,
  citation,
  position,
  turnStarted,
  workerEvent,
} from './journal-fixtures';
import { testCipher } from './data-protection';

export interface JournalBackend<Tx> {
  journal: JournalStore<Tx>;
  payloads: PayloadStore;
  /** Runs `work` in the store's commit transaction, as `orgId` (a PostgreSQL unit runs as one organisation). */
  transact<T>(work: (tx: Tx) => Promise<T>, orgId: string): Promise<T>;
  /** Overwrites a stored payload's bytes (simulates corruption / restore skew). */
  corruptPayload(orgId: string, ref: string): Promise<void>;
  deletePayload(orgId: string, ref: string): Promise<void>;
  reset?(): Promise<void>;
}

export function describeJournalStoreContract<Tx>(
  name: string,
  createBackend: () => JournalBackend<Tx>,
): void {
  describe(`JournalStore contract: ${name}`, function () {
    this.timeout(30_000);
    let backend: JournalBackend<Tx>;
    let service: JournalService;
    let orgId: string;

    beforeEach(async () => {
      backend = createBackend();
      await backend.reset?.();
      service = new JournalService(backend.payloads, testCipher());
      // 24 hex digits: the id shape every store takes (PostgreSQL checks it on every org column).
      orgId = randomBytes(12).toString('hex');
    });

    const append = async (
      overrides: Partial<JournalAppend>,
      events: JournalEventInput[],
    ) => {
      const prepared = await service.prepare({
        ...position({ orgId }),
        writer: 'worker',
        ...overrides,
        events,
      });
      return backend.transact(
        (tx) => backend.journal.append(prepared, tx),
        prepared.orgId,
      );
    };

    const rejection = async (work: Promise<unknown>) => {
      try {
        await work;
      } catch (error) {
        expect(error).to.be.instanceOf(JournalRejectedError);
        return (error as JournalRejectedError).code;
      }
      return expect.fail('expected a JournalRejectedError');
    };

    describe('J-1 gap-free seq per stream', () => {
      it('allocates contiguous seq across batches and reports lastSeq', async () => {
        const first = await append({}, [turnStarted(1), citation()]);
        const second = await append({}, [turnStarted(2)]);
        expect([first.firstSeq, first.lastSeq]).to.deep.equal([1, 2]);
        expect([second.firstSeq, second.lastSeq]).to.deep.equal([3, 3]);
        const events = await backend.journal.readStream(
          orgId,
          'session-1',
          0,
          100,
        );
        expect(events.map((event) => event.seq)).to.deep.equal([1, 2, 3]);
        expect(events.map((event) => event.type)).to.deep.equal([
          'turn.started',
          'x.pipeshub.citation.recorded',
          'turn.started',
        ]);
        expect(
          await backend.journal.lastSeq(orgId, ['session-1', 'absent']),
        ).to.deep.equal({ 'session-1': 3, absent: 0 });
      });

      it('keeps child streams independent of the root stream', async () => {
        await append({}, [turnStarted(1)]);
        const child = await append(
          { streamId: 'child-run-1', runId: 'child-run-1' },
          [turnStarted(1), turnStarted(2)],
        );
        const root = await append({}, [turnStarted(2)]);
        expect([child.firstSeq, child.lastSeq]).to.deep.equal([1, 2]);
        expect([root.firstSeq, root.lastSeq]).to.deep.equal([2, 2]);
        expect(
          await backend.journal.lastSeq(orgId, ['session-1', 'child-run-1']),
        ).to.deep.equal({ 'session-1': 2, 'child-run-1': 2 });
      });

      it('stores the full envelope with DB time and reads a run by (runId, seq)', async () => {
        await append({}, [turnStarted(1)]);
        await append({ runId: 'run-2', attemptId: 'attempt-2', fence: 2 }, [
          turnStarted(1),
        ]);
        const [event] = await backend.journal.readRun(orgId, 'run-2', 0, 10);
        expect(event).to.include({
          orgId,
          sessionId: 'session-1',
          streamId: 'session-1',
          seq: 2,
          runId: 'run-2',
          attemptId: 'attempt-2',
          fence: 2,
          type: 'turn.started',
          schemaVersion: 1,
          critical: true,
          writer: 'worker',
          visibility: 'owner',
        });
        expect(event?.data).to.deep.equal({ turn: 1 });
        expect(event?.ts).to.match(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/);
        expect(
          (await backend.journal.readRun(orgId, 'run-2', 1, 10)).map((e) => e.seq),
        ).to.deep.equal([2]);
        expect(
          await backend.journal.readRun(orgId, 'run-2', 2, 10),
        ).to.deep.equal([]);
      });

      it("does not read another org's stream", async () => {
        await append({}, [turnStarted(1)]);
        expect(
          await backend.journal.readStream('other-org', 'session-1', 0, 10),
        ).to.deep.equal([]);
      });
    });

    describe('J-2 writer allowlist', () => {
      it('refuses a node-only type from a worker and writes nothing', async () => {
        expect(
          await rejection(
            append({}, [
              turnStarted(1),
              workerEvent('run.completed', { outcome: 'answered' }),
            ]),
          ),
        ).to.equal('WRITER_NOT_ALLOWED');
        expect(
          await rejection(
            append({}, [
              workerEvent('interaction.resolved', {
                interactionId: 'i-1',
                decision: 'approve',
                by: 'p_user',
                channel: 'web',
              }),
            ]),
          ),
        ).to.equal('WRITER_NOT_ALLOWED');
        expect(
          await backend.journal.readStream(orgId, 'session-1', 0, 10),
        ).to.deep.equal([]);
        expect(
          await backend.journal.lastSeq(orgId, ['session-1']),
        ).to.deep.equal({ 'session-1': 0 });
      });

      it('A-13: refuses a worker event that widens visibility and writes nothing', async () => {
        for (const visibility of ['shared', 'audit'] as const) {
          expect(
            await rejection(
              append({}, [
                turnStarted(1),
                workerEvent('turn.started', { turn: 2 }, { visibility }),
              ]),
            ),
          ).to.equal('VISIBILITY_NOT_ALLOWED');
        }
        expect(
          await backend.journal.readStream(orgId, 'session-1', 0, 10),
        ).to.deep.equal([]);
        const shared = await append({ writer: 'node' }, [
          workerEvent(
            'run.completed',
            { outcome: 'answered' },
            { visibility: 'shared' },
          ),
        ]);
        expect(shared.lastSeq).to.equal(1);
      });

      it('lets node append node types and refuses worker-only ones from node', async () => {
        const appended = await append({ writer: 'node' }, [
          workerEvent('run.completed', { outcome: 'answered' }),
        ]);
        expect(appended.lastSeq).to.equal(1);
        expect(
          await rejection(append({ writer: 'node' }, [turnStarted(1)])),
        ).to.equal('WRITER_NOT_ALLOWED');
      });

      it('accepts loop.state_saved from a worker only', async () => {
        const saved = workerEvent('loop.state_saved', {
          slots: { 'pre_run.goal': { text: 'Summarize Q3' }, phase: 'execute' },
        });
        const appended = await append({}, [saved]);
        expect(appended.lastSeq).to.equal(1);
        expect(
          await rejection(append({ writer: 'node' }, [saved])),
        ).to.equal('WRITER_NOT_ALLOWED');
        expect(
          await rejection(
            append({}, [workerEvent('loop.state_saved', { slots: [] })]),
          ),
        ).to.not.equal('WRITER_NOT_ALLOWED');
      });
    });

    describe('J-3 typed validation', () => {
      it('refuses an unknown type', async () => {
        expect(
          await rejection(
            append({}, [workerEvent('TEXT_MESSAGE_CONTENT', {})]),
          ),
        ).to.equal('UNKNOWN_EVENT_TYPE');
        expect(
          await rejection(append({}, [workerEvent('x.other.thing.done', {})])),
        ).to.equal('UNKNOWN_EVENT_TYPE');
      });

      it('refuses schema-invalid data and envelope fields', async () => {
        expect(
          await rejection(
            append({}, [workerEvent('turn.started', { turn: 0 })]),
          ),
        ).to.equal('INVALID_EVENT');
        expect(
          await rejection(
            append({}, [workerEvent('turn.started', { turn: 1, extra: true })]),
          ),
        ).to.equal('INVALID_EVENT');
        expect(
          await rejection(
            append({}, [
              workerEvent(
                'turn.started',
                { turn: 1 },
                {
                  visibility: 'everyone' as never,
                },
              ),
            ]),
          ),
        ).to.equal('INVALID_EVENT');
        expect(
          await rejection(append({ fence: 0 }, [turnStarted(1)])),
        ).to.equal('INVALID_EVENT');
        expect(
          await backend.journal.readStream(orgId, 'session-1', 0, 10),
        ).to.deep.equal([]);
      });

      it('refuses a reserved __proto__ key that zod would drop silently', async () => {
        const receipt = JSON.parse('{"ok":true,"nested":{"__proto__":{"admin":true}}}');
        expect(
          await rejection(
            append({}, [
              workerEvent('tool.outcome', {
                invocationId: 'inv-1',
                status: 'ok',
                receipt,
                labels: { integrity: 'external' },
              }),
            ]),
          ),
        ).to.equal('INVALID_EVENT');
        expect(
          await backend.journal.readStream(orgId, 'session-1', 0, 10),
        ).to.deep.equal([]);
      });

      it('reports the index of the offending event', async () => {
        try {
          await append({}, [
            turnStarted(1),
            turnStarted(2),
            workerEvent('nope.x', {}),
          ]);
          expect.fail('expected a rejection');
        } catch (error) {
          expect((error as JournalRejectedError).index).to.equal(2);
        }
      });
    });

    describe('J-4 claim-check', () => {
      it('keeps payloads up to 256 KB inline and claim-checks larger ones', async () => {
        await append({}, [
          bulkyOutcome(JOURNAL_INLINE_PAYLOAD_MAX_BYTES - 1024),
        ]);
        await append({}, [bulkyOutcome(JOURNAL_INLINE_PAYLOAD_MAX_BYTES + 1)]);
        const [inline, checked] = await backend.journal.readStream(
          orgId,
          'session-1',
          0,
          10,
        );
        expect(inline?.payloadRef).to.equal(undefined);
        expect(inline?.data).to.not.equal(undefined);
        expect(checked?.data).to.equal(undefined);
        expect(checked?.payloadRef?.hash).to.match(/^sha256:[0-9a-f]{64}$/);
        expect(checked?.payloadRef?.size).to.be.greaterThan(
          JOURNAL_INLINE_PAYLOAD_MAX_BYTES,
        );

        const [resolved] = await service.resolve([checked!]);
        expect(resolved?.payloadRef).to.equal(undefined);
        expect(resolved?.data).to.deep.equal(
          bulkyOutcome(JOURNAL_INLINE_PAYLOAD_MAX_BYTES + 1).data,
        );
      });

      it('detects a corrupt payload on read', async () => {
        await append({}, [bulkyOutcome(JOURNAL_INLINE_PAYLOAD_MAX_BYTES + 1)]);
        const [checked] = await backend.journal.readStream(
          orgId,
          'session-1',
          0,
          1,
        );
        await backend.corruptPayload(orgId, checked!.payloadRef!.ref);
        try {
          await service.resolve([checked!]);
          expect.fail('expected a missing payload');
        } catch (error) {
          expect(error).to.be.instanceOf(JournalPayloadMissingError);
          expect((error as JournalPayloadMissingError).reason).to.equal(
            'corrupt',
          );
          expect((error as JournalPayloadMissingError).errorType).to.equal(
            'journal_payload_missing',
          );
        }
      });

      it('reports a deleted payload as journal_payload_missing', async () => {
        await append({}, [bulkyOutcome(JOURNAL_INLINE_PAYLOAD_MAX_BYTES + 1)]);
        const [checked] = await backend.journal.readStream(
          orgId,
          'session-1',
          0,
          1,
        );
        await backend.deletePayload(orgId, checked!.payloadRef!.ref);
        try {
          await service.resolve([checked!]);
          expect.fail('expected a missing payload');
        } catch (error) {
          expect(error).to.be.instanceOf(JournalPayloadMissingError);
          expect((error as JournalPayloadMissingError).reason).to.equal(
            'missing',
          );
        }
      });

      it("never resolves another org's payload ref", async () => {
        await append({}, [bulkyOutcome(JOURNAL_INLINE_PAYLOAD_MAX_BYTES + 1)]);
        const [checked] = await backend.journal.readStream(
          orgId,
          'session-1',
          0,
          1,
        );
        expect(
          await backend.payloads.get('other-org', checked!.payloadRef!.ref),
        ).to.equal(null);
      });
    });
  });
}
