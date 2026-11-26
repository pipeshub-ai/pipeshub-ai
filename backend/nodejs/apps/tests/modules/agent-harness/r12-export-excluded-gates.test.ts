/**
 * Round 12 (WP-52 x WP-51): an agent's own run gates do not travel with its export yet (queued for WP-56), and an
 * export says so instead of dropping them silently. The query service names them in `excluded`; Node takes every
 * kind the agent-config contract names, so the export answers rather than failing on the new kind.
 *
 * R12-AX-1 the query service's export naming `gates` reaches the document as excluded.
 */
import { expect } from 'chai';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { QueryAgentDefinitions } from '../../../src/modules/agent-harness/adapters/query-agent-definitions';
import { registerUpstreams } from '../../../src/libs/http/upstream-call';

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

describe('R12-AX: an export names the agent gates it leaves out', () => {
  let server: http.Server;
  let base = '';

  before(async () => {
    server = http.createServer((_req, res) => {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(
        JSON.stringify({
          agent: AGENT,
          source: { agentVersion: 2, digest: null },
          excluded: ['gates', 'sharing'],
        }),
      );
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    registerUpstreams({ query: [base] });
  });

  after(async () => {
    registerUpstreams({});
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  it('R12-AX-1: the query service naming gates is taken, never refused as a bad gateway', async () => {
    const exported = await new QueryAgentDefinitions(() => base).export('Bearer t', 'agent-a', undefined);
    expect(exported.excluded).to.deep.equal(['gates', 'sharing']);
  });
});
