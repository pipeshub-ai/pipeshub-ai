import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ADMIN, Harness, Reply, sessionToken, startHarness } from './connectors-http-harness'
import { createToolsetsRouter } from '../../../../src/modules/toolsets/routes/toolsets_routes'
import { createMcpServersRouter } from '../../../../src/modules/mcp_servers/routes/mcp_servers.routes'
import { createApiToolsetsRouter } from '../../../../src/modules/api_toolsets/routes/api_toolsets.routes'
import * as userAdminService from '../../../../src/modules/user_management/services/user-admin.service'
import { captureLogRecords, newRequestId, recordsOf, type LogRecord } from '../../../helpers/log-capture'

// An admin's toolset, MCP server and API toolset settings, saved by the connector service: one `config.*` audit line
// once Node read the service's 2xx, none for a refusal or a failure.

const ID = '3f2c9e7a-1b4d-4c8e-9a6f-2d5e8b7c1a90' // a toolset, toolset OAuth app or MCP server id (uuid4, as the service mints them)
const HEX = '3f2c9e7a1b4d4c8e9a6f2d5e8b7c1a90' // an API toolset id (uuid4().hex): not a PipesHub id
const AGENT = 'a1b2c3d4-0000-4000-8000-000000000001' // an agent key
const CREATED = '7d1e4b2a-9c3f-4e5d-8a6b-1f2e3d4c5b6a'
const SECRET = 'n5p-secret-marker-41d9'
const T = '/api/v1/toolsets'
const M = '/api/v1/mcp-servers'
const A = '/api/v1/api-toolsets'

interface Save {
  name: string
  method: string
  path: string
  body?: unknown
  /** The service's 2xx body. */
  saved?: unknown
  event: string
  reason: string | null
  target: string | null
  /** The path id the request context puts on every line. */
  context?: [string, string]
}

