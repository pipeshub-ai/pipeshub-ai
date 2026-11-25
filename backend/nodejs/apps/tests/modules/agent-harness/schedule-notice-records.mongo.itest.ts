/**
 * The schedule notifier's records on a MongoDB replica set (TR-07): they read the run's answer and its actor from
 * main's users and messages, and save a notice in the actor's inbox. Run as execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Model, Types } from 'mongoose';
import { mongoScheduleNoticeRecords } from '../../../src/libs/repositories/agent-harness/schedule-notifier/mongo';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import { Notifications } from '../../../src/modules/notification/schema/notification.schema';
import { Users } from '../../../src/modules/user_management/schema/users.schema';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';

registerHarnessModels([
  Users,
  Notifications,
  ChatSessionMessage,
] as unknown as Array<Model<never>>);

interface Person {
  name: string;
  role?: 'admin' | 'member';
  disabled?: boolean;
  deleted?: boolean;
}

const newUser = async (orgId: string, person: Person): Promise<string> => {
  const id = new Types.ObjectId();
  await Users.create({
    _id: id,
    orgId: new Types.ObjectId(orgId),
    email: `${person.name.toLowerCase()}@example.com`,
    fullName: person.name,
    role: person.role ?? 'member',
    isDisabled: person.disabled === true,
    isDeleted: person.deleted === true,
  });
  return id.toHexString();
};

describe("the schedule notifier's records on a MongoDB replica set (TR-07)", function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  describe("the schedule notifier's records (TR-07)", () => {
    it("the answer is the session's latest bot answer or error among its last twenty messages, of that org only", async () => {
      await resetHarnessMongo();
      const org = new Types.ObjectId();
      const session = new Types.ObjectId();
      const message = (
        seq: number,
        messageType: 'user_query' | 'bot_response' | 'error' | 'tool_call',
        content: string,
        orgId = org,
        sessionId = session,
      ) => ({
        sessionId,
        orgId,
        seq,
        messageType,
        content,
      });
      await ChatSessionMessage.create([
        message(1, 'user_query', 'q1'),
        message(2, 'bot_response', 'first answer'),
        message(3, 'user_query', 'q2'),
        message(4, 'error', 'it failed'),
        message(5, 'tool_call', 'tool'),
        message(9, 'bot_response', 'another org', new Types.ObjectId()),
      ]);
      expect(
        await mongoScheduleNoticeRecords.answer(String(org), String(session)),
      ).to.equal('it failed');
      const quiet = new Types.ObjectId();
      await ChatSessionMessage.create([
        message(1, 'user_query', 'only a question', org, quiet),
      ]);
      expect(
        await mongoScheduleNoticeRecords.answer(String(org), String(quiet)),
      ).to.equal(null);
      const buried = new Types.ObjectId();
      await ChatSessionMessage.create([
        message(1, 'bot_response', 'too old', org, buried),
        ...Array.from({ length: 20 }, (_, i) =>
          message(i + 2, 'user_query', `q${i}`, org, buried),
        ),
      ]);
      expect(
        await mongoScheduleNoticeRecords.answer(String(org), String(buried)),
      ).to.equal(null);
    });

    it('the person is a live member with an address, named by full name else email', async () => {
      await resetHarnessMongo();
      const org = new Types.ObjectId().toHexString();
      const mia = await newUser(org, { name: 'Mia' });
      const gone = await newUser(org, { name: 'Gone', deleted: true });
      const unnamed = new Types.ObjectId();
      await Users.create({
        _id: unnamed,
        orgId: new Types.ObjectId(org),
        email: 'nn@example.com',
        fullName: '',
      });
      expect(await mongoScheduleNoticeRecords.person(org, mia)).to.deep.equal({
        email: 'mia@example.com',
        name: 'Mia',
      });
      expect(
        await mongoScheduleNoticeRecords.person(org, unnamed.toHexString()),
      ).to.deep.equal({
        email: 'nn@example.com',
        name: 'nn@example.com',
      });
      expect(await mongoScheduleNoticeRecords.person(org, gone)).to.equal(null);
      expect(
        await mongoScheduleNoticeRecords.person(
          new Types.ObjectId().toHexString(),
          mia,
        ),
      ).to.equal(null);
    });

    it('a notice is saved unread for the actor, from the AI service, and returned as stored', async () => {
      await resetHarnessMongo();
      const org = new Types.ObjectId().toHexString();
      const actor = new Types.ObjectId().toHexString();
      const saved = (await mongoScheduleNoticeRecords.saveNotice(org, actor, {
        type: 'AGENT_SCHEDULE_FIRING_REFUSED',
        severity: 'warning',
        title: 'A scheduled run could not start',
        message: 'm',
        redirectLink: '/workspace/schedules/',
        payload: { scheduleId: 's1' },
      })) as {
        assignedTo: unknown;
        status: string;
        originService: string;
      } | null;
      expect(saved).to.not.equal(null);
      expect([
        String(saved?.assignedTo),
        saved?.status,
        saved?.originService,
      ]).to.deep.equal([actor, 'unread', 'AI Service']);
      expect(
        await Notifications.countDocuments({ orgId: new Types.ObjectId(org) }),
      ).to.equal(1);
    });
  });
});
