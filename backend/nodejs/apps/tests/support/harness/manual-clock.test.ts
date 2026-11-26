import { expect } from 'chai';
import fc from 'fast-check';
import { ManualClock } from './manual-clock';
import { propertyRuns } from './property-profile';

describe('harness test kit: ManualClock', () => {
  it('K5: fires due timers in deadline order, ties in scheduling order', () => {
    const clock = new ManualClock();
    const seen: Array<[string, number]> = [];
    clock.callLater(3000, () => seen.push(['c', clock.monotonic()]));
    clock.callLater(1000, () => seen.push(['a', clock.monotonic()]));
    clock.callLater(1000, () => seen.push(['b', clock.monotonic()]));
    clock.callLater(10000, () => seen.push(['late', clock.monotonic()]));

    expect(clock.advance(500)).to.equal(0);
    expect(clock.advance(2500)).to.equal(3);
    expect(seen).to.deep.equal([
      ['a', 1000],
      ['b', 1000],
      ['c', 3000],
    ]);
    expect(clock.pendingTimers()).to.equal(1);
  });

  it('cancelled timers do not fire; nested timers fire within the same advance', () => {
    const clock = new ManualClock();
    const fired: number[] = [];
    clock.callLater(100, () => fired.push(-1)).cancel();
    clock.callLater(100, () =>
      clock.callLater(100, () => fired.push(clock.monotonic())),
    );
    clock.advance(200);
    expect(fired).to.deep.equal([200]);
  });

  it('sleep resolves only when advanced past the delay', async () => {
    const clock = new ManualClock();
    let done = false;
    const sleeping = clock.sleep(2000).then(() => {
      done = true;
    });
    clock.advance(1999);
    await Promise.resolve();
    expect(done).to.equal(false);
    clock.advance(1);
    await sleeping;
    expect(done).to.equal(true);
  });

  it('suspend moves wall and suspend-aware time but not monotonic', () => {
    const clock = new ManualClock();
    const start = clock.now().getTime();
    let leaseExpired = false;
    clock.callLater(30000, () => {
      leaseExpired = true;
    });
    expect(clock.suspend(60000)).to.equal(1);
    expect(leaseExpired).to.equal(true);
    expect(clock.monotonic()).to.equal(0);
    expect(clock.suspendAware()).to.equal(60000);
    expect(clock.now().getTime() - start).to.equal(60000);
  });

  it('rejects time moving backwards', () => {
    expect(() => new ManualClock().advance(-1)).to.throw(RangeError);
  });

  it('firing order is deterministic for any timers and steps (property)', () => {
    fc.assert(
      fc.property(
        fc.array(fc.nat(100000), { minLength: 1, maxLength: 20 }),
        fc.array(fc.nat(30000), { minLength: 1, maxLength: 10 }),
        (delays, steps) => {
          const run = (): number[] => {
            const clock = new ManualClock();
            const order: number[] = [];
            delays.forEach((d, i) => clock.callLater(d, () => order.push(i)));
            steps.forEach((s) => clock.advance(s));
            return order;
          };
          const order = run();
          expect(run()).to.deep.equal(order);
          const expected = [...order].sort(
            (a, b) => delays[a]! - delays[b]! || a - b,
          );
          expect(order).to.deep.equal(expected);
        },
      ),
      propertyRuns(100),
    );
  });
});
