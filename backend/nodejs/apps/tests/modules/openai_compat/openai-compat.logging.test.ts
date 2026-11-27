/**
 * What a self-answered failure writes to the log (docs/logging.md "Routes that answer their own errors (Node)"): the OpenAI
 * routes, the skills passthrough, the artifacts proxy and the api-docs JSON route, each with the real request context,
 * access line, error middleware and Logger, against fake services over HTTP.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import winston from 'winston';
import { Container } from 'inversify';
import type { AddressInfo } from 'net';
import type { Server } from 'http';
import { Logger } from '../../../src/libs/services/logger.service';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context';
import { accessLog } from '../../../src/libs/middlewares/access-log.middleware';
import { registerUpstreams } from '../../../src/libs/http/upstream-call';
import { runWithRequestContext } from '../../../src/libs/context/request-context';
import { captureLogRecords, newRequestId, recordsOf } from '../../helpers/log-capture';
import { callApi } from '../../../src/modules/openai_compat/utils/pipeshub-api';
import {
  createResponse,
  createChatCompletion,
  listModels,
} from '../../../src/modules/openai_compat/controller/openai_compat.controller';
import { listSkills } from '../../../src/modules/skills/controller/skills.controller';
import { getArtifact } from '../../../src/modules/artifacts/controllers/artifacts.controller';
import { createApiDocsRouter } from '../../../src/modules/api-docs/docs.routes';
import { ApiDocsService } from '../../../src/modules/api-docs/docs.service';

type Row = Record<string, any>;
const MESSAGE = Symbol.for('message');
const CONV = '0123456789abcdef01234567';
const frame = (type: string, fields: Row = {}) => `event: ${type}\ndata: ${JSON.stringify({ type, ...fields })}\n\n`;
const created = frame('CUSTOM', { name: 'conversation_created', value: { conversationId: CONV } });
const finished = frame('RUN_FINISHED', {
  result: { conversation: { _id: CONV, messages: [{ messageType: 'bot_response', content: 'the answer', citations: [] }] } },
});
const runError = frame('RUN_ERROR', { code: 'model_overloaded', message: 'The model said no to alice@example.com' });
const freeTextRunError = frame('RUN_ERROR', { code: 'Rate limit for alice@example.com', message: 'slow down' });
const llm = { llm: [{ modelKey: 'k', provider: 'openAI', isDefault: true, configuration: { model: 'gpt-5' } }] };

const listen = (app: express.Express) =>
  new Promise<Server>((resolve) => {
    const s = app.listen(0, '127.0.0.1', () => resolve(s));
  });
const originOf = (s: Server) => `http://127.0.0.1:${(s.address() as AddressInfo).port}`;

describe('self-answered failures: one reason on the request’s lines', function () {
  this.timeout(20000);
  let records: Row[];
  // The request under test; a line from any other context is not this test's.
  let requestId: string;
  const root = () => (Logger.getInstance() as any).logger as winston.Logger;
  const render = (r: Row): string => {
    const fmt = root().transports.find((t) => t instanceof winston.transports.Console)!.format!;
    const first = root().format.transform({ ...r }, {});
    const out = first ? fmt.transform(first as any, {}) : undefined;
    return out ? String((out as any)[MESSAGE]) : '';
  };
  const level = (r: Row) => {
    const l = String(r.level).toLowerCase();
    return l === 'warning' ? 'warn' : l;
  };
  const mine = () => recordsOf(records, requestId);
  const named = (m: string) => mine().filter((r) => r.message === m);
  const access = async () => {
    for (let i = 0; i < 400 && named('http.request.end').length === 0; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    const lines = named('http.request.end');
    expect(lines, 'access line').to.have.length(1);
    return lines[0];
  };
  const errors = () => mine().filter((r) => level(r) === 'error');

  /** An app with the request layer around one route. */
  const withRequestLayer = (mount: (app: express.Express) => void) => {
    const app = express();
    // A request the test sends without an id runs as the test's request, so its lines can be told from any other's.
    app.use((req, _res, next) => {
      req.headers['x-request-id'] ??= requestId;
      next();
    });
    app.use(requestContextMiddleware);
    app.use(accessLog());
    app.use(express.json());
    mount(app);
    app.use(ErrorMiddleware.handleError());
    return app;
  };

  beforeEach(() => {
    records = captureLogRecords();
    requestId = newRequestId('openai-compat-logging');
  });
  afterEach(() => {
    sinon.restore();
    registerUpstreams({});
  });

  describe('openai_compat', () => {
    let chatApi: Server;
    let api: Server;
    let base = '';
    let mode = 'answer';
    let innerRequestId: string | undefined;
    let innerSeen: () => void = () => undefined;
    const servers: Server[] = [];

    before(async () => {
      const fake = express();
      fake.use(express.json());
      fake.get('/api/v1/knowledgeBase', (req, res) => {
        innerRequestId = req.headers['x-request-id'] as string | undefined;
        res.json({ knowledgeBases: [] });
      });
      fake.post(/\/api\/v1\/conversations(\/.*)?\/stream$/, (req, res) => {
        innerRequestId = req.headers['x-request-id'] as string | undefined;
        innerSeen();
        if (mode === 'down') {
          res.status(500).json({ error: { code: 'INTERNAL', message: 'Chat backend down' } });
          return;
        }
        if (mode === 'hang') return; // never answers: the client gives up first
        if (mode === 'no_body') {
          res.status(204).end();
          return;
        }
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        if (mode === 'drop_mid_body') {
          // The connection goes once the headers and the first frame are flushed (Node corks writes until the next tick, so
          // a destroy right after write() could close it before any answer): the reader sees undici's `terminated`.
          res.write(created, () => res.socket?.destroy());
          return;
        }
        res.write(created);
        if (mode === 'hang_after_headers') return;
        if (mode === 'no_answer') res.end();
        else if (mode === 'run_error') res.end(runError);
        else if (mode === 'run_error_free_text') res.end(freeTextRunError);
        else if (mode === 'run_error_then_answer') res.end(runError + finished);
        else res.end(finished);
      });
      chatApi = await listen(fake);
      servers.push(chatApi);
    });

    const mountApi = async (esBackend: string, readModels: () => Promise<unknown> = async () => llm) => {
      const deps = { appConfig: { esBackend, frontendUrl: 'https://app.test' } as any, readModels };
      const app = withRequestLayer((a) => {
        a.get('/api/v1/openai/models', listModels(deps) as any);
        a.post('/api/v1/openai/chat/completions', createChatCompletion(deps) as any);
        a.post('/api/v1/openai/responses', createResponse(deps) as any);
      });
      api = await listen(app);
      servers.push(api);
      base = `${originOf(api)}/api/v1/openai`;
    };

    beforeEach(async () => {
      mode = 'answer';
      innerRequestId = undefined;
      innerSeen = () => undefined;
      registerUpstreams({ nodejs: [originOf(chatApi)] });
      await mountApi(originOf(chatApi));
    });
    afterEach(async () => {
      api.closeAllConnections();
      await new Promise<void>((resolve) => api.close(() => resolve()));
    });
    after(() => {
      for (const s of servers) s.close();
    });

    const chat = (extra: Row = {}, init: RequestInit = {}) =>
      fetch(`${base}/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer t', 'x-request-id': requestId },
        body: JSON.stringify({ model: 'gpt-5', messages: [{ role: 'user', content: 'hi' }], ...extra }),
        ...init,
      });

    it('our own crash: one ERROR with the stack, the access line internal, the 502 answer unchanged', async () => {
      api.close();
      await mountApi(originOf(chatApi), async () => {
        throw new TypeError("Cannot read properties of undefined (reading 'llm')");
      });
      const res = await fetch(`${base}/models`);
      expect(res.status).to.equal(502);
      expect(((await res.json()) as Row).error.code).to.equal('server_error');
      const line = await access();
      expect(line.error_class).to.equal('internal');
      const failed = named('openai_compat.request.failed');
      expect(failed).to.have.length(1);
      expect(level(failed[0])).to.equal('error');
      expect(render(failed[0])).to.include("reading 'llm'").and.to.include('TypeError');
    });

    it('a client that leaves before the stream starts: cancelled, no ERROR line', async () => {
      mode = 'hang';
      const controller = new AbortController();
      innerSeen = () => controller.abort();
      await chat({}, { signal: controller.signal }).catch(() => undefined);
      const line = await access();
      // the access line's level follows the 502 that sendRunError still sets (WARNING); its class says what happened
      expect(line.error_class).to.equal('cancelled');
      expect(errors()).to.deep.equal([]);
      expect(named('openai_compat.request.failed')).to.deep.equal([]);
    });

    it('a client that leaves mid-stream: cancelled, no ERROR line', async () => {
      mode = 'hang_after_headers';
      const controller = new AbortController();
      const res = await chat({ stream: true }, { signal: controller.signal });
      const reader = res.body!.getReader();
      await reader.read();
      controller.abort();
      const line = await access();
      expect(line.error_class).to.equal('cancelled');
      expect(errors()).to.deep.equal([]);
      expect(named('openai_compat.request.failed')).to.deep.equal([]);
    });

    it('our own API unreachable: unavailable on the access line, no ERROR line', async () => {
      const closed = await listen(express());
      const origin = originOf(closed);
      await new Promise<void>((resolve) => closed.close(() => resolve()));
      registerUpstreams({ nodejs: [origin] });
      api.close();
      await mountApi(origin);
      const res = await chat();
      expect(res.status).to.equal(502);
      const line = await access();
      expect(line.error_class).to.equal('unavailable');
      expect(errors()).to.deep.equal([]);
      expect(named('upstream.call.end').length).to.be.greaterThan(0);
      // the request's own reason, as http.request.error would give it: class and connection code, no text
      const failed = named('openai_compat.request.failed');
      expect(failed.map(level)).to.deep.equal(['warn']);
      expect(failed[0]).to.include({ code: 'unavailable', network_code: 'ECONNREFUSED' });
    });

    it('our API drops the connection after it started answering: one WARNING, upstream_disconnected, no text', async () => {
      mode = 'drop_mid_body';
      const res = await chat();
      expect(res.status).to.equal(502);
      expect((await access()).error_class).to.equal('unavailable');
      const failed = named('openai_compat.request.failed');
      expect(failed.map(level)).to.deep.equal(['warn']);
      expect(failed[0]).to.include({ error_class: 'unavailable', code: 'upstream_disconnected', network_code: 'UND_ERR_SOCKET' });
      // the answer had started: the call's own line saw the 200, so this is not a refused or pre-response failure
      expect(named('upstream.call.end').map((r) => r.status)).to.deep.equal([200]);
      expect(records.map(render).join('\n')).to.not.include('terminated');
    });

    it('our API answers 204 with no body: one WARNING, though the status is not a 5xx', async () => {
      mode = 'no_body';
      await (await chat()).text();
      await access();
      const failed = named('openai_compat.request.failed');
      expect(failed.map(level)).to.deep.equal(['warn']);
      expect(failed[0].code).to.match(/^[a-z0-9_]+$/);
    });

    it('a stream that ends with no answer: one WARNING with its code', async () => {
      mode = 'no_answer';
      const res = await chat();
      expect(res.status).to.equal(502);
      expect((await access()).error_class).to.equal('upstream_5xx');
      const failed = named('openai_compat.request.failed');
      expect(failed.map(level)).to.deep.equal(['warn']);
      expect(failed[0].code).to.equal('server_error');
    });

    it('a RUN_ERROR, also when an answer follows it: one WARNING with the AI service’s code, never its text', async () => {
      for (const m of ['run_error', 'run_error_then_answer']) {
        records.length = 0;
        mode = m;
        const res = await chat();
        expect(res.status, m).to.equal(502);
        await access();
        const failed = named('openai_compat.request.failed');
        expect(failed.map(level), m).to.deep.equal(['warn']);
        expect(failed[0].code, m).to.equal('model_overloaded');
        expect(records.map(render).join('\n'), m).to.not.include('alice@example.com');
      }
    });

    it('a RUN_ERROR after the stream started: the 200 stream’s access line says failure, with one WARNING', async () => {
      mode = 'run_error';
      const res = await chat({ stream: true });
      expect(res.status).to.equal(200);
      expect(await res.text()).to.include('model_overloaded');
      const line = await access();
      expect(line.outcome).to.equal('failure');
      expect(line.error_class).to.equal('upstream_5xx');
      expect(named('openai_compat.request.failed').map(level)).to.deep.equal(['warn']);
    });

    it('a RUN_ERROR inside a Responses stream: the 200 stream’s access line says failure, with one WARNING', async () => {
      mode = 'run_error';
      const res = await fetch(`${base}/responses`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer t' },
        body: JSON.stringify({ model: 'gpt-5', input: 'hi', stream: true }),
      });
      expect(res.status).to.equal(200);
      expect(await res.text()).to.include('response.failed');
      const line = await access();
      expect(line.outcome).to.equal('failure');
      expect(line.error_class).to.equal('upstream_5xx');
      expect(named('openai_compat.request.failed').map(level)).to.deep.equal(['warn']);
    });

    it('a RUN_ERROR code in free text is logged as unknown_error, never as written', async () => {
      mode = 'run_error_free_text';
      const res = await chat();
      expect(res.status).to.equal(502);
      await access();
      const failed = named('openai_compat.request.failed');
      expect(failed).to.have.length(1);
      expect(failed[0].code).to.equal('unknown_error');
      expect(records.map(render).join('\n')).to.not.include('alice@example.com');
    });

    it('callApi carries the request id to our own API, through upstreamFetch', async () => {
      await runWithRequestContext({ rootId: requestId } as any, () =>
        callApi({ baseUrl: originOf(chatApi), authorization: 'Bearer t' }, '/knowledgeBase'),
      );
      expect(innerRequestId).to.equal(requestId);
      const calls = named('upstream.call.end');
      expect(calls).to.have.length(1);
      expect(calls[0].upstream).to.equal('nodejs');
      // only registered PipesHub services: an unknown base is refused before anything is sent
      let refused: unknown;
      await callApi({ baseUrl: 'http://127.0.0.1:9', authorization: 'Bearer t' }, '/knowledgeBase').catch((e) => {
        refused = e;
      });
      expect(String(refused)).to.include('Blocked request');
    });

    it('our own API answered the failure (a 500): no line of its own, the access line says the class', async () => {
      mode = 'down';
      const res = await chat();
      expect(res.status).to.equal(500);
      expect((await access()).error_class).to.equal('upstream_5xx');
      expect(named('openai_compat.request.failed')).to.deep.equal([]);
      expect(errors()).to.deep.equal([]);
    });

    it('the inner request carries the outer request’s id, and the call writes upstream.call.end', async () => {
      const res = await chat();
      expect(res.status).to.equal(200);
      await access();
      expect(innerRequestId).to.equal(requestId);
      const calls = named('upstream.call.end');
      expect(calls).to.have.length(1);
      expect(calls[0].upstream).to.equal('nodejs');
    });
  });

  describe('skills passthrough', () => {
    let skills: Server;
    let status = 500;
    let app: Server;
    before(async () => {
      const fake = express();
      fake.get('/api/v1/skills/', (_req, res) => {
        res.status(status).json({ detail: 'skills store is down' });
      });
      skills = await listen(fake);
    });
    after(() => skills.close());
    afterEach(async () => {
      app.closeAllConnections();
      await new Promise<void>((resolve) => app.close(() => resolve()));
    });

    for (const [code, cls] of [[500, 'upstream_5xx'], [503, 'unavailable'], [404, 'not_found']] as const) {
      it(`a ${code} passed through: same status and body, access line ${cls}`, async () => {
        status = code;
        app = await listen(withRequestLayer((a) => a.get('/api/v1/skills', listSkills({ aiBackend: originOf(skills) } as any) as any)));
        const res = await fetch(`${originOf(app)}/api/v1/skills`);
        expect(res.status).to.equal(code);
        expect(await res.json()).to.deep.equal({ detail: 'skills store is down' });
        expect((await access()).error_class).to.equal(cls);
      });
    }
  });

  describe('artifacts proxy', () => {
    let connector: Server;
    let status = 404;
    let app: Server;
    before(async () => {
      const fake = express();
      fake.get('/api/v1/artifacts/:id', (_req, res) => {
        res.status(status).json({ detail: 'Artifact not found' });
      });
      connector = await listen(fake);
    });
    after(() => connector.close());
    afterEach(async () => {
      app.closeAllConnections();
      await new Promise<void>((resolve) => app.close(() => resolve()));
    });

    for (const code of [404, 500]) {
      it(`a ${code} from the connector service is logged once, by the error middleware`, async () => {
        status = code;
        registerUpstreams({ connector: [originOf(connector)] });
        app = await listen(
          withRequestLayer((a) => {
            a.use((req, _res, next) => {
              (req as any).user = { userId: '64b0000000000000000000a1', orgId: '64b000000000000000000a01' };
              next();
            });
            a.get('/api/v1/artifacts/:artifactId', getArtifact({ connectorBackend: originOf(connector) } as any) as any);
          }),
        );
        const res = await fetch(`${originOf(app)}/api/v1/artifacts/a1`);
        expect(res.status).to.equal(code === 404 ? 404 : 502);
        await access();
        const failureLines = mine().filter((r) => level(r) !== 'debug' && r.message !== 'http.request.end' && r.message !== 'upstream.call.end');
        expect(failureLines.map((r) => r.message)).to.deep.equal(['http.request.error']);
      });
    }
  });

  describe('api-docs /json', () => {
    it('a failure to build the docs is one ERROR on the logger, not console.error; the answer is unchanged', async () => {
      const consoleError = sinon.stub(console, 'error');
      const container = new Container();
      container.bind(ApiDocsService).toConstantValue({
        getUnifiedDocs: () => {
          throw new Error('spec missing');
        },
      } as unknown as ApiDocsService);
      const app = await listen(withRequestLayer((a) => a.use('/api/v1/docs', createApiDocsRouter(container))));
      try {
        const res = await fetch(`${originOf(app)}/api/v1/docs/json`);
        expect(res.status).to.equal(500);
        expect(await res.json()).to.deep.equal({ error: 'Failed to load API documentation', message: 'spec missing' });
        await access();
        expect(consoleError.called).to.equal(false);
        expect(named('api_docs.load_failed').map(level)).to.deep.equal(['error']);
      } finally {
        app.closeAllConnections();
        app.close();
      }
    });
  });
});
