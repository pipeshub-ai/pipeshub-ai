import { expect } from 'chai';
import sinon from 'sinon';
import { NotificationContainer } from '../../../../src/modules/notification/container/notification.container';
import { Notifications } from '../../../../src/modules/notification/schema/notification.schema';
import {
  HarnessScheduleNotifier,
  SCHEDULE_APPROVAL_NOTIFICATION,
} from '../../../../src/libs/repositories/agent-harness/schedule-notifier/mongo';

const notice = {
  orgId: '507f1f77bcf86cd799439011',
  actorId: '507f191e810c19729de860ea',
  scheduleId: 'schedule-1',
  scheduleName: 'Nightly digest',
  sessionId: 'session/1',
  runId: 'run-1',
  deadlineAt: Date.UTC(2026, 0, 2, 3, 0, 0),
};

describe('HarnessScheduleNotifier', () => {
  afterEach(() => sinon.restore());

  it("stores an unread warning in the actor's inbox and pushes it to them", async () => {
    const stored = { toObject: () => ({ _id: 'n1' }) };
    const create = sinon.stub(Notifications, 'create').resolves([stored] as never);
    const sendToUser = sinon.spy();
    sinon
      .stub(NotificationContainer, 'getNotificationService')
      .returns({ sendToUser } as never);

    await new HarnessScheduleNotifier().approvalWaiting(notice);

    const [[row]] = create.firstCall.args as unknown as [[Record<string, unknown>]];
    expect(String(row.orgId)).to.equal(notice.orgId);
    expect(String(row.assignedTo)).to.equal(notice.actorId);
    expect(row).to.deep.include({
      type: SCHEDULE_APPROVAL_NOTIFICATION,
      severity: 'warning',
      status: 'unread',
      isDeleted: false,
      redirectLink: '/chat/?conversationId=session%2F1',
      payload: {
        scheduleId: 'schedule-1',
        scheduleName: 'Nightly digest',
        runId: 'run-1',
        conversationId: 'session/1',
        deadlineAt: notice.deadlineAt,
      },
    });
    expect(row.message).to.contain('"Nightly digest"');
    expect(row.message).to.contain('2026-01-02T03:00:00.000Z');
    expect(sendToUser.firstCall.args).to.deep.equal([
      notice.actorId,
      'newNotification',
      { _id: 'n1' },
    ]);
  });

  it('stores without pushing when no notification service is running', async () => {
    const create = sinon
      .stub(Notifications, 'create')
      .resolves([{ toObject: () => ({}) }] as never);
    sinon.stub(NotificationContainer, 'getNotificationService').returns(null);
    await new HarnessScheduleNotifier().approvalWaiting(notice);
    expect(create.calledOnce).to.equal(true);
  });
});
