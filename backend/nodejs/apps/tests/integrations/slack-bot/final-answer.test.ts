import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  FAILED_RESPONSE_GENERATION_MESSAGE,
  replaceStreamedAnswer,
  slackBlocksFallbackText,
  type TypedSlackClient,
} from '../../../src/integrations/slack-bot/src/helpers';

const BLOCKS = [
  { type: 'section', text: { type: 'mrkdwn', text: '*Revenue* grew 12%.' } },
  {
    type: 'rich_text',
    elements: [{ type: 'rich_text_section', elements: [{ type: 'link', url: 'https://x.test', text: ' Q3 report' }] }],
  },
];

function client(postMessage = sinon.stub().resolves({ ts: '9.9' })) {
  return {
    users: { info: sinon.stub() },
    chat: { postMessage, update: sinon.stub().resolves({}) },
    apiCall: sinon.stub().resolves({ ok: true }),
  };
}

describe('slack-bot final answer', () => {
  beforeEach(() => sinon.stub(console, 'error'));
  afterEach(() => sinon.restore());

  describe('slackBlocksFallbackText', () => {
    it('joins the text of section and rich text blocks', () => {
      expect(slackBlocksFallbackText(BLOCKS)).to.equal('*Revenue* grew 12%. Q3 report');
    });

    it('is never empty', () => {
      expect(slackBlocksFallbackText([{ type: 'divider' }])).to.equal('Reply');
    });
  });

  describe('replaceStreamedAnswer', () => {
    it('posts the blocks with fallback text and deletes the streamed messages, without chat.update', async () => {
      const c = client();

      const sent = await replaceStreamedAnswer(c as unknown as TypedSlackClient, 'C1', '1.1', ['2.1', '2.2'], BLOCKS);

      expect(sent).to.equal(true);
      expect(c.chat.update.called, 'chat.update hits block_mismatch on a streamed message').to.equal(false);
      const posted = c.chat.postMessage.firstCall.args[0];
      expect(posted).to.include({ channel: 'C1', thread_ts: '1.1', text: '*Revenue* grew 12%. Q3 report' });
      expect(posted.blocks).to.equal(BLOCKS);
      expect(c.apiCall.getCalls().map((call) => call.args)).to.deep.equal([
        ['chat.delete', { channel: 'C1', ts: '2.1' }],
        ['chat.delete', { channel: 'C1', ts: '2.2' }],
      ]);
      expect(c.chat.postMessage.calledBefore(c.apiCall)).to.equal(true);
    });

    it('keeps the streamed messages and says the answer failed when the post fails', async () => {
      const postMessage = sinon.stub();
      postMessage.onFirstCall().rejects(new Error('invalid_blocks'));
      postMessage.resolves({ ts: '9.9' });
      const c = client(postMessage);

      const sent = await replaceStreamedAnswer(c as unknown as TypedSlackClient, 'C1', '1.1', ['2.1'], BLOCKS);

      expect(sent).to.equal(false);
      expect(c.apiCall.called).to.equal(false);
      expect(postMessage.secondCall.args[0]).to.include({
        channel: 'C1',
        thread_ts: '1.1',
        text: FAILED_RESPONSE_GENERATION_MESSAGE,
      });
      expect(postMessage.secondCall.args[0]).to.not.have.property('blocks');
    });

    it('still reports success when a streamed message is already gone', async () => {
      const c = client();
      c.apiCall.rejects(Object.assign(new Error('gone'), { data: { error: 'message_not_found' } }));

      const sent = await replaceStreamedAnswer(c as unknown as TypedSlackClient, 'C1', '1.1', ['2.1'], BLOCKS);

      expect(sent).to.equal(true);
    });
  });
});
