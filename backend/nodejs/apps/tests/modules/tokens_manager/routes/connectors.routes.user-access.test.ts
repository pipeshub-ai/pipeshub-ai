import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ADMIN, MEMBER, MEMBER_ID, Harness, call, sessionToken, single, startHarness } from './connectors-http-harness'

const CONNECTOR_ID = 'conn-1'
const BACKEND = `/api/v1/connectors/${CONNECTOR_ID}/user-access`
const LINK_BACKEND = '/api/v1/connectors/user-access/link'

describe('Connector routes: per-user access invitations and connect links', () => {
  let h: Harness

  beforeEach(async () => {
    h = await startHarness()
  })

  afterEach(async () => {
    sinon.restore()
    await h.close()
  })

  describe('public connect-link routes', () => {
    for (const name of ['info', 'authorize', 'opt-out']) {
      it(`${name}: needs no login and forwards only the token`, async () => {
        h.backend.on('POST', `${LINK_BACKEND}/${name}`, { status: 200, body: { ok: name } })

        const r = await call(h, 'POST', `/user-access/link/${name}`, undefined, { token: 'conn-1.abc', extra: 1 })

        expect(r.status).to.equal(200)
        expect(r.body).to.deep.equal({ ok: name })
        expect(single(h.backend.callsTo('POST', `${LINK_BACKEND}/${name}`)).body).to.deep.equal({ token: 'conn-1.abc' })
      })
    }

    it('never forwards the caller’s credentials', async () => {
      h.backend.on('POST', `${LINK_BACKEND}/info`, { status: 200, body: { connected: false } })

      await call(h, 'POST', '/user-access/link/info', sessionToken(h, MEMBER), { token: 'conn-1.abc' })

      expect(single(h.backend.callsTo('POST', `${LINK_BACKEND}/info`)).headers.authorization).to.equal(undefined)
    })

    it('passes the connector service’s 404 for an expired link through', async () => {
      h.backend.on('POST', `${LINK_BACKEND}/info`, {
        status: 404,
        body: { detail: 'This link has expired or was already used.' },
      })

      const r = await call(h, 'POST', '/user-access/link/info', undefined, { token: 'conn-1.gone' })

      expect(r.status).to.equal(404)
    })

    it('refuses a missing or oversized token before reaching the connector service', async () => {
      for (const body of [{}, { token: '' }, { token: 'x'.repeat(301) }, { token: 5 }]) {
        const r = await call(h, 'POST', '/user-access/link/authorize', undefined, body)
        expect(r.status, JSON.stringify(body).slice(0, 40)).to.equal(400)
      }
      expect(h.backend.calls).to.have.length(0)
    })

    it('limits each link on its own, so one address can open many links, and answers 429 in JSON', async () => {
      h.backend.on('POST', `${LINK_BACKEND}/info`, { status: 200, body: { connected: false } })

      for (let i = 0; i < 20; i++) {
        const r = await call(h, 'POST', '/user-access/link/info', undefined, { token: 'conn-1.same' })
        expect(r.status, `request ${i + 1}`).to.equal(200)
      }
      const limited = await call(h, 'POST', '/user-access/link/info', undefined, { token: 'conn-1.same' })
      const other = await call(h, 'POST', '/user-access/link/info', undefined, { token: 'conn-1.other' })

      expect(limited.status).to.equal(429)
      expect((limited.body.error as { code?: unknown }).code).to.be.a('string')
      expect(other.status).to.equal(200)
    })

    it('complete: forwards code and state', async () => {
      h.backend.on('POST', `${LINK_BACKEND}/complete`, { status: 200, body: { success: true, connectorName: 'Notion' } })

      const r = await call(h, 'POST', '/user-access/link/complete', undefined, { code: 'c', state: 's' })

      expect(r.status).to.equal(200)
      expect(single(h.backend.callsTo('POST', `${LINK_BACKEND}/complete`)).body).to.deep.equal({ code: 'c', state: 's' })
      const missing = await call(h, 'POST', '/user-access/link/complete', undefined, { code: 'c' })
      expect(missing.status).to.equal(400)
    })
  })

  describe('roster and invitations (admins only)', () => {
    it('gives an admin the roster from the connector service', async () => {
      const roster = { membersUpdatedAt: null, people: [], counts: {} }
      h.backend.on('GET', `${BACKEND}/roster`, { status: 200, body: roster })

      const r = await call(h, 'GET', `/${CONNECTOR_ID}/user-access/roster`, sessionToken(h, ADMIN))

      expect(r.status).to.equal(200)
      expect(r.body).to.deep.equal(roster)
    })

    it('answers a member with 404 before anything reaches the connector service', async () => {
      const roster = await call(h, 'GET', `/${CONNECTOR_ID}/user-access/roster`, sessionToken(h, MEMBER))
      const invite = await call(h, 'POST', `/${CONNECTOR_ID}/user-access/invites`, sessionToken(h, MEMBER), { all: true })

      expect(roster.status).to.equal(404)
      expect(invite.status).to.equal(404)
      expect(h.backend.calls).to.have.length(0)
    })

    it('forwards an invitation by user ids or to everyone', async () => {
      h.backend.on('POST', `${BACKEND}/invites`, { status: 200, body: { sent: 1, emailed: 1, skipped: [] } })

      for (const body of [{ userIds: [MEMBER_ID] }, { all: true }]) {
        const r = await call(h, 'POST', `/${CONNECTOR_ID}/user-access/invites`, sessionToken(h, ADMIN), body)
        expect(r.status).to.equal(200)
      }
      expect(h.backend.callsTo('POST', `${BACKEND}/invites`).map((c) => c.body)).to.deep.equal([
        { userIds: [MEMBER_ID] },
        { all: true },
      ])
    })

    it('takes exactly one of userIds or all: true', async () => {
      const bad = [
        {},
        { all: false },
        { userIds: [] },
        { userIds: [''] },
        { userIds: [MEMBER_ID], all: true },
        { userIds: Array.from({ length: 501 }, (_, i) => `u${i}`) },
      ]
      for (const body of bad) {
        const r = await call(h, 'POST', `/${CONNECTOR_ID}/user-access/invites`, sessionToken(h, ADMIN), body)
        expect(r.status, JSON.stringify(body).slice(0, 40)).to.equal(400)
      }
      expect(h.backend.calls).to.have.length(0)
    })
  })

  describe('reminders', () => {
    it('forwards the caller’s choice', async () => {
      h.backend.on('POST', `${BACKEND}/reminders`, { status: 200, body: { optedOut: true } })

      const r = await call(h, 'POST', `/${CONNECTOR_ID}/user-access/reminders`, sessionToken(h, MEMBER), { optedOut: true })

      expect(r.status).to.equal(200)
      const forwarded = single(h.backend.callsTo('POST', `${BACKEND}/reminders`))
      expect(forwarded.body).to.deep.equal({ optedOut: true })
      expect(forwarded.headers.authorization).to.match(/^Bearer /)
    })

    it('needs a boolean and a login', async () => {
      const bad = await call(h, 'POST', `/${CONNECTOR_ID}/user-access/reminders`, sessionToken(h, MEMBER), { optedOut: 'yes' })
      const anonymous = await call(h, 'POST', `/${CONNECTOR_ID}/user-access/reminders`, undefined, { optedOut: true })

      expect(bad.status).to.equal(400)
      expect(anonymous.status).to.equal(401)
      expect(h.backend.calls).to.have.length(0)
    })
  })
})
