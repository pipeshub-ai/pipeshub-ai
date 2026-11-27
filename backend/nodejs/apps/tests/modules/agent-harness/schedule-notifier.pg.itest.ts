/**
 * TR-07 / AP-08 schedule notices on PostgreSQL: the notifier's records go through main's conversation, user and
 * notification repositories (`pgScheduleNoticeRecords`), called as the firer and the notice sweep call them, as the
 * org of the firing or notice they work on (ADR-SEC-02). The run's answer is the newest answer of its conversation; a removed person has no address; the
 * inbox row is the actor's, in the actor's org only.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-harness
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { getConversationRepository } from '../../../src/libs/repositories/conversation';
import {
  HarnessScheduleNotifier,
  SCHEDULE_APPROVAL_NOTIFICATION,
  SCHEDULE_RUN_ENDED_NOTIFICATION,
} from '../../../src/libs/repositories/agent-harness/schedule-notifier/mongo';
import { pgScheduleNoticeRecords } from '../../../src/libs/repositories/agent-harness/schedule-notifier/pg';
import {
  PgStack,
  boundStore,
  inOrg,
  pgBins,
  pgSuite,
  startHarnessPg,
} from './support/harness-pg';
import {
  pgOrg,
  pgUser,
  setUserDeleted,
  usePgIdentityRepositories,
} from './support/harness-pg-identity';

/** The records, each call run as the org it names, as the firer and the notice sweep bind it (ADR-SEC-02). */
const records = boundStore(pgScheduleNoticeRecords);

for (const bin of pgBins()) {
  pgSuite(
    `agent harness schedule notices on PostgreSQL (${bin}) behind PgBouncer`,
    function () {
      this.timeout(120_000);
      let stack: PgStack;
      let restore: () => void;
      before(async () => {
        stack = await startHarnessPg(bin, 'harness_schedule_notices');
        restore = usePgIdentityRepositories();
      });
      after(async () => {
        restore?.();
        await stack?.stop();
      });

      const conversation = async (
        orgId: string,
        userId: string,
        messages: Array<{ messageType: string; content: string }>,
      ) => {
        const session = await inOrg(orgId, () =>
          getConversationRepository().createSession({
            orgId,
            userId,
            initiator: userId,
            lastActivityAt: Date.now(),
            status: 'Complete',
            sessionType: 'chat',
          }),
        );
        const sessionId = session._id;
        await inOrg(orgId, () =>
          getConversationRepository().appendMessages(
            sessionId,
            orgId,
            messages as never,
          ),
        );
        return sessionId;
      };
      const inbox = (orgId: string) =>
        stack.admin(
          async (c) =>
            (
              await c.query(
                `SELECT org_id, assigned_to, type, status, severity, payload FROM platform.notifications WHERE org_id = $1 ORDER BY created_at`,
                [orgId],
              )
            ).rows,
        );

      it("SN-P1: the run's answer is its conversation's newest answer, read as its org, as the firer binds it", async () => {
        const orgId = await pgOrg(stack);
        const actor = await pgUser(stack, orgId);
        const sessionId = await conversation(orgId, actor, [
          { messageType: 'user_query', content: 'first ask' },
          { messageType: 'bot_response', content: 'first answer' },
          { messageType: 'user_query', content: 'second ask' },
          { messageType: 'bot_response', content: 'second answer' },
        ]);
        expect(await records.answer(orgId, sessionId)).to.equal(
          'second answer',
        );
        const silent = await conversation(orgId, actor, [
          { messageType: 'user_query', content: 'only an ask' },
        ]);
        expect(await records.answer(orgId, silent)).to.equal(
          null,
        );
      });

      it('SN-P2: a live person has an address and a name; a removed one, or one of another org, has none', async () => {
        const orgId = await pgOrg(stack);
        const ada = await pgUser(stack, orgId, {
          fullName: 'Ada Lovelace',
          email: 'ada@example.com',
        });
        const nameless = await pgUser(stack, orgId, {
          email: 'nameless@example.com',
        });
        expect(await records.person(orgId, ada)).to.deep.equal({
          email: 'ada@example.com',
          name: 'Ada Lovelace',
        });
        expect(
          await records.person(orgId, nameless),
        ).to.deep.equal({
          email: 'nameless@example.com',
          name: 'nameless@example.com',
        });
        expect(
          await records.person(await pgOrg(stack), ada),
        ).to.equal(null);
        await setUserDeleted(stack, orgId, ada);
        expect(await records.person(orgId, ada)).to.equal(null);
      });

      it("SN-P3: the notifier stores the actor's unread notices in their org's inbox, nothing elsewhere", async () => {
        const orgId = await pgOrg(stack);
        const other = await pgOrg(stack);
        const actor = await pgUser(stack, orgId);
        const sessionId = await conversation(orgId, actor, [
          {
            messageType: 'bot_response',
            content: 'All three reports are filed.',
          },
        ]);
        const notifier = new HarnessScheduleNotifier({
          records: records,
        });
        await notifier.approvalWaiting({
          orgId,
          actorId: actor,
          scheduleId: 'schedule-1',
          scheduleName: 'Nightly digest',
          sessionId,
          runId: 'run-1',
          deadlineAt: Date.UTC(2026, 0, 2, 3, 0, 0),
        });
        await notifier.runEnded({
          orgId,
          actorId: actor,
          scheduleId: 'schedule-1',
          scheduleName: 'Nightly digest',
          sessionId,
          runId: 'run-1',
          status: 'completed',
          channels: ['in_app'],
        } as never);
        const rows = await inbox(orgId);
        expect(rows.map((row: { type: string }) => row.type)).to.deep.equal([
          SCHEDULE_APPROVAL_NOTIFICATION,
          SCHEDULE_RUN_ENDED_NOTIFICATION,
        ]);
        for (const row of rows)
          expect(row).to.deep.include({
            org_id: orgId,
            assigned_to: actor,
            status: 'unread',
          });
        expect(rows[0].payload).to.deep.include({
          scheduleId: 'schedule-1',
          runId: 'run-1',
          conversationId: sessionId,
        });
        expect(await inbox(other)).to.deep.equal([]);
      });

      it("SN-P4: a call made in another organisation's context is refused, never written there", async () => {
        const orgId = await pgOrg(stack);
        const actor = await pgUser(stack, orgId);
        const failure = await inOrg(await pgOrg(stack), () =>
          pgScheduleNoticeRecords
            .saveNotice(orgId, actor, {
              type: 'X',
              severity: 'info',
              title: 't',
              message: 'm',
              redirectLink: '/',
              payload: {},
            })
            .catch((e: Error) => e),
        );
        expect(failure).to.be.instanceOf(Error);
        expect(await inbox(orgId)).to.deep.equal([]);
      });
    },
  );
}
