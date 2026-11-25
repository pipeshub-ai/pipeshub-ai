/** PERF-C: `CoalescedWork`, the turn-taking behind the audit chain's linking (PERFC-22, PERFC-24). */
import { expect } from 'chai';
import { CoalescedWork } from '../../../src/modules/agent-harness/application/coalesced-work';

interface Run {
  key: string;
  end: () => void;
  fail: (error: Error) => void;
}

/** Work whose every run stays open until the test ends it. */
const held = () => {
  const runs: Run[] = [];
  const work = new CoalescedWork(
    (key) =>
      new Promise<void>((resolve, reject) => {
        runs.push({ key, end: resolve, fail: reject });
      }),
  );
  return { runs, work };
};

const turn = () => new Promise<void>((resolve) => setImmediate(resolve));

const settled = async (promise: Promise<void>): Promise<boolean> => {
  let done = false;
  promise.then(
    () => (done = true),
    () => (done = true),
  );
  await turn();
  return done;
};

describe('CoalescedWork', () => {
  it('CW-1: a request runs the work for its key and settles when the run ends', async () => {
    const { runs, work } = held();
    const asked = work.request('org-1');
    await turn();
    expect(runs.map((run) => run.key)).to.deep.equal(['org-1']);
    expect(await settled(asked)).to.equal(false);
    runs[0]?.end();
    await asked;
  });

  it('CW-2: requests made before a run started share it', async () => {
    const { runs, work } = held();
    const asked = [work.request('org-1'), work.request('org-1'), work.request('org-1')];
    await turn();
    expect(runs).to.have.length(1);
    runs[0]?.end();
    await Promise.all(asked);
    expect(runs).to.have.length(1);
  });

  it('CW-3: a request made during a run waits for the next run, which everyone asking meanwhile shares', async () => {
    const { runs, work } = held();
    const first = work.request('org-1');
    await turn();
    const during = [work.request('org-1'), work.request('org-1')];
    await turn();
    expect(runs, 'one run at a time').to.have.length(1);
    runs[0]?.end();
    await first;
    expect(await settled(during[0] as Promise<void>), 'the run under way is not the answer').to.equal(false);
    expect(runs).to.have.length(2);
    runs[1]?.end();
    await Promise.all(during);
    expect(runs).to.have.length(2);
  });

  it('CW-4: keys do not wait for each other', async () => {
    const { runs, work } = held();
    const one = work.request('org-1');
    const two = work.request('org-2');
    await turn();
    expect(runs.map((run) => run.key).sort()).to.deep.equal(['org-1', 'org-2']);
    runs.find((run) => run.key === 'org-2')?.end();
    await two;
    expect(await settled(one)).to.equal(false);
    runs.find((run) => run.key === 'org-1')?.end();
    await one;
  });

  it('CW-5: a failed run fails those who shared it; the next request runs again', async () => {
    const { runs, work } = held();
    const failing = work.request('org-1');
    await turn();
    const next = work.request('org-1');
    runs[0]?.fail(new Error('store down'));
    expect(await failing.then(() => null, (error: Error) => error.message)).to.equal('store down');
    await turn();
    expect(runs).to.have.length(2);
    runs[1]?.end();
    await next;
    const later = work.request('org-1');
    await turn();
    runs[2]?.end();
    await later;
    expect(runs).to.have.length(3);
  });

  it('CW-6: idle settles once nothing runs or waits, whatever the outcomes', async () => {
    const { runs, work } = held();
    await work.idle();
    const first = work.request('org-1').catch(() => undefined);
    await turn();
    const second = work.request('org-1');
    const idle = work.idle();
    expect(await settled(idle)).to.equal(false);
    runs[0]?.fail(new Error('store down'));
    await first;
    await turn();
    expect(await settled(idle), 'a run is still queued').to.equal(false);
    runs[1]?.end();
    await second;
    await idle;
  });
});
