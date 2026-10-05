import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import {
  ADMIN,
  KB_ID,
  KB_ROUTES,
  KbHarness,
  MEMBER,
  ORG_B,
  OUTSIDER,
  call,
  callRoute,
  declaredRoutes,
  errorMessage,
  oauthToken,
  pdfForm,
  sessionToken,
  startKbHarness,
  stubRoute,
} from './kb-http-harness'

const ALL_KB_SCOPES = ['kb:read', 'kb:write', 'kb:delete', 'kb:upload'] as const

describe('Knowledge base routes over HTTP: who may call what', () => {
  let h: KbHarness

  beforeEach(async () => {
    h = await startKbHarness()
  })

  afterEach(async () => {
    sinon.restore()
    await h.close()
  })

  it('lists every route the router declares, so none escapes the checks below', () => {
    expect(declaredRoutes(h.router).sort()).to.deep.equal(KB_ROUTES.map((r) => `${r.method} ${r.pattern}`).sort())
  })

  it('refuses every route without a sign-in, before reaching any other service', async () => {
    for (const route of KB_ROUTES) {
      const r = await callRoute(h, route)
      expect(r.status, `${route.method} ${route.pattern}`).to.equal(401)
    }
    expect(h.backend.calls).to.deep.equal([])
  })

  it('refuses a session signed with another key, or for a user who no longer exists', async () => {
    const forged = jwt.sign({ userId: MEMBER._id, orgId: MEMBER.orgId, role: 'admin' }, 'someone-elses-secret')
    const ghost = sessionToken(h, { ...MEMBER, _id: '64c0000000000000000000ff' })
    for (const route of KB_ROUTES) {
      for (const token of [forged, ghost]) {
        const r = await callRoute(h, route, token)
        expect(r.status, `${route.method} ${route.pattern}`).to.equal(401)
      }
    }
    expect(h.backend.calls).to.deep.equal([])
  })

  it("forwards only the caller's own sign-in, never identity headers the client added", async () => {
    const spoofed = { 'x-org-id': ORG_B, 'x-user-id': OUTSIDER._id, 'x-forwarded-user': OUTSIDER.email }
    for (const route of KB_ROUTES) {
      if (!route.forwards) continue
      h.backend.reset()
      stubRoute(h, route)
      const token = sessionToken(h, route.caller ?? MEMBER)
      await callRoute(h, route, token, spoofed)
      const [method, path] = route.forwards.split(' ')
      const forwarded = h.backend.calls.find((c) => c.method === method && c.path === path)
      expect(forwarded, `${route.method} ${route.pattern} reached ${route.forwards}`).to.exist
      expect(forwarded!.headers.authorization, route.pattern).to.equal(`Bearer ${token}`)
      for (const header of Object.keys(spoofed)) {
        expect(forwarded!.headers, `${route.pattern} ${header}`).to.not.have.property(header)
      }
    }
  })

  describe('OAuth scopes', () => {
    for (const route of KB_ROUTES) {
      it(`${route.method} ${route.pattern} needs ${route.scope}`, async () => {
        stubRoute(h, route)
        const caller = route.caller ?? MEMBER
        const without = oauthToken(h, caller, ALL_KB_SCOPES.filter((s) => s !== route.scope).join(' '))
        const refused = await callRoute(h, route, without)
        expect(refused.status).to.equal(403)
        expect(errorMessage(refused)).to.include(route.scope)
        expect(h.backend.calls).to.deep.equal([])

        const allowed = await callRoute(h, route, oauthToken(h, caller, route.scope))
        expect(allowed.status).to.be.within(200, 299)
        if (route.forwards) expect(h.backend.calls.map((c) => `${c.method} ${c.path}`)).to.include(route.forwards)
      })
    }
  })

  it('refuses a member turning the demo off for everyone, before any other call', async () => {
    const r = await call(h, 'PUT', '/demo-data/workspace', { token: sessionToken(h, MEMBER), json: { enabled: false } })
    expect(r.status).to.equal(403)
    expect(h.backend.calls).to.deep.equal([])
    expect((Users.updateMany as sinon.SinonStub).called).to.equal(false)
  })

  describe('request bodies carry no identity the client chose', () => {
    const forwardedBody = (method: string, path: string): unknown =>
      h.backend.calls.find((c) => c.method === method && c.path === path)?.body

    it('creates a knowledge base from its name alone', async () => {
      h.backend.on('POST', '/api/v1/kb/', { status: 200, body: { id: KB_ID } })
      await call(h, 'POST', '/', {
        token: sessionToken(h, MEMBER),
        json: { kbName: 'Handbook', orgId: ORG_B, createdBy: OUTSIDER._id, userRole: 'OWNER' },
      })
      expect(forwardedBody('POST', '/api/v1/kb/')).to.deep.equal({ name: 'Handbook' })
    })

    it('creates and renames folders from their name alone', async () => {
      h.backend.on('POST', `/api/v1/kb/${KB_ID}/folder`, { status: 201, body: { id: 'f1' } })
      h.backend.on('PUT', `/api/v1/kb/${KB_ID}/folder/f1`, { status: 200, body: { success: true } })
      const token = sessionToken(h, MEMBER)
      await call(h, 'POST', `/${KB_ID}/folder`, { token, json: { folderName: 'Specs', orgId: ORG_B } })
      await call(h, 'PUT', `/${KB_ID}/folder/f1`, { token, json: { folderName: 'Specs v2', userId: OUTSIDER._id } })
      expect(forwardedBody('POST', `/api/v1/kb/${KB_ID}/folder`)).to.deep.equal({ name: 'Specs' })
      expect(forwardedBody('PUT', `/api/v1/kb/${KB_ID}/folder/f1`)).to.deep.equal({ name: 'Specs v2' })
    })

    it('grants permissions with only the users, teams and role asked for', async () => {
      h.backend.on('POST', `/api/v1/kb/${KB_ID}/permissions`, { status: 201, body: { success: true } })
      await call(h, 'POST', `/${KB_ID}/permissions`, {
        token: sessionToken(h, MEMBER),
        json: { userIds: [ADMIN._id], teamIds: [], role: 'READER', orgId: ORG_B, grantedBy: OUTSIDER._id },
      })
      expect(forwardedBody('POST', `/api/v1/kb/${KB_ID}/permissions`)).to.deep.equal({
        userIds: [ADMIN._id],
        teamIds: [],
        role: 'READER',
      })
    })

    it('refuses an org id smuggled into the knowledge base list query', async () => {
      const r = await call(h, 'GET', `/?orgId=${ORG_B}`, { token: sessionToken(h, MEMBER) })
      expect(r.status).to.equal(400)
      expect(h.backend.calls).to.deep.equal([])
    })
  })

  describe("another org's or another user's knowledge base", () => {
    it('passes on the refusal and none of the knowledge base', async () => {
      h.backend.on('GET', `/api/v1/kb/${KB_ID}`, { status: 404, body: { detail: 'Knowledge base not found' } })
      const r = await call(h, 'GET', `/${KB_ID}`, { token: sessionToken(h, OUTSIDER) })
      expect(r.status).to.equal(404)
      expect(errorMessage(r)).to.equal('Knowledge base not found')
      expect(r.body).to.not.have.any.keys('id', 'name', 'folders', 'userRole')
    })

    for (const [status, detail] of [
      [403, "You don't have permission to change this knowledge base"],
      [404, 'Knowledge base not found'],
    ] as const) {
      it(`keeps a ${status} from the connector service on every change route`, async () => {
        const token = sessionToken(h, OUTSIDER)
        const changes = KB_ROUTES.filter((r) => r.method !== 'GET' && r.forwards?.includes(KB_ID) && !r.form)
        expect(changes).to.have.length(9)
        for (const route of changes) {
          h.backend.reset()
          const [method, path] = route.forwards!.split(' ') as [string, string]
          h.backend.on(method, path, { status, body: { detail } })
          const r = await callRoute(h, route, token)
          expect(r.status, `${route.method} ${route.pattern}`).to.equal(status)
          expect(errorMessage(r), route.pattern).to.equal(detail)
        }
      })
    }

    it('does not upload a file for someone who cannot see the knowledge base', async () => {
      h.backend.on('GET', `/api/v1/kb/${KB_ID}`, { status: 404, body: { detail: 'Knowledge base not found' } })
      const r = await call(h, 'POST', `/${KB_ID}/upload`, { token: sessionToken(h, OUTSIDER), form: pdfForm('a.pdf') })
      expect(r.status).to.equal(404)
      expect(h.backend.calls.map((c) => c.path)).to.deep.equal([`/api/v1/kb/${KB_ID}`])
    })

    for (const role of ['READER', 'COMMENTER', undefined]) {
      it(`does not upload a file for a ${role ?? 'roleless'} member of the knowledge base`, async () => {
        h.backend.on('GET', `/api/v1/kb/${KB_ID}`, { status: 200, body: { id: KB_ID, userRole: role } })
        const r = await call(h, 'POST', `/${KB_ID}/upload`, { token: sessionToken(h, MEMBER), form: pdfForm('a.pdf') })
        expect(r.status).to.equal(403)
        expect(errorMessage(r)).to.equal('You do not have permission to upload to this knowledge base')
        expect(h.backend.calls.map((c) => c.path)).to.deep.equal([`/api/v1/kb/${KB_ID}`])
      })
    }
  })

  describe('raising your own role', () => {
    it('passes on the refusal when a member makes themselves owner', async () => {
      h.backend.on('PUT', `/api/v1/kb/${KB_ID}/permissions`, {
        status: 403,
        body: { detail: 'Only an owner can change roles on this knowledge base' },
      })
      const r = await call(h, 'PUT', `/${KB_ID}/permissions`, {
        token: sessionToken(h, MEMBER),
        json: { userIds: [MEMBER._id], teamIds: [], role: 'OWNER' },
      })
      expect(r.status).to.equal(403)
      expect(errorMessage(r)).to.equal('Only an owner can change roles on this knowledge base')
      expect(r.body).to.not.have.property('newRole')
    })

    it('passes on the refusal when a member grants themselves owner', async () => {
      h.backend.on('POST', `/api/v1/kb/${KB_ID}/permissions`, {
        status: 403,
        body: { detail: 'Only an owner can share this knowledge base' },
      })
      const r = await call(h, 'POST', `/${KB_ID}/permissions`, {
        token: sessionToken(h, MEMBER),
        json: { userIds: [MEMBER._id], teamIds: [], role: 'OWNER' },
      })
      expect(r.status).to.equal(403)
      expect(r.body).to.not.have.property('permissionResult')
    })

    for (const role of ['ADMIN', 'ORGANIZER', 'owner', '']) {
      it(`refuses the role "${role}" before asking the connector service`, async () => {
        const token = sessionToken(h, MEMBER)
        const grant = await call(h, 'POST', `/${KB_ID}/permissions`, { token, json: { userIds: [MEMBER._id], teamIds: [], role } })
        const change = await call(h, 'PUT', `/${KB_ID}/permissions`, { token, json: { userIds: [MEMBER._id], teamIds: [], role } })
        expect(grant.status).to.equal(400)
        expect(change.status).to.equal(400)
        expect(h.backend.calls).to.deep.equal([])
      })
    }

    it('refuses to give a team a role, since teams do not have one', async () => {
      const r = await call(h, 'PUT', `/${KB_ID}/permissions`, {
        token: sessionToken(h, MEMBER),
        json: { userIds: [], teamIds: ['team-1'], role: 'OWNER' },
      })
      expect(r.status).to.equal(400)
      expect(errorMessage(r)).to.include('Teams do not have roles')
      expect(h.backend.calls).to.deep.equal([])
    })

    it('refuses to add users without saying which role they get', async () => {
      const r = await call(h, 'POST', `/${KB_ID}/permissions`, {
        token: sessionToken(h, MEMBER),
        json: { userIds: [ADMIN._id], teamIds: [] },
      })
      expect(r.status).to.equal(400)
      expect(errorMessage(r)).to.include('Role is required')
      expect(h.backend.calls).to.deep.equal([])
    })

    it('refuses a permission change that names nobody', async () => {
      const token = sessionToken(h, MEMBER)
      for (const method of ['POST', 'DELETE']) {
        const r = await call(h, method, `/${KB_ID}/permissions`, { token, json: { userIds: [], teamIds: [], role: 'READER' } })
        expect(r.status, method).to.equal(400)
      }
      expect(h.backend.calls).to.deep.equal([])
    })
  })
})
