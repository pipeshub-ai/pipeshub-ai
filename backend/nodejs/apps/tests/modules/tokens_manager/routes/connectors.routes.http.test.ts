import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  MEMBER,
  ADMIN,
  MEMBER_ID,
  ORG_A,
  ORG_B,
  Harness,
  call,
  errorMessage,
  oauthToken,
  sessionToken,
  single,
  startHarness,
} from './connectors-http-harness'
import { SERVICE_UNAVAILABLE_MESSAGE } from '../../../../src/libs/errors/backend-error'

const admin = ADMIN
const member = MEMBER

const CONNECTOR_ID = '3f2c9e7a-1b4d-4c8e-9a6f-2d5e8b7c1a90'

describe('Connector routes over HTTP', () => {
  let h: Harness

  beforeEach(async () => {
    h = await startHarness()
  })

  afterEach(async () => {
    sinon.restore()
    await h.close()
  })

  describe('admin-only vector store jobs', () => {
    for (const operation of ['cleanup', 'reindex'] as const) {
      it(`refuses a member's ${operation} before it reaches the connector service`, async () => {
        const r = await call(h, 'POST', `/vector-store/${operation}`, sessionToken(h, member))

        expect(r.status).to.be.within(400, 499)
        expect(errorMessage(r)).to.equal('Admin access required')
        expect(h.backend.calls).to.have.length(0)
      })

      it(`forwards an admin's ${operation} with the admin's own credentials`, async () => {
        h.backend.on('POST', `/api/v1/connectors/vector-store/${operation}`, {
          status: 202,
          body: { success: true, jobId: 'job-1' },
        })
        const token = sessionToken(h, admin)

        const r = await call(h, 'POST', `/vector-store/${operation}`, token)

        expect(r.status).to.equal(202)
        expect(r.body).to.deep.equal({ success: true, jobId: 'job-1' })
        const forwarded = single(h.backend.callsTo('POST', `/api/v1/connectors/vector-store/${operation}`))
        expect(forwarded.headers.authorization).to.equal(`Bearer ${token}`)
      })
    }

    it('refuses an admin OAuth app token that lacks the connector:sync scope', async () => {
      const r = await call(h, 'POST', '/vector-store/cleanup', oauthToken(h, admin, 'connector:read'))

      expect(r.status).to.equal(403)
      expect(h.backend.calls).to.have.length(0)
    })

    it('lets an admin OAuth app token with connector:sync through', async () => {
      h.backend.on('POST', '/api/v1/connectors/vector-store/cleanup', { status: 202, body: { success: true } })

      const r = await call(h, 'POST', '/vector-store/cleanup', oauthToken(h, admin, 'connector:sync'))

      expect(r.status).to.equal(202)
    })

    it('treats a token for a user in another org as unknown', async () => {
      const stranger = { ...admin, orgId: ORG_B }

      const r = await call(h, 'POST', '/vector-store/cleanup', oauthToken(h, stranger, 'connector:sync'))

      expect(r.status).to.equal(401)
      expect(h.backend.calls).to.have.length(0)
    })

    it('rejects a request with no token', async () => {
      const r = await call(h, 'POST', '/vector-store/cleanup')

      expect(r.status).to.equal(401)
      expect(h.backend.calls).to.have.length(0)
    })
  })

  describe('GET /:connectorId/stats', () => {
    it('asks the connector service for that connector only', async () => {
      h.backend.on('GET', '/api/v1/stats', { status: 200, body: { indexed: 12, failed: 1 } })

      const r = await call(h, 'GET', `/${CONNECTOR_ID}/stats`, sessionToken(h, member))

      expect(r.status).to.equal(200)
      expect(r.body).to.deep.equal({ indexed: 12, failed: 1 })
      const forwarded = single(h.backend.callsTo('GET', '/api/v1/stats'))
      expect(forwarded.query.getAll('connector_id')).to.deep.equal([CONNECTOR_ID])
    })

    it("passes on the connector service's own 404 wording", async () => {
      h.backend.on('GET', '/api/v1/stats', { status: 404, body: { detail: 'Connector not found' } })

      const r = await call(h, 'GET', `/${CONNECTOR_ID}/stats`, sessionToken(h, member))

      expect(r.status).to.equal(404)
      expect(errorMessage(r)).to.equal('Connector not found')
    })

    it('tells the user to try again later when the connector service is down', async () => {
      h.backend.on('GET', '/api/v1/stats', 'drop')

      const r = await call(h, 'GET', `/${CONNECTOR_ID}/stats`, sessionToken(h, member))

      expect(r.status).to.equal(503)
      expect(errorMessage(r)).to.equal(SERVICE_UNAVAILABLE_MESSAGE)
    })
  })

  describe('GET /record/:recordId/content', () => {
    it('returns the parsed content for a record', async () => {
      h.backend.on('GET', `/api/v1/records/${CONNECTOR_ID}/content`, { status: 200, body: { content: 'hello' } })

      const r = await call(h, 'GET', `/record/${CONNECTOR_ID}/content`, sessionToken(h, member))

      expect(r.status).to.equal(200)
      expect(r.body).to.deep.equal({ content: 'hello' })
    })

    it('refuses a record id containing a slash, which no record key can hold', async () => {
      const r = await call(h, 'GET', '/record/a%2Fb/content', sessionToken(h, member))

      expect(r.status).to.equal(400)
      expect(h.backend.calls).to.have.length(0)
    })

    it('answers 404 when the connector service returns no body', async () => {
      h.backend.on('GET', `/api/v1/records/${CONNECTOR_ID}/content`, { status: 200, body: null })

      const r = await call(h, 'GET', `/record/${CONNECTOR_ID}/content`, sessionToken(h, member))

      expect(r.status).to.equal(404)
    })
  })

  describe('POST /:connectorId/reindex', () => {
    it('forwards status filters when the user picked some', async () => {
      h.backend.on('POST', `/api/v1/connectors/${CONNECTOR_ID}/reindex`, { status: 200, body: { success: true } })

      const r = await call(h, 'POST', `/${CONNECTOR_ID}/reindex`, sessionToken(h, member), {
        statusFilters: ['FAILED', 'NOT_STARTED'],
      })

      expect(r.status).to.equal(200)
      const forwarded = single(h.backend.callsTo('POST', `/api/v1/connectors/${CONNECTOR_ID}/reindex`))
      expect(forwarded.body).to.deep.equal({ statusFilters: ['FAILED', 'NOT_STARTED'] })
    })

    it('sends an empty body for "reindex everything"', async () => {
      h.backend.on('POST', `/api/v1/connectors/${CONNECTOR_ID}/reindex`, { status: 200, body: { success: true } })

      await call(h, 'POST', `/${CONNECTOR_ID}/reindex`, sessionToken(h, member), { statusFilters: [] })

      const forwarded = single(h.backend.callsTo('POST', `/api/v1/connectors/${CONNECTOR_ID}/reindex`))
      expect(forwarded.body).to.deep.equal({})
    })

    it('rejects status filters that are not a list of strings', async () => {
      const r = await call(h, 'POST', `/${CONNECTOR_ID}/reindex`, sessionToken(h, member), {
        statusFilters: 'FAILED',
      })

      expect(r.status).to.equal(400)
      expect(h.backend.calls).to.have.length(0)
    })
  })

  describe('POST /:connectorId/resync', () => {
    const activeWith = (...keys: string[]) => ({ status: 200, body: { success: true, connectors: keys.map((k) => ({ _key: k })) } })
    const instance = (fields: Record<string, unknown>) => ({
      status: 200,
      body: { connector: { _key: CONNECTOR_ID, type: 'Google Drive', ...fields } },
    })

    it('publishes a resync event for the caller, ignoring any org or user named in the body', async () => {
      h.backend.on('GET', '/api/v1/connectors/active', activeWith('other', CONNECTOR_ID))
      h.backend.on('GET', `/api/v1/connectors/${CONNECTOR_ID}`, instance({ isLocked: false }))

      const r = await call(h, 'POST', `/${CONNECTOR_ID}/resync`, sessionToken(h, member), {
        connectorName: 'Google Drive',
        fullSync: true,
        orgId: ORG_B,
        userId: 'someone-else',
      })

      expect(r.status).to.equal(200)
      expect(h.syncEvents.published).to.have.length(1)
      const event = single(h.syncEvents.published)
      expect(event.eventType).to.equal('googledrive.resync')
      expect(event.payload).to.include({
        orgId: ORG_A,
        syncedBy: MEMBER_ID,
        connectorId: CONNECTOR_ID,
        connector: 'googledrive',
        fullSync: true,
      })
    })

    it('refuses a connector the caller has no active instance of', async () => {
      h.backend.on('GET', '/api/v1/connectors/active', activeWith('something-else'))

      const r = await call(h, 'POST', `/${CONNECTOR_ID}/resync`, sessionToken(h, member), {
        connectorName: 'Google Drive',
      })

      expect(r.status).to.equal(400)
      expect(h.syncEvents.published).to.have.length(0)
      expect(h.backend.callsTo('GET', `/api/v1/connectors/${CONNECTOR_ID}`)).to.have.length(0)
    })

    it('says a full sync is already running instead of queueing another', async () => {
      h.backend.on('GET', '/api/v1/connectors/active', activeWith(CONNECTOR_ID))
      h.backend.on('GET', `/api/v1/connectors/${CONNECTOR_ID}`, instance({ isLocked: true, status: 'FULL_SYNCING' }))

      const r = await call(h, 'POST', `/${CONNECTOR_ID}/resync`, sessionToken(h, member), {
        connectorName: 'Google Drive',
      })

      expect(r.status).to.equal(409)
      expect(errorMessage(r)).to.equal('A full sync is in progress. Please wait and try again.')
      expect(h.syncEvents.published).to.have.length(0)
    })

    it('uses a generic "in progress" message for any other lock', async () => {
      h.backend.on('GET', '/api/v1/connectors/active', activeWith(CONNECTOR_ID))
      h.backend.on('GET', `/api/v1/connectors/${CONNECTOR_ID}`, instance({ isLocked: true, status: 'DELETING' }))

      const r = await call(h, 'POST', `/${CONNECTOR_ID}/resync`, sessionToken(h, member), {
        connectorName: 'Google Drive',
      })

      expect(r.status).to.equal(409)
      expect(errorMessage(r)).to.equal('Another operation is in progress. Please wait and try again.')
    })

    it('does not publish when the connector state cannot be read', async () => {
      h.backend.on('GET', '/api/v1/connectors/active', activeWith(CONNECTOR_ID))
      h.backend.on('GET', `/api/v1/connectors/${CONNECTOR_ID}`, { status: 500, body: { detail: 'boom' } })

      const r = await call(h, 'POST', `/${CONNECTOR_ID}/resync`, sessionToken(h, member), {
        connectorName: 'Google Drive',
      })

      expect(r.status).to.equal(500)
      expect(h.syncEvents.published).to.have.length(0)
    })

    it('tells the user to try again later when the connector service is down, like every other connector action', async () => {
      h.backend.on('GET', '/api/v1/connectors/active', 'drop')

      const r = await call(h, 'POST', `/${CONNECTOR_ID}/resync`, sessionToken(h, member), {
        connectorName: 'Google Drive',
      })

      expect(r.status).to.equal(503)
      expect(errorMessage(r)).to.equal(SERVICE_UNAVAILABLE_MESSAGE)
      expect(h.syncEvents.published).to.have.length(0)
    })

    it('requires the connector name', async () => {
      const r = await call(h, 'POST', `/${CONNECTOR_ID}/resync`, sessionToken(h, member), {})

      expect(r.status).to.equal(400)
      expect(h.backend.calls).to.have.length(0)
    })
  })

  describe('GET /:connectorId/filters/:filterKey/options', () => {
    it('forwards a single context group path as one repeated parameter', async () => {
      h.backend.on('GET', `/api/v1/connectors/${CONNECTOR_ID}/filters/folders/options`, {
        status: 200,
        body: { options: [] },
      })

      const r = await call(
        h,
        'GET',
        `/${CONNECTOR_ID}/filters/folders/options?contextGroupPath=Shared%2FTeam&excludeContextGroupPath=Archive&excludeContextGroupPath=Old&page=2&limit=50`,
        sessionToken(h, member),
      )

      expect(r.status).to.equal(200)
      const forwarded = single(h.backend.calls)
      expect(forwarded.query.getAll('contextGroupPath')).to.deep.equal(['Shared/Team'])
      expect(forwarded.query.getAll('excludeContextGroupPath')).to.deep.equal(['Archive', 'Old'])
      expect(forwarded.query.get('page')).to.equal('2')
      expect(forwarded.query.get('limit')).to.equal('50')
    })

    it('rejects a page size above 200', async () => {
      const r = await call(h, 'GET', `/${CONNECTOR_ID}/filters/folders/options?limit=500`, sessionToken(h, member))

      expect(r.status).to.equal(400)
      expect(h.backend.calls).to.have.length(0)
    })
  })

  describe('GET /oauth/callback', () => {
    it("passes the connector service's failed-sign-in details back for the page to show", async () => {
      h.backend.on('GET', '/api/v1/connectors/oauth/callback', {
        status: 200,
        body: {
          redirect_url: 'https://app.acme.test/connectors/done',
          success: false,
          error: 'invalid_state',
          error_message: 'The sign-in link expired. Start connecting again.',
        },
      })

      const r = await call(h, 'GET', '/oauth/callback?code=c1&state=s1&baseUrl=https%3A%2F%2Fapp.acme.test', sessionToken(h, member))

      expect(r.status).to.equal(200)
      expect(r.body).to.deep.equal({
        redirectUrl: 'https://app.acme.test/connectors/done',
        success: false,
        error: 'invalid_state',
        errorMessage: 'The sign-in link expired. Start connecting again.',
      })
      const forwarded = single(h.backend.calls)
      expect(forwarded.query.get('code')).to.equal('c1')
      expect(forwarded.query.get('state')).to.equal('s1')
      expect(forwarded.query.get('base_url')).to.equal('https://app.acme.test')
    })

    it('marks a successful sign-in', async () => {
      h.backend.on('GET', '/api/v1/connectors/oauth/callback', {
        status: 200,
        body: { redirect_url: 'https://app.acme.test/connectors/done', success: true },
      })

      const r = await call(h, 'GET', '/oauth/callback?code=c1&state=s1', sessionToken(h, member))

      expect(r.body).to.deep.equal({ redirectUrl: 'https://app.acme.test/connectors/done', success: true })
    })

    it('does not call the connector service without both code and state', async () => {
      const r = await call(h, 'GET', '/oauth/callback?code=c1', sessionToken(h, member))

      expect(r.status).to.equal(400)
      expect(h.backend.calls).to.have.length(0)
    })
  })

  describe('GET /navigate', () => {
    it('forwards one node type given as a plain string', async () => {
      h.backend.on('GET', '/api/v1/knowledge-graph/navigate', { status: 200, body: { rows: [] } })

      const r = await call(h, 'GET', '/navigate?nodeTypes=record&limit=50&depth=1', sessionToken(h, member))

      expect(r.status).to.equal(200)
      const forwarded = single(h.backend.calls)
      expect(forwarded.query.getAll('node_types')).to.deep.equal(['record'])
    })
  })

  describe('GET / list filters', () => {
    it('rejects an isActive value that is not true or false', async () => {
      const r = await call(h, 'GET', '/?isActive=yes', sessionToken(h, member))

      expect(r.status).to.equal(400)
      expect(h.backend.calls).to.have.length(0)
    })
  })

  it('keeps the org of the token, not of the request, on every forwarded call', async () => {
    h.backend.on('GET', '/api/v1/stats', { status: 200, body: { indexed: 0 } })
    const token = sessionToken(h, member)

    await call(h, 'GET', `/${CONNECTOR_ID}/stats?orgId=${ORG_B}`, token)

    const forwarded = single(h.backend.calls)
    expect(forwarded.headers.authorization).to.equal(`Bearer ${token}`)
    expect(forwarded.query.has('orgId')).to.equal(false)
    expect(forwarded.headers).to.not.have.property('x-org-id')
  })
})
