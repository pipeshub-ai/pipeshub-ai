import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { ChatAccessLoader } from '../../../src/modules/authz/loaders/chat.loader';
import { projectRole } from '../../../src/modules/authz/domain/rules';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { Project } from '../../../src/modules/projects/schema/project.schema';

const oid = () => new mongoose.Types.ObjectId();
const lean = (value: unknown) => ({ select: () => ({ lean: () => Promise.resolve(value) }), lean: () => Promise.resolve(value) });

describe('ChatAccessLoader', () => {
  afterEach(() => sinon.restore());

  it('returns null for malformed ids without querying', async () => {
    const find = sinon.stub(ChatSession, 'findOne');
    expect(await new ChatAccessLoader().load('bad', 'worse')).to.equal(null);
    expect(find.called).to.equal(false);
  });

  it('scopes the chat lookup to the org and returns null when absent', async () => {
    const find = sinon.stub(ChatSession, 'findOne').returns(lean(null) as any);
    const org = oid().toString();
    const chat = oid().toString();
    expect(await new ChatAccessLoader().load(org, chat)).to.equal(null);
    expect(find.firstCall.args[0]).to.deep.equal({ _id: chat, orgId: org });
  });

  it('carries the project ceiling and members into the facts so H2 can use them', async () => {
    const orgId = oid();
    const me = oid();
    const session = { orgId, userId: oid(), projectId: oid() };
    sinon.stub(ChatSession, 'findOne').returns(lean(session) as any);
    sinon.stub(Project, 'findOne').returns(
      lean({
        orgId,
        userId: oid(),
        visibility: 'private',
        projectChatAccess: 'editor',
        members: [{ principalType: 'team', teamId: 'team-uuid', role: 'editor' }],
      }) as any,
    );
    const loaded = await new ChatAccessLoader().load(orgId.toString(), oid().toString());
    expect(loaded?.project?.projectChatAccess).to.equal('editor');
    expect(projectRole(loaded!.project!, { userId: me.toString(), orgId: orgId.toString(), teamIds: ['team-uuid'] })).to.equal('editor');
  });

  it('treats a chat whose project is gone as having no project (H9)', async () => {
    const orgId = oid();
    sinon.stub(ChatSession, 'findOne').returns(lean({ orgId, userId: oid(), projectId: oid() }) as any);
    sinon.stub(Project, 'findOne').returns(lean(null) as any);
    expect((await new ChatAccessLoader().load(orgId.toString(), oid().toString()))?.project).to.equal(null);
  });

  describe('loadScoped', () => {
    const chain = (value: unknown) => ({ select: () => ({ lean: () => Promise.resolve(value) }) });

    it('filters by id, org, liveness and the chat kind in one read', async () => {
      const orgId = oid();
      const find = sinon.stub(ChatSession, 'findOne').returns(chain({ orgId, userId: oid() }) as any);
      const chat = oid().toString();
      const loaded = await new ChatAccessLoader().loadScoped(orgId.toString(), { id: chat, kind: 'chat' });
      expect(loaded).to.not.equal(null);
      expect(find.calledOnce).to.equal(true);
      expect(find.firstCall.args[0]).to.deep.equal({ _id: chat, orgId: orgId.toString(), isDeleted: false, sessionType: 'chat' });
    });

    it('adds the agentKey for an agent conversation', async () => {
      const find = sinon.stub(ChatSession, 'findOne').returns(chain(null) as any);
      const org = oid().toString();
      const id = oid().toString();
      expect(await new ChatAccessLoader().loadScoped(org, { id, kind: 'agent', agentKey: 'a-1' })).to.equal(null);
      expect(find.firstCall.args[0]).to.deep.equal({ _id: id, orgId: org, isDeleted: false, sessionType: 'agent', agentKey: 'a-1' });
    });

    it('does not query for a malformed id or an agent without a key', async () => {
      const find = sinon.stub(ChatSession, 'findOne');
      const loader = new ChatAccessLoader();
      expect(await loader.loadScoped(oid().toString(), { id: 'bad', kind: 'chat' })).to.equal(null);
      expect(await loader.loadScoped(oid().toString(), { id: oid().toString(), kind: 'agent' })).to.equal(null);
      expect(find.called).to.equal(false);
    });
  });
});
