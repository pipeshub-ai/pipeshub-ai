import { expect } from 'chai';
import { event, ScriptedUpstream, textRun } from './scripted-upstream';

const readAll = async (res: Response): Promise<string> => {
  const reader = res.body?.getReader();
  if (!reader) return '';
  const decoder = new TextDecoder();
  let out = '';
  for (;;) {
    try {
      const { done, value } = await reader.read();
      if (done) return out;
      out += decoder.decode(value, { stream: true });
    } catch {
      return out;
    }
  }
};

const eventTypes = (sse: string): string[] =>
  sse
    .split('\n')
    .filter((line) => line.startsWith('event: '))
    .map((line) => line.slice('event: '.length));

describe('harness test kit: ScriptedUpstream', () => {
  let upstream: ScriptedUpstream;

  afterEach(async () => {
    await upstream.stop();
  });

  it('serves scripted AG-UI frames in order and records the request', async () => {
    upstream = new ScriptedUpstream([
      textRun({ threadId: 't1', runId: 'r1', chunks: ['Hel', 'lo'] }),
    ]);
    const base = await upstream.start();

    const res = await fetch(`${base}/api/v1/chat/stream`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: 'hi' }),
    });
    const body = await readAll(res);

    expect(res.headers.get('content-type')).to.equal('text/event-stream');
    expect(eventTypes(body)).to.deep.equal([
      'RUN_STARTED',
      'TEXT_MESSAGE_START',
      'TEXT_MESSAGE_CONTENT',
      'TEXT_MESSAGE_CONTENT',
      'TEXT_MESSAGE_END',
      'RUN_FINISHED',
    ]);
    expect(body).to.contain(
      'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"r1-msg","delta":"Hel"}',
    );
    expect(upstream.requests).to.have.length(1);
    expect(JSON.parse(upstream.requests[0]!.body)).to.deep.equal({
      query: 'hi',
    });
    expect(upstream.remaining).to.equal(0);
  });

  it('cuts the stream mid-response and reports exhaustion as 500', async () => {
    upstream = new ScriptedUpstream([
      {
        steps: [
          event('RUN_STARTED', { threadId: 't', runId: 'r' }),
          { kind: 'cut' },
        ],
      },
    ]);
    const base = await upstream.start();

    const cut = await readAll(
      await fetch(`${base}/stream`, { method: 'POST' }),
    );
    expect(eventTypes(cut)).to.deep.equal(['RUN_STARTED']);

    const exhausted = await fetch(`${base}/stream`, { method: 'POST' });
    expect(exhausted.status).to.equal(500);
  });
});
