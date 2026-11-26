import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { NotificationConsumer } from '../../../../src/modules/notification/service/notification.consumer';
import { NotificationProducer, EventType } from '../../../../src/modules/notification/service/notification.producer';
import { NotificationService } from '../../../../src/modules/notification/service/notification.service';
import { setNotificationRepository } from '../../../../src/libs/repositories/notification';
import { getCurrentOrgId, forOrg } from '../../../../src/libs/context/orgContext';
import {
  configureEnvelope,
  deriveKey,
  runWithEnvelope,
  signMessage,
} from '../../../../src/libs/context/envelope';
import * as RecipientResolver from '../../../../src/modules/notification/utils/notification-recipient.resolver';
import { notificationOf } from '../../../../src/modules/notification/utils/notification-payload.resolver';

// With ENVELOPE_SIGN on, a notification's signature must cover everything the consumer acts on: its org, its
// recipients and its text. A writer to the broker who replays a signed message with changed top-level fields must not
// reach another org, nor change what the signed org's users are told.
describe('notification/service/notification.signed', () => {
  const id = () => new mongoose.Types.ObjectId().toString();
  const orgA = id();
  const orgB = id();
  const userA = id();
  const userB = id();
  let savedSign: string | undefined;
  let consumer: NotificationConsumer;
  let broker: any;
  let stored: Array<{ org: string | undefined; doc: Record<string, unknown> }>;
  let logger: any;

  // The broker serialises a published value as Redis Streams does: signed in the publisher's org context.
  const wire: Array<Record<string, unknown>> = [];
  const pubBroker = {
    isConnected: () => true,
    connect: async () => {},
    publish: async (topic: string, m: { value: Record<string, unknown> }) => {
      wire.push(JSON.parse(JSON.stringify(signMessage(topic, { ...m.value }))));
    },
  };

  /** One delivery, behind the envelope check the Redis Streams consumer runs. */
  const deliver = async (value: Record<string, unknown>) => {
    const wrapped = broker.consume.firstCall.args[0];
    await runWithEnvelope('notification', value, (v) => wrapped({ value: v }), 'notification-consumer-group');
  };

  beforeEach(async () => {
    savedSign = process.env.ENVELOPE_SIGN;
    process.env.ENVELOPE_SIGN = 'true';
    const key = deriveKey('notification-signed-test');
    configureEnvelope(async () => [key], null, key);
    wire.length = 0;
    stored = [];
    logger = { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() };
    broker = {
      connect: sinon.stub().resolves(),
      disconnect: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(true),
      subscribe: sinon.stub().resolves(),
      consume: sinon.stub().resolves(),
    };
    sinon.stub(RecipientResolver, 'resolveNotificationRecipientUserIds').callsFake(async (_org: string, ids?: string[]) =>
      (ids ?? []).map((u) => new mongoose.Types.ObjectId(u)),
    );
    setNotificationRepository({
      insertMany: sinon.stub().callsFake(async (docs: Array<Record<string, unknown>>) => {
        for (const doc of docs) stored.push({ org: getCurrentOrgId(), doc: { ...doc, assignedTo: String(doc.assignedTo) } });
        return docs.map((d, i) => ({ ...d, _id: `n${i}` }));
      }),
    } as never);
    const service = { sendToUser: sinon.stub() } as unknown as NotificationService;
    consumer = new NotificationConsumer(broker, logger, service);
    await consumer.consume(sinon.stub().resolves());
  });

  afterEach(() => {
    configureEnvelope(null);
    setNotificationRepository();
    sinon.restore();
    if (savedSign === undefined) delete process.env.ENVELOPE_SIGN;
    else process.env.ENVELOPE_SIGN = savedSign;
  });

  const notification = (org: string, user: string, title = 'Sync failed') => ({
    orgId: org,
    type: 'CONNECTOR_SYNC_ERROR',
    title,
    message: 'The connector stopped',
    recipientUserIds: [user],
    payload: { connectorId: 'c1' },
  });

  const publish = (org: string, doc: Record<string, unknown>) =>
    forOrg(org, 'test', () =>
      new NotificationProducer(pubBroker as never, logger).publishEvent({
        eventType: EventType.NewNotificationEvent,
        timestamp: Date.now(),
        payload: doc,
      }),
    );

  it('signs the whole notification and delivers it, as signed, in its org', async () => {
    await publish(orgA, notification(orgA, userA));

    expect(wire).to.have.length(1);
    expect((wire[0].ctx as { orgId?: string }).orgId).to.equal(orgA);
    expect(wire[0]).to.not.have.property('title');
    await deliver(wire[0]);

    expect(stored).to.have.length(1);
    expect(stored[0].org).to.equal(orgA);
    expect(stored[0].doc).to.include({ orgId: orgA, title: 'Sync failed', assignedTo: userA });
    expect(stored[0].doc.payload).to.deep.equal({ connectorId: 'c1' });
  });

  it('ignores top-level fields added to a signed message: no other org, no other recipient, no other text', async () => {
    await publish(orgA, notification(orgA, userA));
    const forged = { ...wire[0], orgId: orgB, type: 'X', title: 'Forged', recipientUserIds: [userB] };

    await deliver(forged);

    expect(stored).to.have.length(1);
    expect(stored[0].org).to.equal(orgA);
    expect(stored[0].doc).to.include({ orgId: orgA, title: 'Sync failed', assignedTo: userA });
    expect(stored.some((s) => s.org === orgB || s.doc.assignedTo === userB)).to.equal(false);
  });

  it('refuses a signed message in the older shape relabelled to another org, before anything is stored', async () => {
    // An older producer: the notification at the top level, only its details under payload (that is what was signed).
    const legacy = forOrg(orgA, 'test', () =>
      JSON.parse(JSON.stringify(signMessage('notification', { ...notification(orgA, userA), payload: { orgId: orgA } }))),
    );
    const relabelled = { ...legacy, orgId: orgB, recipientUserIds: [userB] };

    await deliver(relabelled);

    expect(stored, 'nothing is written in either org').to.have.length(0);
  });

  it('counts a signed message in the older shape and holds it to its signed org', async () => {
    const legacy = forOrg(orgA, 'test', () =>
      JSON.parse(JSON.stringify(signMessage('notification', { ...notification(orgA, userA), payload: { orgId: orgA } }))),
    );

    await deliver(legacy);

    expect(stored).to.have.length(1);
    expect(stored[0].org).to.equal(orgA);
  });

  it('a payload whose details hold type and orgId is still read in the older shape', () => {
    const raw = { ...notification(orgA, userA), payload: { type: 'details', orgId: orgA } };
    expect(notificationOf(raw).value).to.equal(raw);
  });

  it('with signing off the producer sends the notification as before, and the consumer still reads it', async () => {
    process.env.ENVELOPE_SIGN = 'false';
    const doc = notification(orgA, userA);
    await publish(orgA, doc);

    expect(wire[0]).to.include({ orgId: orgA, title: 'Sync failed' });
    expect(wire[0]).to.not.have.property('ctx');
  });

  it('reads the signed shape from an unsigned message too (a producer that could not sign)', async () => {
    process.env.ENVELOPE_SIGN = 'false';
    await deliver({ payload: { notification: notification(orgA, userA), orgId: orgA } });

    expect(stored).to.have.length(1);
    expect(stored[0].doc).to.include({ orgId: orgA, assignedTo: userA });
  });
});
