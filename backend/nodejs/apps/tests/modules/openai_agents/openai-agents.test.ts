/// <reference types="mocha" />
import { expect } from 'chai';
import express from 'express';
import { AddressInfo } from 'net';
import { Server } from 'http';

import {
  createChatCompletion,
  createResponse,
  listModels,
} from '../../../src/modules/openai_agents/controller/openai_agents.controller';
import { answerFromFrames, readSSE } from '../../../src/modules/openai_agents/utils/agent-run';
import {
  citationsToAnnotations,
  conversationIdFromResponseId,
  newResponseId,
  responsesInputToTurns,
  turnsToQuery,
} from '../../../src/modules/openai_agents/utils/openai-format';

const CONV = '0123456789abcdef01234567';

const frame = (type: string, fields: Record<string, unknown> = {}) =>
  `event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`;

const finished = (content: string, citations: unknown[] = []) =>
  frame('RUN_FINISHED', {
    result: {
      conversation: {
        _id: CONV,
        messages: [
          { messageType: 'user_query', content: 'q' },
          { messageType: 'bot_response', content, citations },
        ],
      },
    },
  });

async function* chunks(...parts: string[]) {
  for (const part of parts) yield new TextEncoder().encode(part);
}

describe('openai_agents format', () => {
  it('passes a lone user turn through unchanged', () => {
    expect(turnsToQuery([{ role: 'user', text: 'hello' }])).to.equal('hello');
  });

  it('folds system and earlier turns into one transcript ending in the last user turn', () => {
    const q = turnsToQuery(
      [
        { role: 'system', text: 'be brief' },
        { role: 'user', text: 'first' },
        { role: 'assistant', text: 'answer one' },
        { role: 'user', text: 'second' },
      ],
      'extra rule',
    );
    expect(q).to.contain('extra rule\n\nbe brief');
    expect(q).to.contain('User: first\n\nAssistant: answer one');
    expect(q.endsWith('Current message:\nsecond')).to.equal(true);
  });

  it('returns an empty query when there is no user text', () => {
    expect(turnsToQuery([{ role: 'system', text: 'x' }])).to.equal('');
  });

  it('reads Responses input as a string or as message items with parts', () => {
    expect(responsesInputToTurns('hi')).to.deep.equal([{ role: 'user', text: 'hi' }]);
    expect(
      responsesInputToTurns([
        { type: 'message', role: 'user', content: [{ type: 'input_text', text: 'a' }] },
        { type: 'function_call_output', output: 'ignored' },
      ]),
    ).to.deep.equal([{ role: 'user', text: 'a' }]);
  });

  it('round-trips the conversation id through a response id and rejects foreign ids', () => {
    expect(conversationIdFromResponseId(newResponseId(CONV))).to.equal(CONV);
    expect(conversationIdFromResponseId('resp_abc')).to.equal(null);
    expect(conversationIdFromResponseId('resp_../../x_ab')).to.equal(null);
  });

  it('maps citations to one absolute url_citation per source', () => {
    const cite = (webUrl: string, recordName: string) => ({ citationData: { metadata: { webUrl, recordName } } });
    const out = citationsToAnnotations(
      [cite('/record/1', 'Doc'), cite('/record/1', 'Doc'), cite('https://x.test/a', 'A'), cite('', 'none')],
      'abc',
      'https://app.test/',
    );
    expect(out.map((a) => a.url)).to.deep.equal(['https://app.test/record/1', 'https://x.test/a']);
    expect(out[0]).to.include({ title: 'Doc', start_index: 0, end_index: 3 });
  });
});

