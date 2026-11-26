/** LCFIX, GAPS-1: the person's content in a run's document and receipts is sealed with the org's data key. */
import { expect } from 'chai';
import { RunInputSeal } from '../../../src/modules/agent-harness/application/run-input-seal';
import {
  HarnessUnavailableError,
  JournalPayloadMissingError,
} from '../../../src/modules/agent-harness/domain/errors';
import type { RunInputSnapshot } from '../../../src/modules/agent-harness/domain/execution';
import { dataKeyRing } from './support/data-protection';

const QUERY = 'marker-A what is my salary band?';
const FULL: RunInputSnapshot = {
  query: QUERY,
  recordIds: ['rec-1'],
  filters: { kb: ['marker-B-kb'] },
  appliedFilters: { apps: ['marker-C-app'] },
  attachments: [{ recordId: 'att-1', recordName: 'marker-D payslip.pdf' }],
  caller: { email: 'marker-E@example.com', displayName: 'marker-F Person' },
  model: { chatMode: 'auto' },
};
const run = (input: RunInputSnapshot | null = { query: QUERY, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } }) => ({
  orgId: 'org-1',
  runId: 'run-1',
  input,
});

const rejection = async (work: Promise<unknown>): Promise<unknown> =>
  work.then(
    () => expect.fail('expected a rejection'),
    (error: unknown) => error,
  );

describe('RunInputSeal (LCFIX)', () => {
  it('seals the query out of the stored copy and opens it back, nothing else of the input changed', async () => {
    const seal = new RunInputSeal(dataKeyRing().cipher);
    const sealed = await seal.seal(run());
    expect(JSON.stringify(sealed)).not.to.include('marker-A');
    expect(sealed.input).not.to.have.property('query');
    expect(sealed.input).to.have.property('querySealed');
    expect(await seal.open(sealed)).to.deep.equal(run());
  });

  it('is idempotent: a sealed copy seals as it is, a plaintext one opens as it is', async () => {
    const seal = new RunInputSeal(dataKeyRing().cipher);
    const sealed = await seal.seal(run());
    expect(await seal.seal(sealed)).to.equal(sealed);
    expect(await seal.open(run())).to.deep.equal(run());
    expect(await seal.seal(run(null))).to.deep.equal(run(null));
  });

  it('a sealed query moved to another run does not open', async () => {
    const seal = new RunInputSeal(dataKeyRing().cipher);
    const sealed = await seal.seal(run());
    const error = await rejection(seal.open({ ...sealed, runId: 'run-2' }));
    expect(error).to.be.instanceOf(JournalPayloadMissingError);
  });

  it('without a data key nothing is sealed, and a sealed query cannot be opened', async () => {
    const sealed = await new RunInputSeal(dataKeyRing().cipher).seal(run());
    const none = new RunInputSeal(null);
    expect(await none.seal(run())).to.deep.equal(run());
    expect(await rejection(none.open(sealed))).to.be.instanceOf(HarnessUnavailableError);
  });
});

describe('RunInputSeal: attachments, filters and the caller (GAPS-1)', () => {
  it('seals every piece of the person content and opens the input back exactly', async () => {
    const seal = new RunInputSeal(dataKeyRing().cipher);
    const sealed = await seal.seal(run(FULL));
    const stored = JSON.stringify(sealed);
    for (const marker of ['marker-A', 'marker-B', 'marker-C', 'marker-D', 'marker-E', 'marker-F']) {
      expect(stored, marker).not.to.include(marker);
    }
    for (const field of ['query', 'filters', 'appliedFilters', 'attachments', 'caller']) {
      expect(sealed.input, field).not.to.have.property(field);
    }
    expect(sealed.input).to.include.keys('querySealed', 'detailsSealed');
    // What admission and the claim filter read without the key stays as it was.
    expect(sealed.input).to.deep.include({ recordIds: ['rec-1'], model: { chatMode: 'auto' } });
    expect(await seal.open(sealed)).to.deep.equal(run(FULL));
  });

  it('a row whose query alone was sealed (LCFIX) seals its details and opens whole', async () => {
    const seal = new RunInputSeal(dataKeyRing().cipher);
    const { query, ...rest } = FULL;
    const { querySealed } = await seal.sealInput('org-1', { runId: 'run-1' }, { recordIds: [], model: FULL.model, query });
    const resealed = await seal.seal({ ...run(FULL), input: { ...rest, querySealed } } as never);
    expect(JSON.stringify(resealed)).not.to.match(/marker-[A-F]/);
    expect(await seal.open(resealed)).to.deep.equal(run(FULL));
  });

  it('sealed details moved to another run or swapped into the query do not open', async () => {
    const seal = new RunInputSeal(dataKeyRing().cipher);
    const sealed = (await seal.seal(run(FULL))) as unknown as { input: Record<string, unknown> };
    expect(await rejection(seal.open({ ...sealed, orgId: 'org-1', runId: 'run-2' } as never))).to.be.instanceOf(JournalPayloadMissingError);
    const swapped = { orgId: 'org-1', runId: 'run-1', input: { ...sealed.input, querySealed: sealed.input['detailsSealed'] } };
    expect(await rejection(seal.open(swapped as never))).to.be.instanceOf(JournalPayloadMissingError);
  });

  it('a schedule template seals bound to its schedule, not to a run', async () => {
    const seal = new RunInputSeal(dataKeyRing().cipher);
    const sealed = await seal.sealInput('org-1', { scheduleId: 'sch-1' }, FULL);
    expect(JSON.stringify(sealed)).not.to.match(/marker-[A-F]/);
    expect(await seal.openInput('org-1', { scheduleId: 'sch-1' }, sealed)).to.deep.equal(FULL);
    expect(await rejection(seal.openInput('org-1', { scheduleId: 'sch-2' }, sealed))).to.be.instanceOf(JournalPayloadMissingError);
    expect(await rejection(seal.openInput('org-1', { runId: 'sch-1' }, sealed))).to.be.instanceOf(JournalPayloadMissingError);
  });

  it('withoutSealed drops the sealed fields a worker never opens', async () => {
    const seal = new RunInputSeal(dataKeyRing().cipher);
    const sealed = await seal.seal(run(FULL));
    const bare = RunInputSeal.withoutSealed(sealed);
    expect(bare.input).not.to.have.any.keys('querySealed', 'detailsSealed');
    expect(bare.input).to.deep.include({ recordIds: ['rec-1'] });
    expect(RunInputSeal.withoutSealed(run(null))).to.deep.equal(run(null));
  });
});

