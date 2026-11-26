/**
 * The query service's agents API as the agent-config transfer reaches it (QueryAgentDefinitions): the request each
 * operation sends with the caller's own credential, and how an answer that is not a success or not the expected shape
 * surfaces. A real HTTP server stands in for the query service.
 */
import { expect } from 'chai';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { QueryAgentDefinitions } from '../../../src/modules/agent-harness/adapters/query-agent-definitions';
import { registerUpstreams } from '../../../src/libs/http/upstream-call';
import { BadGatewayError } from '../../../src/libs/errors/http.errors';

const AGENT = {
  name: 'Support triage',
  description: '',
  startMessage: '',
  systemPrompt: '',
  instructions: null,
  tags: [],
  sendUserContext: true,
  webSearch: null,
  defaultReasoningEffort: null,
  engineProfile: null,
  models: [],
  roleModels: {},
  toolsets: [],
  mcpServers: [],
  knowledge: [],
  skills: [],
  gates: null,
};

interface Seen {
  method: string;
  url: string;
  authorization: string | undefined;
  body: unknown;
}

describe('QueryAgentDefinitions: the agents API of the query service', () => {
  let server: http.Server;
  let base = '';
  const seen: Seen[] = [];
  let answer: { status: number; body: unknown } = { status: 200, body: {} };

  before(async () => {
    server = http.createServer((req, res) => {
      let raw = '';
      req.on('data', (chunk: Buffer) => (raw += chunk.toString()));
      req.on('end', () => {
        seen.push({
          method: req.method ?? '',
          url: req.url ?? '',
          authorization: req.headers.authorization,
          body: raw ? JSON.parse(raw) : undefined,
        });
        res.writeHead(answer.status, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(answer.body));
      });
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    registerUpstreams({ query: [base] });
  });

  after(async () => {
    registerUpstreams({});
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  beforeEach(() => {
    seen.length = 0;
  });

  it('imports with the caller credential, asking for a sole-instance binding only when told to', async () => {
    answer = {
      status: 200,
      body: {
        agent: { _key: 'a2', name: 'Support triage' },
        bound: [{ kind: 'toolset', name: 'jira' }],
        rebind: [{ kind: 'mcp', name: 'gh', reason: 'missing', detail: 'no instance' }],
        warnings: ['w'],
      },
    };
    const definitions = new QueryAgentDefinitions(() => base);
    const imported = await definitions.import('Bearer t', AGENT as never, { bind: 'sole_instance' });
    expect(imported).to.deep.equal({
      agentKey: 'a2',
      name: 'Support triage',
      bound: [{ kind: 'toolset', name: 'jira' }],
      rebind: [{ kind: 'mcp', name: 'gh', reason: 'missing', detail: 'no instance' }],
      warnings: ['w'],
    });
    await definitions.import('Bearer t', AGENT as never);
    expect(seen.map((s) => [s.method, s.url, s.authorization])).to.deep.equal([
      ['POST', '/api/v1/agent/import', 'Bearer t'],
      ['POST', '/api/v1/agent/import', 'Bearer t'],
    ]);
    expect(seen[0]!.body).to.deep.equal({ agent: AGENT, bind: 'sole_instance' });
    expect(seen[1]!.body).to.deep.equal({ agent: AGENT });
  });

  it('removes an imported agent by its key, encoded into the path', async () => {
    answer = { status: 200, body: { status: 'success' } };
    await new QueryAgentDefinitions(() => base).remove('Bearer t', 'a/2');
    expect(seen.map((s) => [s.method, s.url])).to.deep.equal([['DELETE', '/api/v1/agent/a%2F2']]);
  });

  it('asks for one version of an export when given one', async () => {
    answer = { status: 200, body: { agent: AGENT, source: { agentVersion: 4, digest: null }, excluded: [] } };
    const exported = await new QueryAgentDefinitions(() => base).export('Bearer t', 'agent a', '4');
    expect(seen[0]!.url).to.equal('/api/v1/agent/agent%20a/export?version=4');
    expect(exported.source).to.deep.equal({ agentVersion: 4, digest: null });
  });

  it('a success whose body is not the agreed shape is a bad gateway, never passed on', async () => {
    answer = { status: 200, body: { agent: { _key: 'a2' } } };
    try {
      await new QueryAgentDefinitions(() => base).import('Bearer t', AGENT as never);
      expect.fail('an unexpected body must be refused');
    } catch (error) {
      expect(error).to.be.instanceOf(BadGatewayError);
    }
  });

  it("an answer that is not a success is the service's error, not a body to read", async () => {
    answer = { status: 404, body: { detail: 'Agent not found' } };
    try {
      await new QueryAgentDefinitions(() => base).remove('Bearer t', 'gone');
      expect.fail('a 404 must surface');
    } catch (error) {
      expect(error).to.be.instanceOf(Error);
      expect(error).not.to.be.instanceOf(BadGatewayError);
    }
  });

  it('a service that cannot be reached surfaces as an error of the operation', async () => {
    try {
      await new QueryAgentDefinitions(() => 'http://127.0.0.1:1').remove('Bearer t', 'a');
      expect.fail('an unreachable service must surface');
    } catch (error) {
      expect(error).to.be.instanceOf(Error);
    }
  });
});