const SAVES: Save[] = [
  { name: 'T1 toolset create', method: 'POST', path: `${T}/instances`, body: { toolsetType: 'jira', instanceName: 'Jira', authType: 'API_TOKEN', authConfig: { apiToken: SECRET } }, saved: { status: 'success', instance: { _id: CREATED } }, event: 'config.toolset.create', reason: null, target: CREATED },
  { name: 'T2 toolset update', method: 'PUT', path: `${T}/instances/${ID}`, body: { instanceName: 'Jira 2', authConfig: { apiToken: SECRET } }, event: 'config.toolset.update', reason: 'settings', target: ID, context: ['toolset_id', ID] },
  { name: 'T3 toolset delete', method: 'DELETE', path: `${T}/instances/${ID}`, event: 'config.toolset.delete', reason: null, target: ID, context: ['toolset_id', ID] },
  { name: 'T4 toolset tool rules', method: 'PUT', path: `${T}/instances/${ID}/tool-rules`, body: { tools: { search: 'ask' } }, event: 'config.toolset.update', reason: 'tool_rules', target: ID, context: ['toolset_id', ID] },
  { name: 'T5 toolset OAuth app update', method: 'PUT', path: `${T}/oauth-configs/jira/${ID}`, body: { authConfig: { clientSecret: SECRET } }, event: 'config.toolset_oauth_app.update', reason: null, target: ID },
  { name: 'T6 toolset OAuth app delete', method: 'DELETE', path: `${T}/oauth-configs/jira/${ID}`, event: 'config.toolset_oauth_app.delete', reason: null, target: ID },
  { name: 'M1 MCP server create', method: 'POST', path: `${M}/instances`, body: { instanceName: 'MCP', serverType: 'custom', url: 'https://mcp.acme.test', clientSecret: SECRET }, saved: { _id: CREATED, name: 'MCP' }, event: 'config.mcp_server.create', reason: null, target: CREATED },
  { name: 'M2 MCP server update', method: 'PUT', path: `${M}/instances/${ID}`, body: { instanceName: 'MCP 2', clientSecret: SECRET }, event: 'config.mcp_server.update', reason: 'settings', target: ID, context: ['mcp_server_id', ID] },
  { name: 'M3 MCP server delete', method: 'DELETE', path: `${M}/instances/${ID}`, event: 'config.mcp_server.delete', reason: null, target: ID, context: ['mcp_server_id', ID] },
  { name: 'M4 MCP server logo set', method: 'PUT', path: `${M}/instances/${ID}/logo`, body: { logo: 'data:image/png;base64,AAAA' }, event: 'config.mcp_server.update', reason: 'logo', target: ID, context: ['mcp_server_id', ID] },
  { name: 'M5 MCP server logo remove', method: 'DELETE', path: `${M}/instances/${ID}/logo`, event: 'config.mcp_server.update', reason: 'logo', target: ID, context: ['mcp_server_id', ID] },
  { name: 'M6 MCP server OAuth app', method: 'PUT', path: `${M}/instances/${ID}/oauth-config`, body: { clientId: 'c', clientSecret: SECRET }, event: 'config.mcp_server.update', reason: 'oauth_app', target: ID, context: ['mcp_server_id', ID] },
  { name: 'A1 API toolset create', method: 'POST', path: `${A}/instances`, body: { name: 'Internal API', apiKey: SECRET }, saved: { id: HEX, name: 'Internal API' }, event: 'config.api_toolset.create', reason: null, target: null },
  { name: 'A2 API toolset update', method: 'PUT', path: `${A}/instances/${HEX}`, body: { name: 'Internal API 2', apiKey: SECRET }, event: 'config.api_toolset.update', reason: 'settings', target: null, context: ['toolset_id', HEX] },
  { name: 'A3 API toolset delete', method: 'DELETE', path: `${A}/instances/${HEX}`, event: 'config.api_toolset.delete', reason: null, target: null, context: ['toolset_id', HEX] },
  { name: 'A4 API toolset shared credential set', method: 'PUT', path: `${A}/instances/${HEX}/shared-credentials`, body: { apiKey: SECRET }, event: 'config.api_toolset.update', reason: 'shared_credential_set', target: null, context: ['toolset_id', HEX] },
  { name: 'A5 API toolset shared credential remove', method: 'DELETE', path: `${A}/instances/${HEX}/shared-credentials`, event: 'config.api_toolset.update', reason: 'shared_credential_removed', target: null, context: ['toolset_id', HEX] },
]
const save = (prefix: string): Save => SAVES.find((s) => s.name.startsWith(`${prefix} `))!

const pause = (ms: number) => new Promise((r) => setTimeout(r, ms))
/** Polls until `done()` or the deadline; the caller's assertion then says what is missing. */
const until = async (done: () => boolean, deadlineMs = 5000): Promise<void> => {
  const end = Date.now() + deadlineMs
  while (!done() && Date.now() < end) await pause(10)
}

