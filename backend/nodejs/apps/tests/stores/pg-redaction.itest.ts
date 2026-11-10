/**
 * The per-viewer redaction of shared chats and saved searches, on PostgreSQL: a viewer gets a placeholder for each
 * citation, attachment and saved record they cannot read, on a conversation read, the archive list, the search history and
 * a search by id. The controllers run for real over the PostgreSQL conversation and semantic-search stores; only the query
 * service's access check (the AI-service command) and the project lookup are stubbed.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-stores
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  getConversationById,
  getSearchById,
  listAllArchivesConversation,
  searchHistory,
} from '../../src/modules/enterprise_search/controller/es_controller';
import { AIServiceCommand } from '../../src/libs/commands/ai_service/ai.service.command';
import { ProjectService } from '../../src/modules/projects/services/project.service';
import { PgConversationRepository } from '../../src/libs/repositories/conversation/pg';
import { PgSemanticSearchRepository } from '../../src/libs/repositories/semantic-search/pg';
import { setConversationRepository } from '../../src/libs/repositories/conversation';
import { setSemanticSearchRepository } from '../../src/libs/repositories/semantic-search';
import { setUserRepository } from '../../src/libs/repositories/user';
import {
  PG_BINS,
  PgStack,
  haveStack,
  inOrg,
  newId,
  startPgStack,
} from './pg-store-harness';

const SECRET = 'SECRET-quarterly-numbers';
const appConfig = {
  aiBackend: 'http://localhost:8000',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
} as never;

const suite = haveStack ? describe : describe.skip;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`PostgreSQL shared-chat redaction (${bin})`, function () {
    this.timeout(240_000);
    let stack: PgStack;
    const conversations = new PgConversationRepository();
    const searches = new PgSemanticSearchRepository();

    before(async () => {
      stack = await startPgStack(bin, 'redaction', { pool: 4, clientPool: 16 });
    });
    after(async () => {
      await stack?.stop();
    });
    beforeEach(() => {
      setConversationRepository(conversations);
      setSemanticSearchRepository(searches);
      setUserRepository({ listNamesByIds: () => Promise.resolve([]) } as never);
      sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([]);
      sinon.stub(ProjectService, 'orgHasTeamMembers').resolves(false);
    });
    afterEach(() => {
      sinon.restore();
      setConversationRepository();
      setSemanticSearchRepository();
      setUserRepository();
    });

    /** The access route names `readable`; every other call of the command is the unrelated permission sync. */
    const stubAccess = (readable: string[]): string[][] => {
      const asked: string[][] = [];
      sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(function (
        this: { uri: string; body?: string },
      ) {
        if (this.uri.endsWith('/chat/records/access')) {
          const body = this.body
            ? (JSON.parse(this.body) as { recordIds?: string[] })
            : {};
          asked.push(body.recordIds ?? []);
          return Promise.resolve({
            statusCode: 200,
            data: { accessible: readable, accessibleUrls: [] },
            msg: '',
          } as never);
        }
        return Promise.resolve({ statusCode: 200, data: {}, msg: '' } as never);
      });
      return asked;
    };

    const world = async () => {
      const org = newId(),
        owner = newId(),
        viewer = newId();
      const cites = await inOrg(org, () =>
        conversations.createCitations(
          ['ok', 'no'].map((r, i) => ({
            content: r === 'ok' ? 'readable text' : SECRET,
            chunkIndex: i,
            citationType: 'vectordb|document',
            metadata: {
              orgId: org,
              recordId: `rec-${r}`,
              recordName: `${r}.pdf`,
              webUrl: `https://x/${r}`,
            },
          })),
        ),
      );
      const session = await inOrg(org, async () => {
        const s = await conversations.openSession(
          {
            orgId: org,
            userId: owner,
            initiator: owner,
            title: 'shared chat',
            lastActivityAt: Date.now(),
            status: 'Complete',
            sessionType: 'chat',
          } as never,
          { messageType: 'user_query', content: 'question' },
        );
        await conversations.appendMessages(s._id, org, [
          {
            messageType: 'bot_response',
            content: 'The answer [1][2]',
            citations: cites.map((c, i) => ({
              citationId: c._id,
              excerpt: i === 0 ? 'readable excerpt' : SECRET,
            })),
            attachments: [
              { recordId: 'att-ok', recordName: 'ok.pdf' },
              { recordId: 'att-no', recordName: 'hidden-name.pdf' },
            ],
          },
        ]);
        await conversations.setSharing({ kind: 'chat' }, s._id, {
          isShared: true,
          sharedWith: [{ userId: viewer, accessLevel: 'read' }],
        });
        return s;
      }, owner);
      return { org, owner, viewer, cites, session };
    };

    const run = async (
      org: string,
      viewer: string,
      handler: (q: never, r: never, n: never) => Promise<unknown>,
      extra: Record<string, unknown> = {},
    ): Promise<{ body: any; failure: unknown }> => {
      let body: unknown;
      const r: any = {
        status: () => r,
        json: (b: unknown) => {
          body = b;
          return r;
        },
      };
      let failure: unknown;
      const req = {
        headers: { authorization: 'Bearer t' },
        body: {},
        params: {},
        query: {},
        user: { userId: viewer, orgId: org },
        context: { requestId: 'r' },
        on: () => undefined,
        ...extra,
      };
      await inOrg(
        org,
        () => handler(req as never, r as never, ((e: unknown) => (failure = e)) as never),
        viewer,
      );
      return { body, failure };
    };

    const hidden = { redacted: true, reason: 'no_access' };

    it('a conversation read hides the citations and attachments the viewer cannot read, and the owner sees all', async () => {
      const w = await world();
      stubAccess(['rec-ok', 'att-ok']);
      const asViewer = await run(w.org, w.viewer, getConversationById(appConfig) as never, {
        params: { conversationId: w.session._id },
      });
      expect(asViewer.failure).to.equal(undefined);
      const [, answer] = asViewer.body.conversation.messages;
      expect(answer.citations[0].citationData.content).to.equal('readable text');
      expect(answer.citations[1]).to.deep.equal(hidden);
      expect(answer.attachments).to.deep.equal([{ recordId: 'att-ok', recordName: 'ok.pdf' }]);
      expect(JSON.stringify(asViewer.body)).to.not.contain(SECRET);
      expect(JSON.stringify(asViewer.body)).to.not.contain('hidden-name');

      sinon.restore();
      sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([]);
      sinon.stub(ProjectService, 'orgHasTeamMembers').resolves(false);
      const asked = stubAccess([]);
      const asOwner = await run(w.org, w.owner, getConversationById(appConfig) as never, {
        params: { conversationId: w.session._id },
      });
      const ownerAnswer = asOwner.body.conversation.messages[1];
      expect(ownerAnswer.citations[1].citationData.content).to.equal(SECRET);
      expect(ownerAnswer.attachments).to.have.length(2);
      expect(asked, 'no access check for the owner').to.have.length(0);
    });

    it('a failed access check hides every record reference', async () => {
      const w = await world();
      sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(function (this: { uri: string }) {
        return this.uri.endsWith('/chat/records/access')
          ? Promise.reject(new Error('access service down'))
          : Promise.resolve({ statusCode: 200, data: {}, msg: '' } as never);
      });
      const r = await run(w.org, w.viewer, getConversationById(appConfig) as never, {
        params: { conversationId: w.session._id },
      });
      const answer = r.body.conversation.messages[1];
      expect(answer.citations).to.deep.equal([hidden, hidden]);
      expect(answer.attachments).to.deep.equal([]);
      expect(JSON.stringify(r.body)).to.not.contain(SECRET);
    });

    it('the archive list redacts the messages of another user’s archived chat, citations stored as bare ids included', async () => {
      const w = await world();
      await inOrg(
        w.org,
        () => conversations.setArchived({ kind: 'chat' }, w.session._id, { by: w.owner }),
        w.owner,
      );
      stubAccess(['rec-ok']);
      const r = await run(w.org, w.viewer, listAllArchivesConversation(appConfig) as never);
      expect(r.failure).to.equal(undefined);
      const mine = r.body.conversations.find((c: any) => c._id === w.session._id);
      expect(mine, 'the shared archived chat is listed').to.not.equal(undefined);
      const answer = mine.messages.find((m: any) => m.messageType === 'bot_response');
      expect(answer.citations[0].excerpt).to.equal('readable excerpt');
      expect(answer.citations[1]).to.deep.equal(hidden);
      expect(answer.attachments).to.deep.equal([]);
      expect(JSON.stringify(r.body)).to.not.contain(SECRET);
    });

    const savedSearch = async (w: Awaited<ReturnType<typeof world>>) =>
      inOrg(
        w.org,
        async () => {
          const s = await searches.create({
            orgId: w.org,
            userId: w.owner,
            query: 'budget',
            limit: 5,
            hits: ['ok', 'no'].map((r, i) => ({
              content: r === 'ok' ? 'readable text' : SECRET,
              chunkIndex: i,
              citationType: 'vectordb|document',
              metadata: { orgId: w.org, recordId: `rec-${r}`, recordName: `${r}.pdf`, webUrl: `https://x/${r}` },
            })),
            records: new Map([
              ['rec-ok', JSON.stringify({ name: 'ok.pdf' })],
              ['rec-no', JSON.stringify({ name: SECRET })],
            ]),
          });
          await searches.setSharing(s._id, {
            isShared: true,
            sharedWith: [{ userId: w.viewer, accessLevel: 'read' }],
          });
          return s;
        },
        w.owner,
      );

    it('the search history hides the hits and records of a search shared with the viewer that they cannot read', async () => {
      const w = await world();
      const s = await savedSearch(w);
      stubAccess(['rec-ok']);
      const r = await run(w.org, w.viewer, searchHistory(appConfig) as never);
      expect(r.failure).to.equal(undefined);
      const out = r.body.searchHistory.find((x: any) => x._id === s._id);
      expect(out, 'the shared search is listed').to.not.equal(undefined);
      // the history lists hits by id only, and an id cannot be checked, so none is shown to another viewer
      expect(out.citationIds).to.deep.equal([]);
      expect(Object.keys(out.records)).to.deep.equal(['rec-ok']);
      expect(JSON.stringify(r.body)).to.not.contain(SECRET);
    });

    it('a search by id hides them too, and the owner’s own search is returned whole without an access check', async () => {
      const w = await world();
      const s = await savedSearch(w);
      stubAccess(['rec-ok']);
      const viewer = await run(w.org, w.viewer, getSearchById(appConfig) as never, {
        params: { searchId: s._id },
      });
      expect(viewer.failure).to.equal(undefined);
      expect(JSON.stringify(viewer.body)).to.not.contain(SECRET);
      expect(JSON.stringify(viewer.body)).to.contain('rec-ok');

      sinon.restore();
      sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([]);
      sinon.stub(ProjectService, 'orgHasTeamMembers').resolves(false);
      const asked = stubAccess([]);
      const owner = await run(w.org, w.owner, getSearchById(appConfig) as never, {
        params: { searchId: s._id },
      });
      expect(JSON.stringify(owner.body)).to.contain(SECRET);
      expect(asked).to.have.length(0);
    });
  });
}
