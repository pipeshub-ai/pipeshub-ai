/**
 * DP-1: worker content over the inline threshold is sealed by Node (purpose
 * `content`) and bound to its org, stream and writer run. The HTTP half
 * (token authority, size, media type) is `run-content.http.test.ts`.
 */
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryDataKeyStore } from '../../../src/modules/agent-harness/adapters/in-memory-data-key-store';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import {
  ContentTooLargeError,
  RunContentService,
} from '../../../src/modules/agent-harness/application/run-content-service';
import { AttemptClaims } from '../../../src/modules/agent-harness/domain/authority';
import {
  HarnessConflictError,
  JournalContentUnavailableError,
  JournalPayloadMissingError,
  PayloadStoreUnavailableError,
} from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  CONTENT_MAX_BYTES,
  formatContentRef,
  parseContentRef,
} from '../../../src/modules/agent-harness/domain/run-content';
import { inlineContent } from '../../../src/modules/agent-harness/projection/timeline';
import { PayloadStore } from '../../../src/modules/agent-harness/ports/payload-store';
import {
  contractClock,
  ContractHarness,
  createContractHarness,
} from './support/execution-store-contract';
import { dataKeyRing, testKek } from './support/data-protection';

const SENTINEL = 'SENTINEL-content-9b3a-customer-tool-result';
const big = (text = SENTINEL): Buffer =>
  Buffer.from(`${text}${'z'.repeat(20 * 1024)}`);

const recording = (): { logger: HarnessLogger; lines: unknown[][] } => {
  const lines: unknown[][] = [];
  const at =
    (level: string) =>
    (message: string, meta: Record<string, unknown> = {}) => {
      lines.push([level, message, meta]);
    };
  return {
    lines,
    logger: {
      debug: at('debug'),
      info: at('info'),
      warn: at('warn'),
      error: at('error'),
    },
  };
};

const claimsOf = (run: HarnessRun): AttemptClaims => ({
  orgId: run.orgId,
  runId: run.runId,
  attemptId: run.attemptId!,
  fence: run.fence,
  workerId: run.workerId!,
});

const refusal = async (work: Promise<unknown>): Promise<{ error: unknown }> => {
  try {
    await work;
  } catch (error) {
    return { error };
  }
  return expect.fail('expected a refusal');
};

