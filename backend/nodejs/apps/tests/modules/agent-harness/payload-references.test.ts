/**
 * T5 (GC-2): payload owners and references. A stored payload names the
 * session and run that wrote it; an append marks every payload its events
 * point at, so GC can tell what nothing references.
 */
import { expect } from 'chai';
import {
  contentRefsIn,
  referencedPayloads,
} from '../../../src/modules/agent-harness/domain/payload-references';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { PayloadReferencingJournalStore } from '../../../src/modules/agent-harness/application/payload-referencing-journal-store';
import {
  InMemoryJournalStore,
  InMemoryPayloadStore,
} from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { sealRunContent } from '../../../src/modules/agent-harness/application/run-content-service';
import { testCipher } from './support/data-protection';
import { bulkyOutcome, position, workerEvent } from './support/journal-fixtures';

const sealedRef = (storeRef: string): string => `sealed:run-1/${storeRef}`;

describe('T5 payload references (GC-2)', () => {
  it('finds every sealed content ref in event data, at any depth, and nothing else', () => {
    const data = {
      result: { ref: sealedRef('memory:org-1:sha256:a'), size: 3 },
      parts: [
        { content: { ref: sealedRef('memory:org-1:sha256:b') } },
        'sealed:',
        'sealed:run-only',
        'inline:aGk=',
        'mongo:org-1:sha256:c',
      ],
    };
    expect(contentRefsIn(data).sort()).to.deep.equal([
      'memory:org-1:sha256:a',
      'memory:org-1:sha256:b',
    ]);
  });

  it('collects claim-checks and the refs a claim-checked event carried', () => {
    expect(
      referencedPayloads([
        { payloadRef: { ref: 'mongo:o:h1', hash: 'h1', size: 1 }, references: ['mongo:o:h2'] },
        { data: { x: sealedRef('mongo:o:h3') } },
        { data: { x: 1 } },
      ]).sort(),
    ).to.deep.equal(['mongo:o:h1', 'mongo:o:h2', 'mongo:o:h3']);
  });

  it('a claim-checked payload records its owner and keeps the refs its data held', async () => {
    const payloads = new InMemoryPayloadStore();
    const journals = new JournalService(payloads, testCipher(), 64);
    const outcome = bulkyOutcome(200);
    (outcome.data['receipt'] as Record<string, unknown>)['contentRef'] = {
      ref: sealedRef('memory:org-1:sha256:inner'),
    };
    const prepared = await journals.prepare({
      ...position(),
      writer: 'worker',
      events: [outcome],
    });
    const event = prepared.events[0]!;
    expect(event.payloadRef).to.not.equal(undefined);
    expect(event.references).to.deep.equal(['memory:org-1:sha256:inner']);
    expect(payloads.owners.get(event.payloadRef!.ref)).to.deep.equal({
      sessionId: 'session-1',
      runId: 'run-1',
    });
  });

  it('sealed content records the run that wrote it', async () => {
    const payloads = new InMemoryPayloadStore();
    const { ref } = await sealRunContent(
      testCipher(),
      payloads,
      { orgId: 'org-1', sessionId: 'session-1', runId: 'run-7' },
      Buffer.from('a large tool result'),
    );
    const storeRef = ref.slice('sealed:run-7/'.length);
    expect(payloads.owners.get(storeRef)).to.deep.equal({
      sessionId: 'session-1',
      runId: 'run-7',
    });
    expect(payloads.referenced.has(storeRef)).to.equal(false);
  });

  it('an append marks the payloads its events point at, and only those', async () => {
    const payloads = new InMemoryPayloadStore();
    const cited = await payloads.put('org-1', 'sha256:cited', Buffer.from('a'), {
      sessionId: 'session-1',
      runId: 'run-1',
    });
    const orphan = await payloads.put('org-1', 'sha256:orphan', Buffer.from('b'), {
      sessionId: 'session-1',
      runId: 'run-1',
    });
    const store = new PayloadReferencingJournalStore(
      new InMemoryJournalStore(),
      payloads,
    );
    await store.append(
      {
        ...position(),
        writer: 'worker',
        events: [workerEvent('tool.outcome', {
          invocationId: 'inv-1',
          status: 'ok',
          receipt: { body: { ref: sealedRef(cited) } },
          labels: { integrity: 'external' },
        })],
      },
      undefined,
    );
    expect(payloads.referenced.has(cited)).to.equal(true);
    expect(payloads.referenced.has(orphan)).to.equal(false);
  });

  it("another org's ref is never marked", async () => {
    const payloads = new InMemoryPayloadStore();
    const theirs = await payloads.put('org-2', 'sha256:x', Buffer.from('a'));
    await payloads.markReferenced('org-1', [theirs]);
    expect(payloads.referenced.size).to.equal(0);
  });
});