describe('Toolset, MCP server and API toolset settings saved by the connector service: config audit lines', function () {
  this.timeout(30000)
  let h: Harness
  let records: LogRecord[]

  // The harness stubs the user lookups on the default sandbox, which each test restores: one harness per test.
  beforeEach(async () => {
    h = await startHarness({
      extraRouters: [
        { mountPath: T, create: createToolsetsRouter },
        { mountPath: M, create: createMcpServersRouter },
        { mountPath: A, create: createApiToolsetsRouter },
      ],
    })
    sinon.stub(userAdminService, 'isUserOrgAdmin').resolves(true)
    records = captureLogRecords()
  })
  afterEach(async () => {
    await h.close()
    sinon.restore()
  })

  const send = async (
    method: string,
    path: string,
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<{ id: string; status: number; lines: LogRecord[] }> => {
    const id = newRequestId('n5p2')
    let status = 0
    try {
      const res = await fetch(`${h.origin}${path}`, {
        method,
        headers: {
          authorization: `Bearer ${sessionToken(h, ADMIN)}`,
          'x-request-id': id,
          ...(body !== undefined && { 'content-type': 'application/json' }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal,
      })
      status = res.status
      await res.text()
    } catch (error) {
      // The client gave up (the abort case); status stays 0.
      if ((error as Error).name !== 'TimeoutError') throw error
    }
    // The access line is written on 'close', after the client read the answer.
    if (status !== 0) await until(() => recordsOf(records, id).some((r) => r.message === 'http.request.end'))
    return { id, status, lines: recordsOf(records, id) }
  }
  const auditLines = (lines: LogRecord[]) => lines.filter((r) => r.service === 'audit')
  const answer = (s: Save, reply: Parameters<Harness['backend']['on']>[2]) => h.backend.on(s.method, s.path, reply)

  for (const s of SAVES) {
    describe(`${s.name}: ${s.method} ${s.path}`, () => {
      it('a save the service answered 2xx is one audit line naming the admin, before the access line', async () => {
        answer(s, { status: 200, body: s.saved ?? { success: true } })
        const { status, lines } = await send(s.method, s.path, s.body)

        expect(status).to.equal(200)
        expect(h.backend.callsTo(s.method, s.path)).to.have.length(1)
        const [line, ...more] = auditLines(lines)
        expect(more, 'one audit line').to.have.length(0)
        expect(line).to.include({
          message: s.event,
          level: 'info',
          actor: ADMIN._id,
          org_id: ADMIN.orgId,
          target: s.target,
          outcome: 'success',
          reason: s.reason,
        })
        if (s.context) expect(line[s.context[0]], s.context[0]).to.equal(s.context[1])
        const access = lines.findIndex((r) => r.message === 'http.request.end')
        expect(access, 'access line').to.be.greaterThan(lines.indexOf(line))
        expect(JSON.stringify(lines)).to.not.contain(SECRET)
      })

      for (const reply of [{ status: 500, body: { detail: 'boom' } }, { status: 403, body: { detail: 'no' } }, 'drop'] as Reply[]) {
        it(`no audit line when the service answers ${typeof reply === 'string' ? 'nothing (connection dropped)' : reply.status}`, async () => {
          answer(s, reply)
          const { status, lines } = await send(s.method, s.path, s.body)

          expect(status).to.be.at.least(400)
          expect(h.backend.callsTo(s.method, s.path)).to.not.be.empty
          expect(auditLines(lines)).to.deep.equal([])
        })
      }
    })
  }

  for (const [prefix, saved, event] of [
    ['T1', { instance: { _id: 'admin@acme.test' } }, 'config.toolset.create'],
    ['M1', { _id: 'admin@acme.test' }, 'config.mcp_server.create'],
  ] as const) {
    const s = save(prefix)
    it(`${prefix}: a created id that is not a PipesHub id is never printed (target null, reason invalid_id)`, async () => {
      answer(s, { status: 200, body: saved })
      const { lines } = await send(s.method, s.path, s.body)

      expect(auditLines(lines).map((r) => [r.message, r.target, r.reason])).to.deep.equal([[event, null, 'invalid_id']])
      expect(JSON.stringify(lines)).to.not.contain('admin@acme.test')
    })
    it(`${prefix}: an answer without the created id still writes the line, with no target`, async () => {
      answer(s, { status: 200, body: { success: true } })
      const { lines } = await send(s.method, s.path, s.body)

      expect(auditLines(lines).map((r) => [r.message, r.target, r.reason])).to.deep.equal([[event, null, null]])
    })
  }

  for (const prefix of ['T2', 'M2', 'A2']) {
    const s = save(prefix)
    it(`${prefix}: a save whose client left before the service answered is still one audit line`, async () => {
      // The client leaves once the service has the save (not on a clock), while the service is still working.
      const client = new AbortController()
      answer(s, () => {
        client.abort(new DOMException('client left', 'TimeoutError'))
        return { status: 200, body: { success: true }, delayMs: 300 }
      })
      const { id, status } = await send(s.method, s.path, s.body, client.signal)
      await until(() => auditLines(recordsOf(records, id)).length > 0)

      expect(status, 'the client left').to.equal(0)
      expect(auditLines(recordsOf(records, id)).map((r) => [r.message, r.reason, r.target])).to.deep.equal([
        [s.event, s.reason, s.target],
      ])
    })
  }

  it('M2: a save the service made after Node stopped waiting (timeout) has no audit line, only the error and access lines', async () => {
    // The MCP proxy waits 90 s; Node's deadline instead expires once the service has the save (not on a clock).
    const deadline = new AbortController()
    sinon.stub(AbortSignal, 'timeout').returns(deadline.signal)
    const s = save('M2')
    let answeredAt = Infinity
    answer(s, () => {
      deadline.abort(new DOMException('deadline passed', 'TimeoutError'))
      answeredAt = Date.now() + 300
      return { status: 200, body: { success: true }, delayMs: 300 }
    })
    const { id, status } = await send(s.method, s.path, s.body)
    // Past the service's late answer (and Node reading it): nothing is written for it either.
    await until(() => Date.now() > answeredAt + 100)
    const lines = recordsOf(records, id)

    expect(status).to.equal(504)
    expect(h.backend.callsTo(s.method, s.path), 'a timed-out write is not sent again').to.have.length(1)
    expect(auditLines(lines)).to.deep.equal([])
    expect(lines.filter((r) => r.level === 'warning').map((r) => [r.message, r.error_class])).to.deep.equal([
      ['upstream.call.end', 'timeout'],
      ['http.request.error', 'timeout'],
      ['http.request.end', 'timeout'],
    ])
  })

  for (const [name, method, path, body] of [
    ['a read of the tool rules (the same handler as their save)', 'GET', `${T}/instances/${ID}/tool-rules`, undefined],
    ['a member saving their own toolset credentials', 'PUT', `${T}/instances/${ID}/credentials`, { auth: { apiToken: SECRET } }],
    ['a member saving their own MCP server credentials', 'PUT', `${M}/instances/${ID}/credentials`, { apiToken: SECRET }],
    ['a member removing their own MCP server credentials', 'DELETE', `${M}/instances/${ID}/credentials`, undefined],
    ['a member authenticating to an MCP server', 'POST', `${M}/instances/${ID}/authenticate`, { apiToken: SECRET }],
    ['a member re-authenticating to an MCP server', 'POST', `${M}/instances/${ID}/reauthenticate`, {}],
    ["saving an agent's own MCP server credentials", 'PUT', `${M}/agents/${AGENT}/instances/${ID}/credentials`, { apiToken: SECRET }],
    ['a member authenticating to a toolset', 'POST', `${T}/instances/${ID}/authenticate`, { auth: { apiToken: SECRET } }],
    ['a member saving their own MCP tool rules', 'PUT', `${M}/instances/${ID}/my-tool-rules`, { tools: { search: 'ask' } }],
    ['MCP OAuth discovery (an operation)', 'POST', `${M}/oauth/discover`, { url: 'https://mcp.acme.test' }],
    ['a member saving their own API toolset credentials', 'PUT', `${A}/instances/${HEX}/credentials`, { apiKey: SECRET }],
    ['a member removing their own API toolset credentials', 'DELETE', `${A}/instances/${HEX}/credentials`, undefined],
    ['an API toolset test call (an operation)', 'POST', `${A}/instances/${HEX}/test`, {}],
    ['an API toolset spec check (an operation)', 'POST', `${A}/validate`, {}],
  ] as const) {
    it(`out of scope: ${name} writes no audit line`, async () => {
      h.backend.on(method, path, { status: 200, body: { success: true } })
      const { status, lines } = await send(method, path, body)

      expect(status).to.equal(200)
      expect(h.backend.callsTo(method, path)).to.have.length(1)
      expect(auditLines(lines)).to.deep.equal([])
    })
  }
})