describe('DP-1 sealed worker content', () => {
  let h: ContractHarness;
  let payloads: InMemoryPayloadStore;
  let keyStore: InMemoryDataKeyStore;
  let ring: ReturnType<typeof dataKeyRing>;
  let log: ReturnType<typeof recording>;
  let content: RunContentService;

  beforeEach(() => {
    const clock = contractClock();
    const store = new InMemoryExecutionStore(clock);
    payloads = new InMemoryPayloadStore();
    h = createContractHarness(
      {
        executionStore: store,
        journal: store.journal,
        payloads,
        admissionStore: store,
        dispatches: async () => [],
        deletePayload: async () => undefined,
      },
      clock,
    );
    keyStore = new InMemoryDataKeyStore();
    ring = dataKeyRing({ store: keyStore });
    log = recording();
    content = new RunContentService(
      h.service,
      ring.cipher,
      payloads,
      log.logger,
    );
  });

  const claimed = async (
    overrides: Parameters<ContractHarness['admit']>[0] = {},
    workerId = 'worker-a',
  ): Promise<HarnessRun> => {
    const admitted = await h.admit(overrides);
    return h.claim(admitted.run, workerId);
  };

  const complete = (run: HarnessRun) =>
    h.service.commit({
      commandId: `${run.runId}:done`,
      orgId: run.orgId,
      runId: run.runId,
      workerId: run.workerId!,
      attemptId: run.attemptId!,
      fence: run.fence,
      expectedRevision: run.revision,
      status: 'completed',
      outcome: 'answer',
      events: [{ type: 'run_completed', data: { outcome: 'answer' } }],
      outbox: [],
    });

  it('CS-1: seals through the payload store; the stored bytes hold no plaintext; a read opens them', async () => {
    const run = await claimed();
    const plaintext = big();
    const stored = await content.put(claimsOf(run), plaintext);
    expect(stored.size).to.equal(plaintext.length);
    const ref = parseContentRef(stored.ref);
    expect(ref?.writerRunId).to.equal(run.runId);
    expect(payloads.objects.size).to.equal(1);
    const [bytes] = [...payloads.objects.values()];
    expect(bytes!.toString('latin1')).not.to.contain(SENTINEL);
    expect(bytes!.subarray(0, 4).toString('ascii')).to.equal('PHSB');
    expect(
      (await content.get(claimsOf(run), stored.ref)).equals(plaintext),
    ).to.equal(true);
  });

  it('CS-2: a later run of the same session reads it; another session or org does not', async () => {
    const first = await claimed();
    const sessionId = first.sessionId;
    const stored = await content.put(claimsOf(first), big());
    await complete(first);

    const next = await claimed({ sessionId }, 'worker-b');
    expect(next.sessionId).to.equal(sessionId);
    expect(
      (await content.get(claimsOf(next), stored.ref)).toString(),
    ).to.contain(SENTINEL);

    const foreign = await claimed({}, 'worker-c');
    expect(foreign.sessionId).not.to.equal(sessionId);
    const refused = await refusal(content.get(claimsOf(foreign), stored.ref));
    expect(refused.error).to.be.instanceOf(JournalPayloadMissingError);

    const otherTenant = h.newTenant();
    const otherOrg = await claimed({ ...otherTenant }, 'worker-d');
    const crossOrg = await refusal(content.get(claimsOf(otherOrg), stored.ref));
    expect(crossOrg.error).to.be.instanceOf(JournalPayloadMissingError);
    // Even naming its own run as the writer, the store ref is another org's.
    const forged = formatContentRef({
      writerRunId: otherOrg.runId,
      storeRef: parseContentRef(stored.ref)!.storeRef,
    });
    const forgedRead = await refusal(content.get(claimsOf(otherOrg), forged));
    expect(forgedRead.error).to.be.instanceOf(JournalPayloadMissingError);

    const reasons = log.lines
      .filter(([, message]) => message === 'content.refused')
      .map(([level, , meta]) => [level, (meta as { reason: string }).reason]);
    expect(reasons).to.deep.equal([
      ['warn', 'foreign_run'],
      ['warn', 'foreign_run'],
      ['warn', 'missing'],
    ]);
  });

  it('CS-3: a write or read from an attempt that no longer owns the run is refused', async () => {
    const run = await claimed();
    const stored = await content.put(claimsOf(run), big());
    h.setNow((run.leaseExpiresAt ?? 0) + 1);
    const reclaimed = await h.claim(run, 'worker-b');
    for (const work of [
      content.put(claimsOf(run), big()),
      content.get(claimsOf(run), stored.ref),
    ]) {
      const { error } = await refusal(work);
      expect(error).to.be.instanceOf(HarnessConflictError);
      expect((error as HarnessConflictError).code).to.equal('STALE_ATTEMPT');
    }
    expect(
      (await content.get(claimsOf(reclaimed), stored.ref)).toString(),
    ).to.contain(SENTINEL);
  });

  it('CS-4: a flipped byte, a journal payload passed off as content, and a ref naming another writer fail closed', async () => {
    const first = await claimed();
    const stored = await content.put(claimsOf(first), big());
    const storeRef = parseContentRef(stored.ref)!.storeRef;

    const original = payloads.objects.get(storeRef)!;
    const flipped = Buffer.from(original);
    flipped[flipped.length - 1] = flipped[flipped.length - 1]! ^ 0xff;
    payloads.objects.set(storeRef, flipped);
    const tampered = await refusal(content.get(claimsOf(first), stored.ref));
    expect(tampered.error).to.be.instanceOf(JournalPayloadMissingError);
    payloads.objects.set(storeRef, original);

    const journalBlob = await ring.cipher.sealBlob(
      first.orgId,
      'journal.payload',
      { streamId: first.sessionId, runId: first.runId },
      big(),
    );
    const journalRef = await payloads.put(first.orgId, 'sha256:j', journalBlob);
    const purpose = await refusal(
      content.get(
        claimsOf(first),
        formatContentRef({ writerRunId: first.runId, storeRef: journalRef }),
      ),
    );
    expect(purpose.error).to.be.instanceOf(JournalPayloadMissingError);

    await complete(first);
    const next = await claimed({ sessionId: first.sessionId }, 'worker-b');
    const renamed = formatContentRef({ writerRunId: next.runId, storeRef });
    const moved = await refusal(content.get(claimsOf(next), renamed));
    expect(moved.error).to.be.instanceOf(JournalPayloadMissingError);
    expect(
      log.lines
        .filter(([, message]) => message === 'content.refused')
        .map(([, , meta]) => (meta as { reason: string }).reason),
    ).to.deep.equal(['corrupt', 'corrupt', 'corrupt']);

    for (const malformed of ['blob:abc', 'sealed:', 'sealed:run-only']) {
      const { error } = await refusal(content.get(claimsOf(next), malformed));
      expect(error).to.be.instanceOf(JournalPayloadMissingError);
    }
  });

  it('CS-5: after a shred the content is unreadable; another org is unaffected', async () => {
    const run = await claimed();
    const other = await claimed({ ...h.newTenant() }, 'worker-b');
    const mine = await content.put(claimsOf(run), big());
    const theirs = await content.put(claimsOf(other), big());
    await ring.keys.shred(run.orgId);
    const { error } = await refusal(content.get(claimsOf(run), mine.ref));
    expect(error).to.be.instanceOf(JournalPayloadMissingError);
    expect((error as JournalPayloadMissingError).reason).to.equal('shredded');
    expect(
      (await content.get(claimsOf(other), theirs.ref)).toString(),
    ).to.contain(SENTINEL);
    // A fresh replica has nothing cached and cannot read it either.
    const replica = new RunContentService(
      h.service,
      dataKeyRing({ store: keyStore }).cipher,
      payloads,
    );
    const again = await refusal(replica.get(claimsOf(run), mine.ref));
    expect((again.error as JournalPayloadMissingError).reason).to.equal(
      'shredded',
    );
  });

  it('CS-6: content over the limit is refused and nothing is stored', async () => {
    const run = await claimed();
    const { error } = await refusal(
      content.put(claimsOf(run), Buffer.alloc(CONTENT_MAX_BYTES + 1)),
    );
    expect(error).to.be.instanceOf(ContentTooLargeError);
    expect(payloads.objects.size).to.equal(0);
  });

  it('a store outage is retryable, never missing; a KEK that cannot unwrap too', async () => {
    const run = await claimed();
    const stored = await content.put(claimsOf(run), big());
    const down: PayloadStore = {
      put: () => Promise.reject(new PayloadStoreUnavailableError('put')),
      get: () => Promise.reject(new PayloadStoreUnavailableError('get')),
    };
    const outage = new RunContentService(
      h.service,
      ring.cipher,
      down,
      log.logger,
    );
    for (const work of [
      outage.put(claimsOf(run), big()),
      outage.get(claimsOf(run), stored.ref),
    ]) {
      expect((await refusal(work)).error).to.be.instanceOf(
        PayloadStoreUnavailableError,
      );
    }
    const other = dataKeyRing({
      store: keyStore,
      kek: testKek(Buffer.alloc(32, 9)),
    });
    const unwrap = await refusal(
      new RunContentService(h.service, other.cipher, payloads).get(
        claimsOf(run),
        stored.ref,
      ),
    );
    expect(unwrap.error).to.be.instanceOf(JournalContentUnavailableError);
  });

  it('CS-9: the projector never reads a sealed ref', () => {
    expect(
      inlineContent({
        ref: formatContentRef({ writerRunId: 'r', storeRef: 's' }),
      }),
    ).to.equal(null);
  });

  it('LG-5: content.sealed and content.opened at debug with size and ref, never content', async () => {
    const run = await claimed();
    const plaintext = big();
    const stored = await content.put(claimsOf(run), plaintext);
    await content.get(claimsOf(run), stored.ref);
    const lines = log.lines.filter(([, message]) =>
      String(message).startsWith('content.'),
    );
    expect(
      lines.map(([level, message]) => `${String(level)}:${String(message)}`),
    ).to.deep.equal(['debug:content.sealed', 'debug:content.opened']);
    expect(lines[0]![2]).to.include({
      orgId: run.orgId,
      runId: run.runId,
      attemptId: run.attemptId,
      ref: stored.ref,
      size: plaintext.length,
    });
    expect(lines[1]![2]).to.include({
      ref: stored.ref,
      size: plaintext.length,
    });
    expect(JSON.stringify(log.lines)).not.to.contain(SENTINEL);
  });
});