describe('RunInputSeal: an inline history (GAPS2-1)', () => {
  const HISTORY = [
    { role: 'user_query', content: 'marker-G how many leave days?' },
    { role: 'bot_response', content: 'marker-H twenty-five' },
  ];
  const WITH_HISTORY: RunInputSnapshot = { ...FULL, previousConversations: HISTORY };

  it('seals the history the run document holds when it is not kept by reference, and opens it back exactly', async () => {
    const seal = new RunInputSeal(dataKeyRing().cipher);
    const sealed = await seal.seal(run(WITH_HISTORY));
    expect(JSON.stringify(sealed)).not.to.match(/marker-[A-H]/);
    expect(sealed.input).not.to.have.property('previousConversations');
    expect(sealed.input).to.include.keys('querySealed', 'detailsSealed', 'historySealed');
    expect(await seal.open(sealed)).to.deep.equal(run(WITH_HISTORY));
  });

  it("a first turn's empty history is left as it is: nothing to hide, no seal to pay", async () => {
    const seal = new RunInputSeal(dataKeyRing().cipher);
    const sealed = await seal.seal(run({ ...FULL, previousConversations: [] }));
    expect(sealed.input).to.deep.include({ previousConversations: [] });
    expect(sealed.input).not.to.have.property('historySealed');
    expect((await seal.open(sealed)).input?.previousConversations).to.deep.equal([]);
    // A sealed row whose only plaintext left is an empty history has nothing to seal: the same object back.
    const rest = { recordIds: [], model: { chatMode: 'auto' }, previousConversations: [] };
    expect(await seal.sealInput('org-1', { runId: 'run-1' }, rest)).to.equal(rest);
  });

  it('a row GAPS-1 sealed keeps its sealed query and details as they are and gains the sealed history', async () => {
    const seal = new RunInputSeal(dataKeyRing().cipher);
    const gaps1 = (await seal.seal(run(FULL))).input as unknown as Record<string, unknown>;
    const resealed = (await seal.sealInput('org-1', { runId: 'run-1' }, {
      ...gaps1,
      previousConversations: HISTORY,
    } as never)) as unknown as Record<string, unknown>;
    expect(resealed['detailsSealed']).to.equal(gaps1['detailsSealed']);
    expect(resealed['querySealed']).to.equal(gaps1['querySealed']);
    expect(JSON.stringify(resealed)).not.to.match(/marker-[GH]/);
    expect(await seal.open({ ...run(), input: resealed } as never)).to.deep.equal(run(WITH_HISTORY));
  });

  it('a sealed history moved to another run or swapped into the details does not open', async () => {
    const seal = new RunInputSeal(dataKeyRing().cipher);
    const sealed = (await seal.seal(run(WITH_HISTORY))) as unknown as { input: Record<string, unknown> };
    expect(await rejection(seal.open({ ...sealed, orgId: 'org-1', runId: 'run-2' } as never))).to.be.instanceOf(JournalPayloadMissingError);
    const swapped = { orgId: 'org-1', runId: 'run-1', input: { ...sealed.input, detailsSealed: sealed.input['historySealed'] } };
    expect(await rejection(seal.open(swapped as never))).to.be.instanceOf(JournalPayloadMissingError);
  });

  it('withoutSealed drops the sealed history too', async () => {
    const seal = new RunInputSeal(dataKeyRing().cipher);
    const bare = RunInputSeal.withoutSealed(await seal.seal(run(WITH_HISTORY)));
    expect(bare.input).not.to.have.any.keys('querySealed', 'detailsSealed', 'historySealed');
  });
});
