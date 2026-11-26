import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ADMIN, Harness, Reply, sessionToken, startHarness } from './connectors-http-harness'
import { createOAuthRouter } from '../../../../src/modules/tokens_manager/routes/oauth.routes'
import * as userAdminService from '../../../../src/modules/user_management/services/user-admin.service'
import { captureLogRecords, newRequestId, recordsOf, type LogRecord } from '../../../helpers/log-capture'
import { RECONCILE_LOG_MESSAGE } from '../../../../src/modules/crawling_manager/services/connector_schedule_orchestrator'

// An admin's connector and connector OAuth app settings, saved by the connector service: one `config.*` audit line
// once Node read the service's 2xx, none for a refusal or a failure.

const ID = '3f2c9e7a-1b4d-4c8e-9a6f-2d5e8b7c1a90' // a connector or OAuth app id (uuid4, as the service mints them)
const CREATED = '7d1e4b2a-9c3f-4e5d-8a6b-1f2e3d4c5b6a'
const SECRET = 'n5p-secret-marker-41d9'
const C = '/api/v1/connectors'
const O = '/api/v1/oauth'

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
}

const SAVES: Save[] = [
  { name: 'C1 create', method: 'POST', path: `${C}/`, body: { connectorType: 'Jira', instanceName: 'Jira', scope: 'team', config: { auth: { clientSecret: SECRET } } }, saved: { success: true, connector: { connectorId: CREATED } }, event: 'config.connector.create', reason: null, target: CREATED },
  { name: 'C2 config', method: 'PUT', path: `${C}/${ID}/config`, body: { auth: { clientSecret: SECRET } }, event: 'config.connector.update', reason: 'settings', target: ID },
  { name: 'C3 config/auth', method: 'PUT', path: `${C}/${ID}/config/auth`, body: { auth: { clientSecret: SECRET } }, event: 'config.connector.update', reason: 'auth', target: ID },
  { name: 'C4 config/filters-sync', method: 'PUT', path: `${C}/${ID}/config/filters-sync`, body: { filters: {} }, event: 'config.connector.update', reason: 'filters_sync', target: ID },
  { name: 'C5 filters (deprecated)', method: 'POST', path: `${C}/${ID}/filters`, body: { filters: {} }, event: 'config.connector.update', reason: 'filters_sync', target: ID },
  { name: 'C6 name', method: 'PUT', path: `${C}/${ID}/name`, body: { instanceName: 'Renamed' }, event: 'config.connector.update', reason: 'rename', target: ID },
  { name: 'C7 toggle', method: 'POST', path: `${C}/${ID}/toggle`, body: { type: 'agent', enabled: true }, event: 'config.connector.update', reason: 'agent_enable', target: ID },
  { name: 'C8 permission filter', method: 'PATCH', path: `${C}/${ID}/permissions/filter`, body: { mode: 'scopes' }, event: 'config.connector.update', reason: 'permission_filter', target: ID },
  { name: 'C9 delete', method: 'DELETE', path: `${C}/${ID}`, event: 'config.connector.delete', reason: null, target: ID },
  { name: 'O1 OAuth app create', method: 'POST', path: `${O}/Jira`, body: { oauthInstanceName: 'Jira app', config: { clientSecret: SECRET } }, saved: { success: true, oauthConfig: { _id: CREATED } }, event: 'config.connector_oauth_app.create', reason: null, target: CREATED },
  { name: 'O2 OAuth app update', method: 'PUT', path: `${O}/Jira/${ID}`, body: { config: { clientSecret: SECRET } }, event: 'config.connector_oauth_app.update', reason: null, target: ID },
  { name: 'O3 OAuth app delete', method: 'DELETE', path: `${O}/Jira/${ID}`, event: 'config.connector_oauth_app.delete', reason: null, target: ID },
]

