/** PERF-B (QA22-F2): a run's history sealed beside its document and read back through the reference. */
import { expect } from 'chai';
import { RunHistoryContent } from '../../../src/modules/agent-harness/application/run-history-content';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import {
  HarnessUnavailableError,
  JournalPayloadMissingError,
  PayloadStoreUnavailableError,
} from '../../../src/modules/agent-harness/domain/errors';
import { parseContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import { RunHistoryRef } from '../../../src/modules/agent-harness/domain/run-history';
import { dataKeyRing } from './support/data-protection';

const RUN = { orgId: 'org-1', sessionId: 'session-1', runId: 'run-2' };
const CANARY = 'forty-two days of leave';
const TURNS = [
  { role: 'user_query', content: 'How much leave do I get?' },
  { role: 'bot_response', content: CANARY, tools: [{ name: 'read', result: 'x'.repeat(300) }] },
];

const rejection = async (work: Promise<unknown>): Promise<unknown> =>
  work.then(
    () => expect.fail('expected a rejection'),
    (error: unknown) => error,
  );

describe('RunHistoryContent', () => {
  const world = (partBytes?: number) => {
    const blobs = new InMemoryPayloadStore();
    const ring = dataKeyRing();
    return { blobs, ring, content: new RunHistoryContent(ring.cipher, blobs, blobs, partBytes) };
  };
  const storeRefs = (ref: RunHistoryRef): string[] => ref.parts.map((part) => parseContentRef(part)!.storeRef);

  it('PERFB-1: the history is stored as sealed parts the run and its session own, marked referenced', async () => {
    const { blobs, content } = world(64);
    const ref = (await content.store(RUN, TURNS, 'tx'))!;

    expect(ref.turns).to.equal(2);
    expect(ref.parts.length).to.be.greaterThan(1);
    expect(ref.parts.every((part) => parseContentRef(part)?.writerRunId === RUN.runId)).to.equal(true);
    for (const stored of storeRefs(ref)) {
      expect(blobs.owners.get(stored)).to.deep.equal({ sessionId: RUN.sessionId, runId: RUN.runId });
      expect(blobs.referenced.has(stored), 'referenced').to.equal(true);
      expect(blobs.objects.get(stored)!.toString('utf8')).to.not.include(CANARY);
    }
    expect(blobs.objects.size).to.equal(ref.parts.length);
  });

  it('PERFB-2: the reference reads back the entries that were stored, in order', async () => {
    for (const partBytes of [16, 4096]) {
      const { content } = world(partBytes);
      const ref = (await content.store(RUN, TURNS))!;
      expect(await content.read(RUN, ref), `parts of ${String(partBytes)}`).to.deep.equal(TURNS);
    }
  });

  it('a history is readable only as the run that wrote it: another run or session opens nothing', async () => {
    const { content } = world();
    const ref = (await content.store(RUN, TURNS))!;
    for (const other of [
      { ...RUN, runId: 'run-3' },
      { ...RUN, sessionId: 'session-2' },
    ]) {
      const error = await rejection(content.read(other, ref));
      expect(error).to.be.instanceOf(JournalPayloadMissingError);
      expect((error as JournalPayloadMissingError).reason).to.equal('corrupt');
    }
  });

  it('PERFB-10: with no history, or nowhere to seal one, nothing is stored and the caller keeps it inline', async () => {
    const { blobs, ring, content } = world();
    expect(await content.store(RUN, [])).to.equal(null);
    expect(await new RunHistoryContent(null, blobs).store(RUN, TURNS)).to.equal(null);
    expect(await new RunHistoryContent(ring.cipher, null).store(RUN, TURNS)).to.equal(null);
    expect(blobs.objects.size).to.equal(0);
  });

  it('a store with no reference marks still keeps the history', async () => {
    const blobs = new InMemoryPayloadStore();
    const content = new RunHistoryContent(dataKeyRing().cipher, blobs);
    const ref = (await content.store(RUN, TURNS))!;
    expect(blobs.referenced.size).to.equal(0);
    expect(await content.read(RUN, ref)).to.deep.equal(TURNS);
  });

  it('PERFB-8: a part that is gone, changed or malformed is terminal; a store that cannot answer is not', async () => {
    const { blobs, content } = world(64);
    const ref = (await content.store(RUN, TURNS))!;
    const [first, second] = storeRefs(ref) as [string, string];

    const swapped = { ...ref, parts: [ref.parts[1]!, ref.parts[0]!, ...ref.parts.slice(2)] };
    expect(((await rejection(content.read(RUN, swapped))) as JournalPayloadMissingError).reason).to.equal('corrupt');

    const malformed = { ...ref, parts: ['not-a-content-ref', ...ref.parts.slice(1)] };
    expect(((await rejection(content.read(RUN, malformed))) as JournalPayloadMissingError).reason).to.equal('missing');

    const kept = blobs.objects.get(second)!;
    blobs.objects.set(second, Buffer.from(kept).fill(1, kept.length - 8));
    const tampered = await rejection(content.read(RUN, ref));
    expect(tampered).to.be.instanceOf(JournalPayloadMissingError);
    expect((tampered as JournalPayloadMissingError).reason).to.equal('corrupt');
    blobs.objects.set(second, kept);

    blobs.objects.delete(first);
    const gone = await rejection(content.read(RUN, ref));
    expect(gone).to.be.instanceOf(JournalPayloadMissingError);
    expect(gone).to.include({ reason: 'missing', ref: ref.parts[0] });

    const down = new RunHistoryContent(dataKeyRing().cipher, {
      put: blobs.put.bind(blobs),
      get: () => Promise.reject(new PayloadStoreUnavailableError('get', new Error('down'))),
    });
    expect(await rejection(down.read(RUN, ref))).to.be.instanceOf(PayloadStoreUnavailableError);
  });

  it('a reference with nothing composed to read it is a configuration fault, never a failed run', async () => {
    const { content } = world();
    const ref = (await content.store(RUN, TURNS))!;
    expect(await rejection(new RunHistoryContent(null, null).read(RUN, ref))).to.be.instanceOf(HarnessUnavailableError);
  });
});
