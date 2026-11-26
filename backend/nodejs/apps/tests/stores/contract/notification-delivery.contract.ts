/**
 * The notification consumer's writes on a store: what one delivery stores, what it takes back, and what an event
 * delivered again leaves behind. Store-neutral: the store under test supplies its notification repository (one that
 * reads the organisation from the context the consumer binds) and a raw read of the stored rows.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { NotificationConsumer } from '../../../src/modules/notification/service/notification.consumer';
import { NotificationService } from '../../../src/modules/notification/service/notification.service';
import type { INotificationDeliveryPolicy } from '../../../src/modules/notification/service/notification-delivery.port';
import * as RecipientResolver from '../../../src/modules/notification/utils/notification-recipient.resolver';
import {
  setNotificationRepository,
  type NotificationRepository,
} from '../../../src/libs/repositories/notification';

export interface DeliveryFixture {
  repo: NotificationRepository;
  newId(): string;
  /** Every notification the user has in the org, deleted ones included. */
  stored(orgId: string, userId: string): Promise<{ type: string; dedupeKey?: string }[]>;
}

export function describeNotificationDelivery(fx: DeliveryFixture): void {
  let pushedTo: sinon.SinonStub;
  let emailed: sinon.SinonStub;
  let deliver: (value: string, policy?: INotificationDeliveryPolicy) => Promise<void>;

  beforeEach(() => {
    setNotificationRepository(fx.repo);
    sinon
      .stub(RecipientResolver, 'resolveNotificationRecipientUserIds')
      .callsFake(async (_org, ids) => (Array.isArray(ids) ? ids.map(String) : []));
    const service = sinon.createStubInstance(NotificationService);
    pushedTo = service.sendToUser as unknown as sinon.SinonStub;
    emailed = sinon.stub().resolves();
    const logger = { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() };
    deliver = async (value, policy) => {
      const broker = { isConnected: () => true, consume: sinon.stub().resolves() };
      const consumer = new NotificationConsumer(
        broker as never,
        logger as never,
        service as unknown as NotificationService,
        undefined,
        { dispatch: emailed },
        policy,
      );
      await consumer.consume(async () => undefined);
      await broker.consume.firstCall.args[0]({ value });
    };
  });
  afterEach(() => {
    sinon.restore();
    setNotificationRepository();
  });

  const event = (orgId: string, userIds: string[], dedupeKey: string, extra: Record<string, unknown> = {}): string =>
    JSON.stringify({
      orgId,
      type: 'test.shared',
      recipientUserIds: userIds,
      title: 'Shared',
      message: 'Something was shared with you',
      dedupeKey,
      emailIntent: { template: 'test' },
      payload: { sessionId: 's1' },
      ...extra,
    });
  const pushed = (): string[] => pushedTo.args.map((args) => String(args[0]));

  it('stores one notice per recipient; the same event delivered again stores, pushes and emails nothing', async () => {
    const org = fx.newId();
    const [a, b] = [fx.newId(), fx.newId()];
    await deliver(event(org, [a, b], 'k:1'));
    expect(pushed()).to.have.members([a, b]);
    expect(emailed.args.map(([e]) => (e as { dedupeKey?: string }).dedupeKey)).to.deep.equal(['k:1', 'k:1']);
    await deliver(event(org, [a, b], 'k:1'));
    expect(pushed()).to.have.length(2);
    expect(emailed.callCount).to.equal(2);
    expect(await fx.stored(org, a)).to.deep.equal([{ type: 'test.shared', dedupeKey: 'k:1' }]);
    expect(await fx.stored(org, b)).to.have.length(1);
  });

  it('tells only the recipients who do not have the key yet', async () => {
    const org = fx.newId();
    const [a, b] = [fx.newId(), fx.newId()];
    await deliver(event(org, [a], 'k:2'));
    await deliver(event(org, [a, b], 'k:2'));
    expect(pushed()).to.deep.equal([a, b]);
    expect(await fx.stored(org, a)).to.have.length(1);
  });

  it('stores a scheduled notice without pushing or emailing it; the same event delivered again stores nothing', async () => {
    const org = fx.newId();
    const me = fx.newId();
    const scheduled = event(org, [me], 'k:4', { visibleAt: new Date(Date.now() + 3_600_000).toISOString() });
    await deliver(scheduled);
    expect(pushed()).to.deep.equal([]);
    expect(emailed.called).to.equal(false);
    expect(await fx.stored(org, me)).to.deep.equal([{ type: 'test.shared', dedupeKey: 'k:4' }]);
    await deliver(scheduled);
    expect(await fx.stored(org, me)).to.have.length(1);
  });

  it('takes back a notice its recipient may no longer have, which frees its key for a later delivery', async () => {
    const org = fx.newId();
    const me = fx.newId();
    const nobody: INotificationDeliveryPolicy = {
      plan: async (_event, base) => ({ userIds: base, stillFor: async () => new Set<string>() }),
    };
    await deliver(event(org, [me], 'k:3'), nobody);
    expect(pushed()).to.deep.equal([]);
    expect(await fx.stored(org, me)).to.deep.equal([]);
    await deliver(event(org, [me], 'k:3'));
    expect(pushed()).to.deep.equal([me]);
    expect(await fx.stored(org, me)).to.have.length(1);
  });
}