describe('openai_agents agent stream', () => {
  it('splits frames across chunk boundaries and CRLF', async () => {
    const out: string[] = [];
    for await (const f of readSSE(chunks('event: A\r\ndata: {"x"', ':1}\r\n\r\nevent: B\ndata: 2\n\n'))) {
      out.push(`${f.event}=${f.data}`);
    }
    expect(out).to.deep.equal(['A={"x":1}', 'B=2']);
  });

  it('finds a frame boundary whose CRLF pair is split across chunks', async () => {
    const out: string[] = [];
    for await (const f of readSSE(chunks('data: 1\r\n\r', '\ndata: 2\r\n\r\n'))) {
      out.push(f.data);
    }
    expect(out).to.deep.equal(['1', '2']);
  });

  it('returns the root answer and ignores sub-agent finishes', async () => {
    const seen: string[] = [];
    const answer = await answerFromFrames(
      readSSE(
        chunks(
          frame('CUSTOM', { name: 'conversation_created', value: { conversationId: CONV } }),
          frame('RUN_FINISHED', { parentRunId: 'r1', result: { conversation: { _id: 'x', messages: [] } } }),
          frame('TEXT_MESSAGE_CONTENT', { delta: 'ignored' }),
          finished('final text', [{ citationData: { metadata: { webUrl: 'https://s.test', recordName: 'S' } } }]),
        ),
      ),
      '',
      null,
      (id) => seen.push(id),
    );
    expect(seen).to.deep.equal([CONV]);
    expect(answer.text).to.equal('final text');
    expect(answer.annotations).to.have.length(1);
  });

  it('throws on a root RUN_ERROR and on a stream with no answer', async () => {
    const run = (body: string) => answerFromFrames(readSSE(chunks(body)), '', null);
    let err: any = await run(frame('RUN_ERROR', { message: 'boom', code: 'x' })).catch((e) => e);
    expect(err.message).to.equal('boom');
    err = await run(frame('STATE_SNAPSHOT', {})).catch((e) => e);
    expect(err.message).to.contain('without an answer');
  });
});

