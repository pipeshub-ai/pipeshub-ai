/**
 * SS1 on PostgreSQL (WP-114): the share route changes a conversation's participants and journals the change in one
 * durable unit of work (`withParticipantRecord` → `inDurableTransaction`), so a recorder that fails rolls the share
 * back (PE-5), and one that succeeds commits the share with its event (PE-10's atomic half). Runs the real route
 * handler with `DOC_STORE=postgres` against the conversation repository and the PostgreSQL journal.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… mocha … tests/modules/agent-harness/session-participants.pg.itest.ts
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { PgConversationRepository } from '../../../src/libs/repositories/conversation/pg';
import {
  getConversationRepository,
  setConversationRepository,
} from '../../../src/libs/repositories/conversation';
import {
  setUserRepository,
  type UserRepository,
} from '../../../src/libs/repositories/user';
import * as controller from '../../../src/modules/enterprise_search/controller/es_controller';
import { IAMServiceCommand } from '../../../src/libs/commands/iam/iam.service.command';
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { SessionParticipantJournal } from '../../../src/modules/agent-harness/application/session-participant-journal';
import { PgJournalStore } from '../../../src/libs/repositories/agent-harness/journal/pg';
import { PgPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/pg';
import type { SessionParticipantRecorder } from '../../../src/modules/agent-harness/ports/session-participants';
import type { NewMessage } from '../../../src/libs/repositories/conversation';
import type { Tx } from '../../../src/libs/repositories/uow';
import { testCipher } from './support/data-protection';
import {
  PgStack,
  hexOrg,
  inOrg,
  pgBins,
  pgSuite,
  startHarnessPg,
} from './support/harness-pg';

const appConfig = {
  aiBackend: 'http://ai.test',
  iamBackend: 'http://iam.test',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
} as never;

for (const bin of pgBins()) {
  pgSuite(
    `SS1 participant events on PostgreSQL (${bin}) behind PgBouncer`,
    function () {
      this.timeout(120_000);
      let stack: PgStack;
      let docStore: string | undefined;
      const journal = new SealedJournalStore(
        new PgJournalStore(),
        testCipher(),
      );
      const recorder = new SessionParticipantJournal<Tx>(
        journal,
        new JournalService(new PgPayloadStore(), testCipher()),
      );

      before(async () => {
        stack = await startHarnessPg(bin, 'harness_participants');
        setConversationRepository(new PgConversationRepository());
        // Main's share admits only users of the org; every recipient here is one.
        setUserRepository({
          listLiveByIds: async (_orgId: string, ids: string[]) =>
            ids.map((_id) => ({ _id })),
        } as unknown as UserRepository);
        docStore = process.env.DOC_STORE;
        process.env.DOC_STORE = 'postgres';
      });
      after(async () => {
        if (docStore === undefined) delete process.env.DOC_STORE;
        else process.env.DOC_STORE = docStore;
        setConversationRepository();
        setUserRepository();
        await stack?.stop();
      });
      beforeEach(() => {
        // The share route syncs record permissions with IAM and Python after the commit.
        sinon
          .stub(IAMServiceCommand.prototype, 'execute')
          .resolves({ statusCode: 200 } as never);
        sinon
          .stub(AIServiceCommand.prototype, 'execute')
          .resolves({ statusCode: 200, data: {} } as never);
      });
      afterEach(() => sinon.restore());

      const legacySession = (orgId: string, owner: string): Promise<string> =>
        inOrg(orgId, async () => {
          const session = await getConversationRepository().openSession(
            {
              orgId,
              userId: owner,
              initiator: owner,
              title: 'Legacy conversation',
              lastActivityAt: 1,
              status: 'Complete',
              sessionType: 'chat',
            },
            {
              messageType: 'user_query',
              content: 'What is the leave policy?',
            } as NewMessage,
          );
          return session._id;
        });

      const share = (
        orgId: string,
        owner: string,
        sessionId: string,
        body: Record<string, unknown>,
        using: SessionParticipantRecorder<Tx>,
      ) =>
        inOrg(
          orgId,
          async () => {
            const next = sinon.stub();
            const res = { status: () => res, json: () => res };
            await controller.shareConversationById(appConfig, () => using)(
              {
                headers: { authorization: 'Bearer owner' },
                params: { conversationId: sessionId },
                body,
                query: {},
                user: { userId: owner, orgId },
                context: { requestId: 'req-share' },
              } as never,
              res as never,
              next as never,
            );
            return next.firstCall?.args[0] as unknown;
          },
          owner,
        );

      const stored = (orgId: string, sessionId: string) =>
        inOrg(
          orgId,
          async () =>
            (await getConversationRepository().findHarnessSession(
              sessionId,
              orgId,
            ))!.session,
        );

      it('PE-5: a recorder that fails rolls the share back: the conversation keeps its participants', async () => {
        const [org, owner, recipient] = [hexOrg(), hexOrg(), hexOrg()];
        const sessionId = await legacySession(org, owner);
        const failing: SessionParticipantRecorder<Tx> = {
          participantsChanged: async () => {
            throw new Error('journal unavailable');
          },
          forked: async () => undefined,
        };
        const error = await share(
          org,
          owner,
          sessionId,
          { userIds: [recipient] },
          failing,
        );
        expect((error as Error | undefined)?.message).to.equal(
          'journal unavailable',
        );
        const session = await stored(org, sessionId);
        expect(session.isShared).to.equal(false);
        expect(session.sharedWith).to.deep.equal([]);
        expect(
          await inOrg(org, () => journal.readStream(org, sessionId, 0, 100)),
        ).to.have.length(0);
      });

      it('PE-10: a recorder that succeeds commits the share with its event, in the same unit', async () => {
        const [org, owner, recipient] = [hexOrg(), hexOrg(), hexOrg()];
        const sessionId = await legacySession(org, owner);
        expect(
          await share(
            org,
            owner,
            sessionId,
            { userIds: [recipient] },
            recorder,
          ),
        ).to.equal(undefined);
        const session = await stored(org, sessionId);
        expect(session.isShared).to.equal(true);
        expect(
          session.sharedWith!.map((s) => [s.userId, s.accessLevel]),
        ).to.deep.equal([[recipient, 'read']]);
        const events = await inOrg(org, () =>
          journal.readStream(org, sessionId, 0, 100),
        );
        expect(events.map((e) => [e.type, e.data])).to.deep.equal([
          [
            'session.participant_changed',
            {
              by: `p_${owner}`,
              subject: `p_${recipient}`,
              role: 'viewer',
              prevRole: 'none',
            },
          ],
        ]);
      });
    },
  );
}
