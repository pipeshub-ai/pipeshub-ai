/**
 * R35-SLACK D-SLACK-ASK (R34FIX-FU-2): the bot reads a hand-off (the run waits on an approval in PipesHub) as its own
 * end, not as an answer, and tells the thread what waits and where to decide; the default assistant's Agent mode asks
 * for the agent harness as the web app's does.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { createSlackAGUIEventHandler } from '../../../src/integrations/slack-bot/src/utils/agui-stream';
import { handOffText } from '../../../src/integrations/slack-bot/src/utils/hand-off';

const finished = (result: unknown) => ({ event: 'RUN_FINISHED', data: { type: 'RUN_FINISHED', result } });
const conversation = { _id: 'c1', status: 'Waiting', messages: [{ messageType: 'bot_response', content: 'words' }] };

describe('slack-bot: a run handed off to PipesHub at an approval (D-SLACK-ASK)', () => {
  it('SLACKBOT-1: a hand-off is not an answer; an end is', () => {
    const onHandOff = sinon.stub();
    const onComplete = sinon.stub();
    const handle = createSlackAGUIEventHandler({ onHandOff, onComplete });
    handle(finished({
      conversation,
      meta: {
        awaiting: 'approval',
        link: '/chat/?agentId=a1&conversationId=c1',
        waitingOn: [{ toolName: 'mcp_e2esaas_create_ticket', displayName: 'e2esaas · create_ticket' }, { toolName: 7 }],
      },
    }));
    expect(onComplete.called).to.equal(false);
    expect(onHandOff.firstCall.args[0]).to.deep.equal({
      conversationId: 'c1',
      link: '/chat/?agentId=a1&conversationId=c1',
      waitingOn: [{ toolName: 'mcp_e2esaas_create_ticket', displayName: 'e2esaas · create_ticket' }],
    });
    handle(finished({ conversation: { ...conversation, status: 'Complete' }, meta: {} }));
    expect(onComplete.calledOnce).to.equal(true);
  });

  it('SLACKBOT-2: the thread is told what waits and given the conversation to decide in, as plain text', () => {
    const text = handOffText(
      {
        conversationId: 'c1',
        link: '/chat/?conversationId=c1',
        waitingOn: [{ toolName: 'mcp_e2esaas_create_ticket', displayName: 'e2esaas · create_ticket' }],
      },
      'https://pipeshub.example',
    );
    expect(text).to.equal(
      'This needs an approval before I can go on: *e2esaas · Create ticket*.\n' +
        '<https://pipeshub.example/chat/?conversationId=c1|Open this conversation in PipesHub> to approve or decline it. ' +
        "I'll post the answer here once it goes on.",
    );
    const odd = handOffText(
      { conversationId: 'c 2', waitingOn: [{ toolName: 'x', displayName: 'Evil · <!channel>' }] },
      'https://p.example',
    );
    expect(odd).to.contain('&lt;!channel&gt;');
    expect(odd).to.contain('<https://p.example/chat/?conversationId=c%202|');
  });

  it('R40TEXT-FU-2: a waiting tool\'s name from an older worker is shown display-safe', () => {
    const text = handOffText(
      { conversationId: 'c3', waitingOn: [{ toolName: 'x', displayName: 'Pay \u202Eexe.ssap\u202C now' }] },
      'https://p.example',
    );
    expect(text).not.to.match(/[\u202A-\u202E\u2066-\u2069]/);
    expect(text).to.contain('⟨U+202E⟩');
  });
});
