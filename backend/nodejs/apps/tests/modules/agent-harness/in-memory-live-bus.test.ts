import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryLiveBus } from '../../../src/modules/agent-harness/adapters/in-memory-live-bus';
import { LiveDelta } from '../../../src/modules/agent-harness/projection/frames';

const delta = (n: number): LiveDelta =>
  ({
    runId: 'run-1',
    fence: 1,
    modelAttempt: 1,
    n,
    kind: 'text_delta',
    data: { text: String(n) },
  }) as LiveDelta;

describe('InMemoryLiveBus', () => {
  it('delivers to a follower until it closes', () => {
    const bus = new InMemoryLiveBus();
    const seen: number[] = [];
    const subscription = bus.follow('run-1', '0', (deltas) => seen.push(...deltas.map((d) => d.n)), () => undefined);
    bus.publish(delta(1));
    subscription.close();
    bus.publish(delta(2));
    expect(seen).to.deep.equal([1]);
  });

  it('a failing bus reports the failure after follow returns, and its subscription closes safely', async () => {
    const bus = new InMemoryLiveBus();
    const failure = new Error('redis down');
    bus.failing = failure;
    const onError = sinon.spy();
    const onDeltas = sinon.spy();
    const subscription = bus.follow('run-1', '0', onDeltas, onError);
    // The caller has its handle before it hears of the failure.
    expect(onError.called).to.equal(false);
    await Promise.resolve();
    expect(onError.calledOnceWithExactly(failure)).to.equal(true);
    expect(() => subscription.close()).to.not.throw();
    bus.failing = null;
    bus.publish(delta(1));
    expect(onDeltas.called).to.equal(false);
  });
});
