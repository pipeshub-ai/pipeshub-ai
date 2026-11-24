import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { SlackNativeStatus } from '../../../src/integrations/slack-bot/src/utils/native-status';
import { createSlackAGUIEventHandler } from '../../../src/integrations/slack-bot/src/utils/agui-stream';

describe('Slack native activity status', () => {
  it('starts rotating status in the correct thread, updates with real progress and clears', async () => {
    const api = sinon.stub().resolves({ ok: true });
    const status = new SlackNativeStatus(api, 'C123', '123.456');
    expect(await status.start()).to.equal(true);
    expect(api.firstCall.args[1]).to.include({ channel_id: 'C123', thread_ts: '123.456' });
    expect(api.firstCall.args[1].loading_messages).to.have.length.greaterThan(1);
    expect(await status.update('Searching... 3/10')).to.equal(true);
    expect(api.secondCall.args[1].status).to.equal('is searching... 3/10');
    await status.update('Thinking...');
    expect(api.thirdCall.args[1].loading_messages).to.have.length.greaterThan(1);
    await status.clear();
    expect(api.getCall(3).args[1].status).to.equal('');
  });

  it('lets the caller fall back when Slack does not support native status', async () => {
    const api = sinon.stub().rejects(new Error('not supported'));
    const status = new SlackNativeStatus(api, 'C123', '123.456');
    expect(await status.start()).to.equal(false);
    expect(await status.update('Thinking...')).to.equal(false);
    await status.clear();
    expect(api.callCount).to.equal(1);
  });

  it('signals a failed update so the caller can switch to the message fallback', async () => {
    const api = sinon.stub();
    api.onFirstCall().resolves({ ok: true });
    api.onSecondCall().resolves({ ok: false });
    const status = new SlackNativeStatus(api, 'C123', '123.456');
    expect(await status.start()).to.equal(true);
    expect(await status.update('Searching...')).to.equal(false);
  });

  it('updates the visible status from actual tool-progress events', async () => {
    const api = sinon.stub().resolves({ ok: true });
    const status = new SlackNativeStatus(api, 'C123', '123.456');
    await status.start();
    const updates: Promise<boolean>[] = [];
    const handle = createSlackAGUIEventHandler({ onStatus: (message) => { updates.push(status.update(message)); } });
    handle({ event: 'STATE_SNAPSHOT', data: { type: 'STATE_SNAPSHOT', snapshot: { status: 'running_tool', current_tool: 'web_search', progress: 3, total: 10 } } });
    await Promise.all(updates);
    expect(api.lastCall.args[1].status).to.contain('3/10');
    await status.clear();
  });
});
