import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { getConversationRepository } from '../../../src/libs/repositories/conversation';
import { chatTurnMiddleware } from '../../../src/modules/agent-harness/transport/chat-turn-route';
import { isTemporaryConversation } from '../../../src/modules/enterprise_search/utils/temporary-chat';

const ORG = String(new Types.ObjectId());
const USER = String(new Types.ObjectId());
const CONVERSATION = String(new Types.ObjectId());

describe('temporary chat turns stay off the agent harness', () => {
  afterEach(() => sinon.restore());

  const followUp = async (legacyOnly: () => Promise<boolean>) => {
    const service = {
      declined: sinon.spy(),
      start: sinon.stub().rejects(new Error('durable route')),
      answersGatedQuestion: sinon.stub().resolves(false),
    };
    const middleware = chatTurnMiddleware(service as never, {
      kind: 'chat',
      surface: () => 'web',
      enabled: () => true,
      legacyOnly,
    });
    const req = {
      body: { query: 'and then?', chatMode: 'agent', harness: 'durable' },
      params: { conversationId: CONVERSATION },
      headers: {},
      query: {},
      user: { userId: USER, orgId: ORG },
      header: () => undefined,
      get: () => undefined,
    };
    const error = await new Promise<unknown>((resolve) => {
      middleware(req as never, { locals: {} } as never, resolve);
    });
    return { service, error };
  };

  it('hands a follow-up in a temporary chat to the legacy handler without admitting a run', async () => {
    const { service, error } = await followUp(async () => true);
    expect(error).to.equal(undefined);
    expect(service.start.called).to.equal(false);
    expect(service.declined.calledOnce).to.equal(true);
  });

  it('leaves a normal chat to routing', async () => {
    const { service } = await followUp(async () => false);
    expect(service.start.calledOnce).to.equal(true);
  });

  it('fails closed when the chat cannot be read', async () => {
    const { service, error } = await followUp(async () => {
      throw new Error('store down');
    });
    expect((error as Error).message).to.equal('store down');
    expect(service.start.called).to.equal(false);
  });

  it('isTemporaryConversation is true only for the owner\'s temporary chat', async () => {
    const find = sinon.stub(getConversationRepository(), 'findOwned');
    const owner = { orgId: ORG, actorId: USER };
    find.resolves({ isTemporary: true } as never);
    expect(await isTemporaryConversation(owner, CONVERSATION)).to.equal(true);
    find.resolves({} as never);
    expect(await isTemporaryConversation(owner, CONVERSATION)).to.equal(false);
    find.resolves(null);
    expect(await isTemporaryConversation(owner, CONVERSATION)).to.equal(false);
  });
});