describe('openai_agents routes against a fake agent API', () => {
  let upstream: Server;
  let api: Server;
  let base = '';
  const calls: { path: string; body: any; auth?: string }[] = [];

  before(async () => {
    const fake = express();
    fake.use(express.json());
    fake.get('/api/v1/agents', (req, res) => {
      calls.push({ path: req.path, body: req.query, auth: req.headers.authorization });
      res.json({
        agents: [{ _key: 'agent-1', name: 'HR', description: 'd', createdAtTimestamp: 2000 }],
        pagination: { hasNext: false },
      });
    });
    fake.get('/api/v1/agents/:agentKey', (req, res) => {
      if (req.params.agentKey === 'missing') {
        res.status(404).json({ error: { code: 'HTTP_NOT_FOUND', message: 'Agent not found' } });
        return;
      }
      res.json({ agent: { _key: req.params.agentKey } });
    });
    fake.post(/\/api\/v1\/agents\/([^/]+)\/conversations(\/.*)?\/stream$/, (req, res) => {
      calls.push({ path: req.path, body: req.body, auth: req.headers.authorization });
      if (req.path.includes('broken')) {
        res.status(500).json({ error: { code: 'INTERNAL', message: 'Agent backend down' } });
        return;
      }
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      res.write(frame('CUSTOM', { name: 'conversation_created', value: { conversationId: CONV } }));
      res.end(finished('Hello from the agent'));
    });
    upstream = await new Promise<Server>((r) => {
      const s = fake.listen(0, '127.0.0.1', () => r(s));
    });
    const appConfig = {
      esBackend: `http://127.0.0.1:${(upstream.address() as AddressInfo).port}`,
      frontendUrl: 'https://app.test',
    } as any;
    const app = express();
    app.use(express.json());
    app.get('/models', listModels(appConfig));
    app.post('/chat/completions', createChatCompletion(appConfig));
    app.post('/responses', createResponse(appConfig));
    api = await new Promise<Server>((r) => {
      const s = app.listen(0, '127.0.0.1', () => r(s));
    });
    base = `http://127.0.0.1:${(api.address() as AddressInfo).port}`;
  });

  after(() => {
    api.close();
    upstream.close();
  });

  beforeEach(() => {
    calls.length = 0;
  });

  const post = (path: string, body: unknown) =>
    fetch(`${base}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer t' },
      body: JSON.stringify(body),
    });

  it('lists agents as models with the caller token', async () => {
    const res = await fetch(`${base}/models`, { headers: { Authorization: 'Bearer t' } });
    const body = await res.json();
    expect(body.data).to.deep.equal([
      { id: 'agent-1', object: 'model', created: 2, owned_by: 'pipeshub', name: 'HR', description: 'd' },
    ]);
    expect(calls[0]!.auth).to.equal('Bearer t');
  });

  it('answers a chat completion and starts a quick-mode agent conversation', async () => {
    const res = await post('/chat/completions', { model: 'agent-1', messages: [{ role: 'user', content: 'hi' }] });
    const body = await res.json();
    expect(res.status).to.equal(200);
    expect(body.object).to.equal('chat.completion');
    expect(body.choices[0].message.content).to.equal('Hello from the agent');
    expect(calls[0]).to.deep.include({ path: '/api/v1/agents/agent-1/conversations/stream', auth: 'Bearer t' });
    expect(calls[0]!.body).to.deep.equal({ query: 'hi', chatMode: 'quick' });
  });

  it('streams chat completion chunks ending in [DONE]', async () => {
    const text = await (await post('/chat/completions', {
      model: 'agent-1',
      stream: true,
      messages: [{ role: 'user', content: 'hi' }],
    })).text();
    const data = text.split('\n\n').filter((b) => b.startsWith('data: ')).map((b) => b.slice(6));
    expect(data.at(-1)).to.equal('[DONE]');
    const parsed = data.slice(0, -1).map((d) => JSON.parse(d));
    expect(parsed[0].choices[0].delta.role).to.equal('assistant');
    expect(parsed.map((p) => p.choices[0].delta.content ?? '').join('')).to.equal('Hello from the agent');
    expect(parsed.at(-1).choices[0].finish_reason).to.equal('stop');
  });

  it('answers an unknown agent with model_not_found before starting a run', async () => {
    const res = await post('/chat/completions', { model: 'missing', messages: [{ role: 'user', content: 'hi' }] });
    const body = await res.json();
    expect(res.status).to.equal(404);
    expect(body.error).to.include({ code: 'model_not_found', param: 'model' });
    expect(calls).to.have.length(0);
  });

  it('passes the upstream status and message through when the run cannot start', async () => {
    const res = await post('/responses', { model: 'broken', input: 'hi', stream: true });
    const body = await res.json();
    expect(res.status).to.equal(500);
    expect(body.error).to.include({ code: 'server_error', message: 'Agent backend down' });
  });

  it('rejects a request without model or user text', async () => {
    expect((await post('/chat/completions', { messages: [{ role: 'user', content: 'x' }] })).status).to.equal(400);
    expect((await post('/responses', { model: 'agent-1', input: [] })).status).to.equal(400);
    expect(calls).to.have.length(0);
  });

  it('continues the conversation named by previous_response_id', async () => {
    const first = await (await post('/responses', { model: 'agent-1', input: 'hi' })).json();
    expect(first.status).to.equal('completed');
    expect(first.output_text).to.equal('Hello from the agent');
    expect(conversationIdFromResponseId(first.id)).to.equal(CONV);

    await post('/responses', { model: 'agent-1', input: 'again', previous_response_id: first.id });
    expect(calls[1]!.path).to.equal(`/api/v1/agents/agent-1/conversations/${CONV}/messages/stream`);
    expect(calls[1]!.body.query).to.equal('again');
  });

  it('refuses a previous_response_id this server did not issue', async () => {
    const res = await post('/responses', { model: 'agent-1', input: 'x', previous_response_id: 'resp_nope' });
    expect(res.status).to.equal(400);
  });

  it('streams Responses events in order with sequence numbers', async () => {
    const text = await (await post('/responses', { model: 'agent-1', input: 'hi', stream: true })).text();
    const events = text
      .split('\n\n')
      .filter((b) => b.includes('data: '))
      .map((b) => JSON.parse(b.slice(b.indexOf('data: ') + 6)));
    expect(events.map((e) => e.type)).to.deep.equal([
      'response.created',
      'response.in_progress',
      'response.output_item.added',
      'response.content_part.added',
      'response.output_text.delta',
      'response.output_text.done',
      'response.content_part.done',
      'response.output_item.done',
      'response.completed',
    ]);
    expect(events.map((e) => e.sequence_number)).to.deep.equal(events.map((_, i) => i));
    expect(events.at(-1).response.output[0].content[0].text).to.equal('Hello from the agent');
  });
});
