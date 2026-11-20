/// <reference types="mocha" />
import { expect } from 'chai';
import express from 'express';
import { AddressInfo } from 'net';
import { Server } from 'http';

import {
  createChatCompletion,
  createResponse,
  listModels,
  retrieveModel,
} from '../../../src/modules/openai_compat/controller/openai_compat.controller';
import { chatModelsFrom, findChatModel } from '../../../src/modules/openai_compat/utils/chat-models';
import { answerFromFrames, readSSE } from '../../../src/modules/openai_compat/utils/chat-run';
import {
  citationsToAnnotations,
  conversationIdFromResponseId,
  newResponseId,
  responsesInputToTurns,
  turnsToQuery,
} from '../../../src/modules/openai_compat/utils/openai-format';

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

describe('openai_compat format', () => {
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

describe('openai_compat chat stream', () => {
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

const llm = (modelKey: string, provider: string, model: string, extra: Record<string, unknown> = {}) => ({
  modelKey,
  provider,
  configuration: { model },
  ...extra,
});

describe('openai_compat chat models', () => {
  it('lists one model per stored name, default first, with friendly names only for single-model entries', () => {
    const models = chatModelsFrom({
      llm: [
        llm('k-anthropic', 'anthropic', 'claude-sonnet-5', { modelFriendlyName: 'Sonnet' }),
        llm('k-openai', 'openAI', 'gpt-5, gpt-5-mini', { isDefault: true, modelFriendlyName: 'ignored' }),
      ],
      embedding: [llm('k-emb', 'openAI', 'text-embedding-3-small')],
    });
    expect(models.map((m) => [m.id, m.modelKey, m.friendlyName])).to.deep.equal([
      ['gpt-5', 'k-openai', ''],
      ['gpt-5-mini', 'k-openai', ''],
      ['claude-sonnet-5', 'k-anthropic', 'Sonnet'],
    ]);
  });

  it('qualifies a name configured twice, and resolves either form when unambiguous', () => {
    const models = chatModelsFrom({
      llm: [
        llm('k1aaaaaaaa', 'openAI', 'gpt-5'),
        llm('k2bbbbbbbb', 'azureOpenAI', 'gpt-5'),
        llm('k3cccccccc', 'azureOpenAI', 'gpt-5'),
        llm('k4dddddddd', 'gemini', 'gemini-3-pro'),
      ],
    });
    expect(models.map((m) => m.id)).to.deep.equal([
      'openAI/gpt-5',
      'azureOpenAI/gpt-5@k2bbbbbb',
      'azureOpenAI/gpt-5@k3cccccc',
      'gemini-3-pro',
    ]);
    expect(findChatModel(models, 'openAI/gpt-5')?.modelKey).to.equal('k1aaaaaaaa');
    expect(findChatModel(models, 'gemini/gemini-3-pro')?.modelKey).to.equal('k4dddddddd');
    expect(findChatModel(models, 'gpt-5')).to.equal(undefined);
    expect(findChatModel(models, 'azureOpenAI/gpt-5')).to.equal(undefined);
  });

  it('returns no models when nothing is configured', () => {
    expect(chatModelsFrom(null)).to.deep.equal([]);
    expect(chatModelsFrom({ llm: [{ provider: 'openAI', configuration: { model: 'x' } }] })).to.deep.equal([]);
  });
});

describe('openai_compat routes against a fake chat API', () => {
  let upstream: Server;
  let api: Server;
  let base = '';
  const calls: { path: string; body: any; auth?: string }[] = [];

  before(async () => {
    const fake = express();
    fake.use(express.json());
    fake.post(/\/api\/v1\/conversations(\/.*)?\/stream$/, (req, res) => {
      calls.push({ path: req.path, body: req.body, auth: req.headers.authorization });
      if (req.path.includes('ffffffffffffffffffffffff')) {
        res.status(404).json({ error: { code: 'HTTP_NOT_FOUND', message: 'Conversation not found' } });
        return;
      }
      if (req.body.query === 'boom') {
        res.status(500).json({ error: { code: 'INTERNAL', message: 'Chat backend down' } });
        return;
      }
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      res.write(frame('CUSTOM', { name: 'conversation_created', value: { conversationId: CONV } }));
      res.end(finished('Hello from the assistant'));
    });
    upstream = await new Promise<Server>((r) => {
      const s = fake.listen(0, '127.0.0.1', () => r(s));
    });
    const deps = {
      appConfig: {
        esBackend: `http://127.0.0.1:${(upstream.address() as AddressInfo).port}`,
        frontendUrl: 'https://app.test',
      } as any,
      readModels: async () => ({
        llm: [llm('key-gpt', 'openAI', 'gpt-5', { isDefault: true, modelFriendlyName: 'GPT 5', updatedAt: 2000 })],
      }),
    };
    const app = express();
    app.use(express.json());
    app.get('/models', listModels(deps));
    app.get('/models/*', retrieveModel(deps));
    app.post('/chat/completions', createChatCompletion(deps));
    app.post('/responses', createResponse(deps));
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

  it('lists the configured chat models', async () => {
    const body = await (await fetch(`${base}/models`)).json();
    expect(body).to.deep.equal({
      object: 'list',
      data: [{ id: 'gpt-5', object: 'model', created: 2000, owned_by: 'openAI', name: 'GPT 5' }],
    });
  });

  it('retrieves one model by id or provider/name, and 404s an unknown one', async () => {
    expect((await (await fetch(`${base}/models/gpt-5`)).json()).id).to.equal('gpt-5');
    expect((await (await fetch(`${base}/models/openAI/gpt-5`)).json()).id).to.equal('gpt-5');
    const res = await fetch(`${base}/models/nope`);
    expect(res.status).to.equal(404);
    expect((await res.json()).error.code).to.equal('model_not_found');
  });

  it('runs the assistant with the chosen model and the caller token', async () => {
    const res = await post('/chat/completions', { model: 'gpt-5', messages: [{ role: 'user', content: 'hi' }] });
    const body = await res.json();
    expect(res.status).to.equal(200);
    expect(body.object).to.equal('chat.completion');
    expect(body.model).to.equal('gpt-5');
    expect(body.choices[0].message.content).to.equal('Hello from the assistant');
    expect(calls[0]).to.deep.include({ path: '/api/v1/conversations/stream', auth: 'Bearer t' });
    expect(calls[0]!.body).to.deep.equal({
      query: 'hi',
      chatMode: 'agent',
      modelKey: 'key-gpt',
      modelName: 'gpt-5',
      modelProvider: 'openAI',
      modelFriendlyName: 'GPT 5',
    });
  });

  it('streams chat completion chunks ending in [DONE]', async () => {
    const text = await (await post('/chat/completions', {
      model: 'gpt-5',
      stream: true,
      messages: [{ role: 'user', content: 'hi' }],
    })).text();
    const data = text.split('\n\n').filter((b) => b.startsWith('data: ')).map((b) => b.slice(6));
    expect(data.at(-1)).to.equal('[DONE]');
    const parsed = data.slice(0, -1).map((d) => JSON.parse(d));
    expect(parsed[0].choices[0].delta.role).to.equal('assistant');
    expect(parsed.map((p) => p.choices[0].delta.content ?? '').join('')).to.equal('Hello from the assistant');
    expect(parsed.at(-1).choices[0].finish_reason).to.equal('stop');
  });

  it('answers an unknown model with model_not_found before starting a run', async () => {
    const res = await post('/chat/completions', { model: 'missing', messages: [{ role: 'user', content: 'hi' }] });
    const body = await res.json();
    expect(res.status).to.equal(404);
    expect(body.error).to.include({ code: 'model_not_found', param: 'model' });
    expect(calls).to.have.length(0);
  });

  it('passes the upstream status and message through when the run cannot start', async () => {
    const res = await post('/responses', { model: 'gpt-5', input: 'boom', stream: true });
    const body = await res.json();
    expect(res.status).to.equal(500);
    expect(body.error).to.include({ code: 'server_error', message: 'Chat backend down' });
  });

  it('rejects a request without model or user text', async () => {
    expect((await post('/chat/completions', { messages: [{ role: 'user', content: 'x' }] })).status).to.equal(400);
    expect((await post('/responses', { model: 'gpt-5', input: [] })).status).to.equal(400);
    expect(calls).to.have.length(0);
  });

  it('continues the conversation named by previous_response_id', async () => {
    const first = await (await post('/responses', { model: 'gpt-5', input: 'hi' })).json();
    expect(first.status).to.equal('completed');
    expect(first.output_text).to.equal('Hello from the assistant');
    expect(conversationIdFromResponseId(first.id)).to.equal(CONV);

    await post('/responses', { model: 'gpt-5', input: 'again', previous_response_id: first.id });
    expect(calls[1]!.path).to.equal(`/api/v1/conversations/${CONV}/messages/stream`);
    expect(calls[1]!.body).to.include({ query: 'again', modelKey: 'key-gpt', chatMode: 'agent' });
  });

  it('reports a missing previous conversation against previous_response_id, not model', async () => {
    const res = await post('/responses', {
      model: 'gpt-5',
      input: 'x',
      previous_response_id: newResponseId('ffffffffffffffffffffffff'),
    });
    const body = await res.json();
    expect(res.status).to.equal(404);
    expect(body.error).to.include({ code: 'previous_response_not_found', param: 'previous_response_id' });
  });

  it('refuses a previous_response_id this server did not issue', async () => {
    const res = await post('/responses', { model: 'gpt-5', input: 'x', previous_response_id: 'resp_nope' });
    expect(res.status).to.equal(400);
  });

  it('streams Responses events in order with sequence numbers', async () => {
    const text = await (await post('/responses', { model: 'gpt-5', input: 'hi', stream: true })).text();
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
    expect(events.at(-1).response.output[0].content[0].text).to.equal('Hello from the assistant');
  });
});
