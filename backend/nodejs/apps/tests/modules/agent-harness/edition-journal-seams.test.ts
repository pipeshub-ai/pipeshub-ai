/**
 * The extension points an edition uses to keep its own records of the journal, to have its own events audited and
 * to name its own features to workers. Each does nothing until an edition uses it.
 *
 * EJS-1 a journal recorder takes its part of each event while the data is plain, inline or claim-checked, and
 *       writes what it took after the append, in the append's transaction, keyed by the events' seqs; an append
 *       it takes nothing of is not handed to it.
 * EJS-2 an added audit event is audited by the named fields only; a built-in type, or the same type with other
 *       fields, is refused.
 * EJS-3 a Node names an edition's claim features only while they are bound, and its own features always.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import { InMemoryJournalStore, InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RecordingJournalStore } from '../../../src/modules/agent-harness/application/recording-journal-store';
import { AGENT_HARNESS_AUTHORITY, AGENT_HARNESS_CLAIM_FEATURES, AGENT_HARNESS_EXECUTION_SERVICE } from '../../../src/modules/agent-harness/binders/tokens';
import { addAuditedEvent, auditProjection } from '../../../src/modules/agent-harness/domain/audit';
import type { JournalEventInput } from '../../../src/modules/agent-harness/domain/journal';
import { KNOWLEDGE_EXPORTS_FEATURE, NODE_CAPABILITIES } from '../../../src/modules/agent-harness/domain/node-capabilities';
import type { AppendedEvents, JournalAppendRecorder } from '../../../src/modules/agent-harness/ports/journal-append-recorder';
import { nodeCapabilitiesOf } from '../../../src/modules/agent-harness/transport/internal-routes';
import { testCipher } from './support/data-protection';

const exported = (records: number): JournalEventInput => ({
  type: 'x.pipeshub.knowledge.exported',
  schemaVersion: 1,
  critical: false,
  visibility: 'owner',
  data: { taskId: 'exp_1', phase: 'started', records, requested: records, argsDigest: `sha256:${'a'.repeat(64)}` },
});

const finished: JournalEventInput = {
  ...exported(0),
  data: { ...exported(0).data, phase: 'finished', exported: 40, outcome: 'completed' },
};

class Records implements JournalAppendRecorder<string> {
  readonly name = 'records';
  readonly written: Array<{ tx: string; appended: AppendedEvents; taken: readonly unknown[] }> = [];
  take(type: string, data: Readonly<Record<string, unknown>>): unknown {
    return type === 'x.pipeshub.knowledge.exported' && data['phase'] === 'started' ? data['records'] : null;
  }
  async record(tx: string, appended: AppendedEvents, taken: readonly unknown[]): Promise<void> {
    this.written.push({ tx, appended, taken });
  }
}

const position = { orgId: 'org-1', sessionId: 's-1', streamId: 's-1', runId: 'run-1', attemptId: 'a-1', fence: 1 };

describe('Edition journal seams: recorders, audited events and claim features', () => {
  for (const [label, inlineMax] of [
    ['inline', undefined],
    ['claim-checked', 1],
  ] as const) {
    it(`EJS-1: a recorder takes its part of ${label} events and writes it after the append, in its transaction`, async () => {
      const records = new Records();
      const payloads = new InMemoryPayloadStore();
      const service = new JournalService(payloads, testCipher(), inlineMax, [records]);
      const store = new RecordingJournalStore<string>(
        new InMemoryJournalStore() as never,
        [records],
      );
      const prepared = await service.prepare({ ...position, writer: 'worker', events: [finished, exported(40)] });
      const appended = await store.append(prepared, 'tx-1');
      expect(records.written).to.have.length(1);
      const [written] = records.written;
      expect(written!.tx).to.equal('tx-1');
      expect(written!.taken).to.deep.equal([null, 40]);
      expect(written!.appended).to.include({ orgId: 'org-1', sessionId: 's-1', runId: 'run-1', streamId: 's-1' });
      expect(written!.appended.firstSeq).to.equal(appended.firstSeq);

      await store.append(await service.prepare({ ...position, writer: 'worker', events: [finished] }), 'tx-2');
      expect(records.written).to.have.length(1);
    });
  }

  it('EJS-1: no recorder, no take kept on a claim-checked event', async () => {
    const service = new JournalService(new InMemoryPayloadStore(), testCipher(), 1);
    const prepared = await service.prepare({ ...position, writer: 'worker', events: [exported(3)] });
    expect(prepared.events[0]!.taken).to.equal(undefined);
  });

  it('EJS-2: an added event is audited by its named fields only; a built-in or a changed one is refused', () => {
    expect(auditProjection('x.edition.example.raised', { count: 3, secret: 's' })).to.equal(null);
    addAuditedEvent('x.edition.example.raised', ['count', 'limit']);
    addAuditedEvent('x.edition.example.raised', ['count', 'limit']);
    expect(auditProjection('x.edition.example.raised', { count: 3, secret: 's' })?.fields).to.deep.equal({ count: 3 });
    expect(() => addAuditedEvent('x.edition.example.raised', ['count'])).to.throw(/already audited/);
    expect(() => addAuditedEvent('run.completed', ['outcome'])).to.throw(/already audited/);
  });

  it("EJS-3: an edition's claim features are named only while bound; this Node's own always", () => {
    const container = new Container();
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({});
    container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue({});
    expect(nodeCapabilitiesOf(container)).to.deep.equal(NODE_CAPABILITIES);
    expect(NODE_CAPABILITIES.features).to.include(KNOWLEDGE_EXPORTS_FEATURE);
    container
      .bind(AGENT_HARNESS_CLAIM_FEATURES)
      .toConstantValue({ features: () => ['edition_feature', KNOWLEDGE_EXPORTS_FEATURE] });
    expect(nodeCapabilitiesOf(container).features).to.deep.equal([...NODE_CAPABILITIES.features, 'edition_feature']);
  });
});
