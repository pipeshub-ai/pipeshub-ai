/**
 * Hiding what a reader cannot read, on PostgreSQL. An answer that used a record its reader cannot read is replaced
 * whole by a marker, for the owner and a shared viewer alike; another user's question loses the attachments the viewer
 * cannot read; a saved search loses the hits and records its reader cannot read. Covered on a conversation read, the
 * archive list, the search history and a search by id. The controllers run for real over the PostgreSQL conversation and
 * semantic-search stores; only the query service's access check (the AI-service command) and the project lookup are stubbed.
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
    const stubProjects = () => {
      sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([]);
      sinon.stub(ProjectService, 'orgHasTeamMembers').resolves(false);
    };
    beforeEach(() => {
      setConversationRepository(conversations);
      setSemanticSearchRepository(searches);
      setUserRepository({ listNamesByIds: () => Promise.resolve([]) } as never);
      stubProjects();
    });
    afterEach(() => {
      sinon.restore();
      setConversationRepository();
      setSemanticSearchRepository();
      setUserRepository();
    });

    /** The access route names `readable`; every other call of the command is the unrelated permission sync. */
    const stubAccess = (readable: string[]): string[][] => {
      sinon.restore();
      stubProjects();
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
          {
            messageType: 'user_query',
            content: 'question',
            attachments: [
              { recordId: 'att-ok', recordName: 'ok.pdf' },
              { recordId: 'att-no', recordName: 'hidden-name.pdf' },
            ],
          },
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

    const ALL = ['rec-ok', 'rec-no', 'att-ok', 'att-no'];
    const lost = { redacted: true, reason: 'no_longer_accessible' };
    const unconfirmed = { redacted: true, reason: 'access_check_unavailable' };
    const readConversation = (w: Awaited<ReturnType<typeof world>>, as: string) =>
      run(w.org, as, getConversationById(appConfig) as never, {
        params: { conversationId: w.session._id },
      });
    /** The answer keeps its marker and nothing it said or used. */
    const expectHidden = (answer: any, marker: object) => {
      expect(answer.answerHidden).to.deep.equal(marker);
      for (const k of ['citations', 'attachments', 'referenceData', 'followUpQuestions', 'reasoning', 'tools', 'parts'])
        expect(answer[k] ?? [], k).to.deep.equal([]);
      const wire = JSON.stringify(answer);
      for (const leaked of [SECRET, 'hidden-name', 'The answer', 'readable', 'rec-no', 'att-no'])
        expect(wire).to.not.contain(leaked);
    };

    it('a conversation read hides an answer that used a record its reader cannot read, for the owner too, and drops the viewer’s unreadable attachments', async () => {
      const w = await world();
      const asked = stubAccess(['rec-ok', 'att-ok']);
      const asViewer = await readConversation(w, w.viewer);
      expect(asViewer.failure).to.equal(undefined);
      const [question, answer] = asViewer.body.conversation.messages;
      expect(question.content).to.equal('question');
      expect(question.attachments).to.deep.equal([{ recordId: 'att-ok', recordName: 'ok.pdf' }]);
      expectHidden(answer, lost);
      expect(asked.flat()).to.include.members(['rec-no', 'att-no']);
      expect(JSON.stringify(asViewer.body)).to.not.contain(SECRET);
      expect(JSON.stringify(asViewer.body)).to.not.contain('hidden-name');

      stubAccess(['rec-ok', 'att-ok']);
      expectHidden((await readConversation(w, w.owner)).body.conversation.messages[1], lost);

      for (const reader of [w.viewer, w.owner]) {
        stubAccess(ALL);
        const whole = (await readConversation(w, reader)).body.conversation.messages;
        expect(whole[0].attachments, reader).to.have.length(2);
        expect(whole[1].answerHidden, reader).to.equal(undefined);
        expect(whole[1].content).to.equal('The answer [1][2]');
        expect(whole[1].citations.map((c: any) => c.citationData.content)).to.deep.equal(['readable text', SECRET]);
        expect(whole[1].attachments).to.have.length(2);
      }
    });

    it('a failed access check hides the answer as unconfirmed and every attachment of another user', async () => {
      const w = await world();
      sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(function (this: { uri: string }) {
        return this.uri.endsWith('/chat/records/access')
          ? Promise.reject(new Error('access service down'))
          : Promise.resolve({ statusCode: 200, data: {}, msg: '' } as never);
      });
      const r = await readConversation(w, w.viewer);
      expect(r.failure).to.equal(undefined);
      const [question, answer] = r.body.conversation.messages;
      expect(question.attachments).to.deep.equal([]);
      expectHidden(answer, unconfirmed);
      expect(JSON.stringify(r.body)).to.not.contain(SECRET);
      expect(JSON.stringify(r.body)).to.not.contain('hidden-name');
    });

    it('the archive list hides the answer of another user’s archived chat, citations stored as bare ids included', async () => {
      const w = await world();
      await inOrg(
        w.org,
        () => conversations.setArchived({ kind: 'chat' }, w.session._id, { by: w.owner }),
        w.owner,
      );
      const listArchived = async () => {
        const r = await run(w.org, w.viewer, listAllArchivesConversation(appConfig) as never);
        expect(r.failure).to.equal(undefined);
        const mine = r.body.conversations.find((c: any) => c._id === w.session._id);
        expect(mine, 'the shared archived chat is listed').to.not.equal(undefined);
        return { body: r.body, answer: mine.messages.find((m: any) => m.messageType === 'bot_response') };
      };
      const asked = stubAccess(['rec-ok']);
      const denied = await listArchived();
      expectHidden(denied.answer, lost);
      // The check was asked about both bare citation ids, which proves each was resolved to its record in PostgreSQL.
      expect(asked.flat()).to.include.members(['rec-ok', 'rec-no']);
      expect(JSON.stringify(denied.body)).to.not.contain(SECRET);

      stubAccess(ALL);
      const whole = await listArchived();
      expect(whole.answer.answerHidden).to.equal(undefined);
      expect(whole.answer.citations.map((c: any) => c.excerpt)).to.deep.equal(['readable excerpt', SECRET]);
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

    it('a search by id hides them too, and the owner’s own search hides only a record the owner lost', async () => {
      const w = await world();
      const s = await savedSearch(w);
      stubAccess(['rec-ok']);
      const viewer = await run(w.org, w.viewer, getSearchById(appConfig) as never, {
        params: { searchId: s._id },
      });
      expect(viewer.failure).to.equal(undefined);
      expect(JSON.stringify(viewer.body)).to.not.contain(SECRET);
      expect(JSON.stringify(viewer.body)).to.contain('rec-ok');

      const asOwner = async () =>
        JSON.stringify(
          (await run(w.org, w.owner, getSearchById(appConfig) as never, { params: { searchId: s._id } })).body,
        );
      stubAccess(['rec-ok', 'rec-no']);
      expect(await asOwner()).to.contain(SECRET);

      stubAccess(['rec-ok']);
      const lostOne = await asOwner();
      expect(lostOne).to.not.contain(SECRET);
      expect(lostOne).to.contain('rec-ok');
      expect(lostOne).to.contain('"reason":"no_longer_accessible"');
    });
  });
}