const pause = (ms: number) => new Promise((r) => setTimeout(r, ms))
/** Polls until `done()` or the deadline; the caller's assertion then says what is missing. */
const until = async (done: () => boolean, deadlineMs = 5000): Promise<void> => {
  const end = Date.now() + deadlineMs
  while (!done() && Date.now() < end) await pause(10)
}

describe('Connector settings saved by the connector service: config audit lines', function () {
  this.timeout(30000)
  let h: Harness
  let records: LogRecord[]

  // The harness stubs the user lookups on the default sandbox, which each test restores: one harness per test.
  beforeEach(async () => {
    h = await startHarness({ extraRouters: [{ mountPath: O, create: createOAuthRouter }] })
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
    const id = newRequestId('n5p')
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
    // The access line is written on 'close'.
    await pause(20)
    return { id, status, lines: recordsOf(records, id) }
  }
  const auditLines = (lines: LogRecord[]) => lines.filter((r) => r.service === 'audit')
  const answer = (s: Save, reply: Reply) => h.backend.on(s.method, s.path, reply)

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
        // The path's connector id is on every line of the request, the audit line included.
        if (s.path.startsWith(`${C}/${ID}`)) expect(line.connector_id).to.equal(ID)
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

  describe('C7 toggle names the direction it saved', () => {
    const toggle = `${C}/${ID}/toggle`
    const cases: Array<[Record<string, unknown>, string]> = [
      [{ type: 'agent', enabled: true }, 'agent_enable'],
      [{ type: 'agent', enabled: false }, 'agent_disable'],
      [{ type: 'sync', enabled: true }, 'sync_enable'],
      [{ type: 'sync', enabled: false }, 'sync_disable'],
      [{ type: 'agent' }, 'toggle'],
      [{ type: 'sync' }, 'toggle'],
    ]
    for (const [body, reason] of cases) {
      it(`${JSON.stringify(body)} -> reason ${reason}`, async () => {
        // A sync toggle reads the connector first, and after the save its config, for the schedule.
        h.backend.on('GET', `${C}/${ID}`, { status: 200, body: { connector: { _key: ID, type: 'Jira', isActive: false } } })
        h.backend.on('GET', `${C}/${ID}/config`, { status: 200, body: { success: true, config: { type: 'Jira', isActive: false, config: { sync: null } } } })
        h.backend.on('POST', toggle, { status: 200, body: { success: true } })
        const { id, status, lines } = await send('POST', toggle, body)
        // The schedule reconcile runs after the answer; let it end before the harness closes.
        if (body.type === 'sync') {
          await until(() => recordsOf(records, id).some((r) => r.message === RECONCILE_LOG_MESSAGE))
          expect(recordsOf(records, id).filter((r) => r.message === RECONCILE_LOG_MESSAGE).map((r) => r.outcome)).to.deep.equal(['success'])
        }

        expect(status).to.equal(200)
        expect(auditLines(lines).map((r) => [r.message, r.reason, r.target])).to.deep.equal([
          ['config.connector.update', reason, ID],
        ])
      })
    }
  })

  it('C7: the Local FS sync refusal Node answers itself (409) writes no audit line', async () => {
    const toggle = `${C}/${ID}/toggle`
    h.backend.on('GET', `${C}/${ID}`, { status: 200, body: { connector: { _key: ID, type: 'Local FS', isActive: false, createdBy: ADMIN._id } } })
    h.backend.on('POST', toggle, { status: 200, body: { success: true } })
    const { status, lines } = await send('POST', toggle, { type: 'sync', enabled: true })

    expect(status).to.equal(409)
    expect(h.backend.callsTo('POST', toggle)).to.have.length(0)
    expect(auditLines(lines)).to.deep.equal([])
  })

  it('C8: the 409 refusal that lists what blocks the switch writes no audit line', async () => {
    const path = `${C}/${ID}/permissions/filter`
    h.backend.on('PATCH', path, { status: 409, body: { detail: { error: 'not_switchable', blockers: [] } } })
    const { status, lines } = await send('PATCH', path, { mode: 'scopes' })

    expect(status).to.equal(409)
    expect(auditLines(lines)).to.deep.equal([])
  })

  for (const [name, path, body, saved] of [
    ['C1', `${C}/`, SAVES[0].body, { connector: { connectorId: 'admin@acme.test' } }],
    ['O1', `${O}/Jira`, SAVES[9].body, { oauthConfig: { _id: 'admin@acme.test' } }],
  ] as const) {
    it(`${name}: a created id that is not a PipesHub id is never printed (target null, reason invalid_id)`, async () => {
      h.backend.on('POST', path, { status: 200, body: saved })
      const { lines } = await send('POST', path, body)

      expect(auditLines(lines).map((r) => [r.target, r.reason])).to.deep.equal([[null, 'invalid_id']])
      expect(JSON.stringify(lines)).to.not.contain('admin@acme.test')
    })
    it(`${name}: an answer without the created id still writes the line, with no target`, async () => {
      h.backend.on('POST', path, { status: 200, body: { success: true } })
      const { lines } = await send('POST', path, body)

      expect(auditLines(lines).map((r) => [r.message, r.target, r.reason])).to.have.deep.members([
        [name === 'C1' ? 'config.connector.create' : 'config.connector_oauth_app.create', null, null],
      ])
    })
  }

  it('a save whose client left before the service answered is still one audit line', async () => {
    const path = `${C}/${ID}/name`
    // The client leaves once the service has the save (not on a clock), while the service is still working.
    const client = new AbortController()
    h.backend.on('PUT', path, () => {
      client.abort(new DOMException('client left', 'TimeoutError'))
      return { status: 200, body: { success: true }, delayMs: 300 }
    })
    const { id, status } = await send('PUT', path, { instanceName: 'Renamed' }, client.signal)
    await until(() => auditLines(recordsOf(records, id)).length > 0)

    expect(status, 'the client left').to.equal(0)
    expect(auditLines(recordsOf(records, id)).map((r) => [r.message, r.reason, r.target])).to.deep.equal([
      ['config.connector.update', 'rename', ID],
    ])
  })

  it('a save the service made after Node stopped waiting (timeout) has no audit line, only the error and access lines', async () => {
    // The toggle waits 60 s; Node's deadline instead expires once the service has the save (not on a clock).
    const deadline = new AbortController()
    sinon.stub(AbortSignal, 'timeout').returns(deadline.signal)
    const path = `${C}/${ID}/toggle`
    let answeredAt = Infinity
    h.backend.on('POST', path, () => {
      deadline.abort(new DOMException('deadline passed', 'TimeoutError'))
      answeredAt = Date.now() + 300
      return { status: 200, body: { success: true }, delayMs: 300 }
    })
    const { id, status } = await send('POST', path, { type: 'agent', enabled: false })
    // Past the service's late answer (and Node reading it): nothing is written for it either.
    await until(() => Date.now() > answeredAt + 100)
    const lines = recordsOf(records, id)

    expect(status).to.equal(504)
    expect(h.backend.callsTo('POST', path), 'a timed-out request is not sent again').to.have.length(1)
    expect(auditLines(lines)).to.deep.equal([])
    expect(lines.filter((r) => r.level === 'warning').map((r) => [r.message, r.error_class])).to.deep.equal([
      ['upstream.call.end', 'timeout'],
      ['http.request.error', 'timeout'],
      ['http.request.end', 'timeout'],
    ])
  })

  for (const [name, method, path] of [
    ['an admin removing a user\'s connection (an action on a person)', 'DELETE', `${C}/${ID}/user-access/users/${ADMIN._id}`],
    ['a read of the config', 'GET', `${C}/${ID}/config`],
  ] as const) {
    it(`out of scope: ${name} writes no audit line`, async () => {
      h.backend.on(method, path, { status: 200, body: { success: true } })
      const { status, lines } = await send(method, path)

      expect(status).to.equal(200)
      expect(auditLines(lines)).to.deep.equal([])
    })
  }
})
