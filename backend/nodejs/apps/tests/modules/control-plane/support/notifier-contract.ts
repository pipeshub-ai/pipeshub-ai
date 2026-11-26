/**
 * CN: what every `ChangeNotifier` does (03-interfaces §5.4); CPD-6 runs it on
 * `BrokerChangeNotifier` over Kafka and Redis Streams. Python runs the same
 * cases (`app.control_plane.testing.contracts.ChangeNotifierContract`).
 */
import { expect } from 'chai';
import type { ChangeEvent } from '../../../../src/modules/control-plane';
import type { ChangeNotifier } from '../../../../src/modules/control-plane/ports/change-notifier';
import { ORG_A, ORG_B } from './cp-objects';

export interface NotifierWorld {
  readonly notifier: ChangeNotifier;
  /** Resolves once every published event has reached every subscriber (a broker's consumer catch-up; nothing in memory). */
  settled(): Promise<void>;
}

export const changeEvent = (orgId: string, revision: number): ChangeEvent => ({
  type: 'cp.changed',
  orgId,
  revision,
  previous: revision - 1,
  touched: [{ kind: 'grant', id: 'agent:a7' }],
  narrowing: false,
});

const seen = (events: readonly ChangeEvent[]) => events.map((event) => `${event.orgId}@${event.revision}`);

export function describeChangeNotifierContract(name: string, make: () => Promise<NotifierWorld>): void {
  describe(`ChangeNotifier contract (CN): ${name}`, () => {
    it('CN-1: every subscriber gets every event, in publish order per org', async () => {
      const world = await make();
      const first: ChangeEvent[] = [];
      const second: ChangeEvent[] = [];
      world.notifier.subscribe((event) => first.push(event));
      world.notifier.subscribe((event) => second.push(event));
      const published = [changeEvent(ORG_A, 1), changeEvent(ORG_B, 7), changeEvent(ORG_A, 2), changeEvent(ORG_A, 3), changeEvent(ORG_B, 8)];
      for (const event of published) await world.notifier.publish(event);
      await world.settled();
      for (const events of [first, second]) {
        expect(seen(events.filter((e) => e.orgId === ORG_A))).to.deep.equal([`${ORG_A}@1`, `${ORG_A}@2`, `${ORG_A}@3`]);
        expect(seen(events.filter((e) => e.orgId === ORG_B))).to.deep.equal([`${ORG_B}@7`, `${ORG_B}@8`]);
      }
      expect(first[0]).to.deep.equal(published[0]);
    });

    it('CN-2: an unsubscribed handler gets nothing more', async () => {
      const world = await make();
      const got: ChangeEvent[] = [];
      const unsubscribe = world.notifier.subscribe((event) => got.push(event));
      await world.notifier.publish(changeEvent(ORG_A, 1));
      await world.settled();
      unsubscribe();
      await world.notifier.publish(changeEvent(ORG_A, 2));
      await world.settled();
      expect(seen(got)).to.deep.equal([`${ORG_A}@1`]);
    });

    it('CN-3: a subscriber that starts late is not replayed what it missed', async () => {
      const world = await make();
      await world.notifier.publish(changeEvent(ORG_A, 1));
      await world.settled();
      const late: ChangeEvent[] = [];
      world.notifier.subscribe((event) => late.push(event));
      await world.notifier.publish(changeEvent(ORG_A, 2));
      await world.settled();
      expect(seen(late)).to.deep.equal([`${ORG_A}@2`]);
    });
  });
}
