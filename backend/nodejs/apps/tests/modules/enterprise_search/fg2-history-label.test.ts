/**
 * FG2 HL: today's loop decides a follow-up turn's flow guard from the label of
 * the history Node sends it. Node records what each legacy answer read (the
 * `label` Python puts on the answer) and joins the history's labels into the
 * request, as it does for a durable run's import.
 */
import { expect } from 'chai';
import { historyLabel } from '../../../src/libs/repositories/agent-harness/history-source/mongo';
import { buildAiChatRequest } from '../../../src/modules/enterprise_search/utils/ai-chat-payload';
import { buildAIResponseMessage } from '../../../src/modules/enterprise_search/utils/utils';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import type { IMessage } from '../../../src/modules/enterprise_search/types/conversation.interfaces';

const PRIVATE = { complete: true, sources: [{ kind: 'indexed' as const, recordId: 'rec-1' }] };
const PUBLIC = { complete: true, sources: [] };
const UNKNOWN = { complete: false, sources: [] };

const user = (content: string): IMessage => ({ messageType: 'user_query', content } as IMessage);
const answer = (label?: unknown): IMessage =>
  ({ messageType: 'bot_response', content: 'a', ...(label !== undefined ? { label } : {}) }) as IMessage;

const aiResponse = (data: Record<string, unknown>) =>
  ({ statusCode: 200, data: { answer: 'a', citations: [], ...data } }) as never;

describe('FG2: the history label today\'s loop decides from', () => {
  it('HL-6 joins what every message read: public answers stay public, one private makes it private', () => {
    expect(historyLabel([user('q1'), answer(PUBLIC), user('q2'), answer(PUBLIC)])).to.deep.equal(PUBLIC);
    expect(historyLabel([user('q1'), answer(PRIVATE), user('q2'), answer(PUBLIC)])).to.deep.equal(PRIVATE);
    expect(historyLabel([])).to.deep.equal(PUBLIC);
  });

  it('HL-6 an answer that recorded nothing (written before FG2, or unreadable) is unknown', () => {
    expect(historyLabel([user('q1'), answer()])).to.deep.equal(UNKNOWN);
    expect(historyLabel([user('q1'), answer({ complete: 'yes' })])).to.deep.equal(UNKNOWN);
  });

  it('HL-7 a legacy answer records the label Python sent with it, and nothing it cannot read', () => {
    expect(buildAIResponseMessage(aiResponse({ label: PRIVATE })).label).to.deep.equal(PRIVATE);
    expect(buildAIResponseMessage(aiResponse({ label: PUBLIC })).label).to.deep.equal(PUBLIC);
    expect(buildAIResponseMessage(aiResponse({})).label).to.equal(undefined);
    expect(buildAIResponseMessage(aiResponse({ label: { sources: 'x' } })).label).to.equal(undefined);
  });

  it('HL-8 the request to the AI backend carries the history label for agent and assistant turns alike', () => {
    for (const target of [{ kind: 'assistant' as const }, { kind: 'agent' as const, agentKey: 'agent-1' }]) {
      const withLabel = buildAiChatRequest(target, { query: 'q' }, {
        previousConversations: [{ role: 'user_query', content: 'q1' }],
        historyLabel: PUBLIC,
        isNewConversation: false,
      });
      expect(withLabel.payload['historyLabel']).to.deep.equal(PUBLIC);
      const without = buildAiChatRequest(target, { query: 'q' }, { previousConversations: [], isNewConversation: true });
      expect(without.payload).not.to.have.property('historyLabel');
    }
  });

  it('HL-9 a message written this request never sends its label to a client', () => {
    const doc = new ChatSessionMessage({ messageType: 'bot_response', content: 'a', label: PRIVATE, seq: 1 });
    expect(doc.get('label')).to.deep.equal(PRIVATE);
    expect(doc.toObject()).not.to.have.property('label');
    expect(JSON.parse(JSON.stringify(doc))).not.to.have.property('label');
  });
});
