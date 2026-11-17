import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { randomUUID } from 'crypto';
import {
  MAX_ALERTS_PER_TYPE_PER_HOUR,
  MAX_ALERTS_PER_TYPE_PER_HOUR_ALL_SCOPES,
  MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR,
  MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR_ALL_SCOPES,
  OTHER_IDS_SCOPE,
  PlatformAlertHandler,
  budgetScope,
} from '../../../../src/modules/notification/service/platform-alert.handler';
import { NotificationService } from '../../../../src/modules/notification/service/notification.service';
import { setNotificationRepository } from '../../../../src/libs/repositories/notification';
import { setUserRepository } from '../../../../src/libs/repositories/user';
import { DocStoreReadOnlyError } from '../../../../src/libs/repositories/errors';
import { parseSandboxCapacityAlert } from '../../../../src/modules/notification/utils/sandbox-capacity-alert';

const id = () => new mongoose.Types.ObjectId().toString();
const HOUR = 3600_000;

describe('platform alerts: rate budgets are only used by delivered alerts', () => {
  const orgA = id();
  let admins: string[];
  let logger: any;
  let mailProducer: { publishEvent: sinon.SinonStub };
  let resolveRecipients: sinon.SinonStub;
  let insertMany: sinon.SinonStub;
  let handler: PlatformAlertHandler;

  const event = (
    payload: Record<string, unknown> = {},
    overrides: Record<string, unknown> = {},
  ) => ({
    audience: 'platformAdmins',
    type: 'SANDBOX_CAPACITY_CRITICAL',
    email: true,
    payload: {
      v: 1,
      alertId: randomUUID(),
      level: 'critical',
      firedAt: new Date().toISOString(),
      host: 'pipeshub-real',
      reason: 'slots',
      ...payload,
    },
    ...overrides,
  });
  const delivered = (alertId: string) =>
    insertMany.getCalls().some((c) => c.args[0][0].payload.alertId === alertId);

  beforeEach(() => {
    admins = [id(), id()];
    logger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() };
    mailProducer = { publishEvent: sinon.stub().resolves() };
    resolveRecipients = sinon.stub().callsFake(async () => [{ orgId: orgA, userIds: admins }]);
    insertMany = sinon.stub().callsFake(async (docs: any[]) => docs.map((doc, i) => ({ ...doc, _id: `n${i}` })));
    setNotificationRepository({ insertMany } as never);
    setUserRepository({
      listLiveByIds: sinon.stub().callsFake(async (_org: string, ids: string[]) =>
        ids.map((userId) => ({ _id: userId, email: `${userId}@example.com` })),
      ),
      serviceAccountIdsAmong: sinon.stub().resolves([]),
    } as never);
    handler = new PlatformAlertHandler(
      logger,
      sinon.createStubInstance(NotificationService) as unknown as NotificationService,
      mailProducer as never,
      resolveRecipients,
    );
  });

  afterEach(() => {
    setNotificationRepository();
    setUserRepository();
    sinon.restore();
  });

  describe('failed deliveries', () => {
    it('a store error that the broker redelivers does not use up the hour', async () => {
      const same = event();
      insertMany.rejects(new DocStoreReadOnlyError());
      for (let i = 0; i < MAX_ALERTS_PER_TYPE_PER_HOUR + 5; i += 1) {
        try {
          await handler.handle(same);
        } catch {
          // the broker would redeliver
        }
      }
      expect(insertMany.callCount).to.equal(MAX_ALERTS_PER_TYPE_PER_HOUR + 5);
      insertMany.resetBehavior();
      insertMany.callsFake(async (docs: any[]) => docs);
      const real = event();
      await handler.handle(real);
      expect(delivered(real.payload.alertId)).to.be.true;
    });

    it('an empty recipient list does not use up the hour', async () => {
      resolveRecipients.resolves([]);
      for (let i = 0; i < MAX_ALERTS_PER_TYPE_PER_HOUR; i += 1) await handler.handle(event());
      resolveRecipients.callsFake(async () => [{ orgId: orgA, userIds: admins }]);
      const real = event();
      await handler.handle(real);
      expect(delivered(real.payload.alertId)).to.be.true;
    });

    it('an org without recipients is not remembered as delivered', async () => {
      const same = event();
      resolveRecipients.resolves([{ orgId: orgA, userIds: [] }]);
      await handler.handle(same);
      resolveRecipients.callsFake(async () => [{ orgId: orgA, userIds: admins }]);
      await handler.handle(same);
      expect(delivered(same.payload.alertId)).to.be.true;
    });

    it('an email that could not be queued does not use up the email cap', async () => {
      mailProducer.publishEvent.rejects(new Error('broker down'));
      for (let i = 0; i < MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR + 2; i += 1) await handler.handle(event());
      mailProducer.publishEvent.resetBehavior();
      mailProducer.publishEvent.resolves();
      const before = mailProducer.publishEvent.callCount;
      await handler.handle(event());
      expect(mailProducer.publishEvent.callCount).to.equal(before + admins.length);
    });
  });

  describe('invalid events', () => {
    it('take no token', async () => {
      for (let i = 0; i < MAX_ALERTS_PER_TYPE_PER_HOUR + 5; i += 1) {
        await handler.handle(event({ level: 'warning' })); // level does not match the type
        await handler.handle(event({ firedAt: new Date(Date.now() - 30 * HOUR).toISOString() })); // expired
      }
      const real = event();
      await handler.handle(real);
      expect(delivered(real.payload.alertId)).to.be.true;
    });
  });

  describe('scopes', () => {
    it('events with ids not in the publisher format do not starve a real alert', async () => {
      for (let i = 0; i < MAX_ALERTS_PER_TYPE_PER_HOUR + 5; i += 1) {
        await handler.handle(event({ alertId: `forged-${i}` }));
      }
      const real = event();
      await handler.handle(real);
      expect(delivered(real.payload.alertId)).to.be.true;
      expect(insertMany.callCount).to.equal(MAX_ALERTS_PER_TYPE_PER_HOUR + 1);
    });

    it('events naming another host do not starve this host', async () => {
      for (let i = 0; i < MAX_ALERTS_PER_TYPE_PER_HOUR + 5; i += 1) {
        await handler.handle(event({ host: 'pipeshub-other' }));
      }
      const real = event();
      await handler.handle(real);
      expect(delivered(real.payload.alertId)).to.be.true;
    });

    it('forged emails from other ids leave the real email', async () => {
      for (let i = 0; i < MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR + 2; i += 1) {
        await handler.handle(event({ alertId: `forged-${i}` }));
      }
      const before = mailProducer.publishEvent.callCount;
      await handler.handle(event());
      expect(mailProducer.publishEvent.callCount).to.equal(before + admins.length);
    });

    it('still caps a type across all scopes', async () => {
      for (let i = 0; i < MAX_ALERTS_PER_TYPE_PER_HOUR_ALL_SCOPES + 5; i += 1) {
        await handler.handle(event({ host: `host-${i}` }, { email: false }));
      }
      expect(insertMany.callCount).to.equal(MAX_ALERTS_PER_TYPE_PER_HOUR_ALL_SCOPES);
    });

    it('still caps a level of email across all scopes', async () => {
      for (let i = 0; i < MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR_ALL_SCOPES + 5; i += 1) {
        await handler.handle(event({ host: `host-${i}` }));
      }
      expect(mailProducer.publishEvent.callCount).to.equal(
        MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR_ALL_SCOPES * admins.length,
      );
    });

    it('keeps each level and type on its own budget', async () => {
      for (let i = 0; i < MAX_ALERTS_PER_TYPE_PER_HOUR + 2; i += 1) await handler.handle(event({}, { email: false }));
      const warning = event({ level: 'warning' }, { type: 'SANDBOX_CAPACITY_WARNING' });
      await handler.handle(warning);
      expect(delivered(warning.payload.alertId)).to.be.true;
      expect(mailProducer.publishEvent.callCount).to.equal(admins.length);
    });

    it('a sender that copies the host and id format can still use up the budget (unsigned events)', async () => {
      for (let i = 0; i < MAX_ALERTS_PER_TYPE_PER_HOUR; i += 1) await handler.handle(event({}, { email: false }));
      const real = event();
      await handler.handle(real);
      expect(delivered(real.payload.alertId)).to.be.false;
    });

    it('scopes by host for publisher ids and shares one scope otherwise', () => {
      const parse = (payload: Record<string, unknown>) => {
        const result = parseSandboxCapacityAlert(event(payload));
        if (!result.ok) throw new Error(result.reason);
        return result.alert;
      };
      expect(budgetScope(parse({}))).to.equal('pipeshub-real');
      expect(budgetScope(parse({ host: undefined }))).to.equal('~no-host');
      expect(budgetScope(parse({ alertId: 'forged-1' }))).to.equal(OTHER_IDS_SCOPE);
      expect(budgetScope(parse({ alertId: randomUUID().toUpperCase() }))).to.equal(OTHER_IDS_SCOPE);
    });
  });
});
