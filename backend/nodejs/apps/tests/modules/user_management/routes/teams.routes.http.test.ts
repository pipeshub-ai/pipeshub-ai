import 'reflect-metadata'
import { expect } from 'chai'
import http from 'http'
import { AddressInfo } from 'net'
import express from 'express'
import jwt from 'jsonwebtoken'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createTeamsRouter } from '../../../../src/modules/user_management/routes/teams.routes'
import { TeamsController } from '../../../../src/modules/user_management/controller/teams.controller'
import { UserDisplayPicture } from '../../../../src/modules/user_management/schema/userDp.schema'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service'
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware'
import { INVALID_PATH_SEGMENT_MESSAGE } from '../../../../src/libs/middlewares/safe-path-params.middleware'
import { Logger } from '../../../../src/libs/services/logger.service'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { UserActivities } from '../../../../src/modules/auth/schema/userActivities.schema'
import { OAuthTokenService } from '../../../../src/modules/oauth_provider/services/oauth_token.service'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'
import {
  ApiResponse,
  FakeBackend,
  RecordedCall,
  buildConfig,
  errorMessage,
  single,
} from '../../tokens_manager/routes/connectors-http-harness'

const JWT_SECRET = 'connectors-http-test-jwt-secret'
const SCOPED_JWT_SECRET = 'connectors-http-test-scoped-secret'

const ORG_A = '64b000000000000000000a01'
const ORG_B = '64b000000000000000000b01'

interface FakeUser {
  _id: string
  orgId: string
  email: string
  fullName: string
  role: 'admin' | 'member'
}

const OWNER: FakeUser = { _id: '64b0000000000000000000a1', orgId: ORG_A, email: 'ada@acme.test', fullName: 'Ada Owner', role: 'member' }
const READER: FakeUser = { _id: '64b0000000000000000000a2', orgId: ORG_A, email: 'max@acme.test', fullName: 'Max Reader', role: 'member' }
const OUTSIDER: FakeUser = { _id: '64b0000000000000000000b1', orgId: ORG_B, email: 'bea@globex.test', fullName: 'Bea Outsider', role: 'admin' }
const USERS = [OWNER, READER, OUTSIDER]

const TEAM_ID = '3f2b8c1e-9a4d-4c7b-8e2f-1a2b3c4d5e6f'
const TEAM_PATH = `/api/v1/entity/team/${TEAM_ID}`

interface Query<T> {
  select(): Query<T>
  lean(): Query<T>
  sort(): Query<T>
  exec(): Promise<T>
  then<R>(onFulfilled: (value: T) => R, onRejected?: (reason: unknown) => R): Promise<R>
}
const query = <T>(value: T): Query<T> => {
  const q: Query<T> = {
    select: () => q,
    lean: () => q,
    sort: () => q,
    exec: async () => value,
    then: (onFulfilled, onRejected) => Promise.resolve(value).then(onFulfilled, onRejected),
  }
  return q
}

const member = (user: FakeUser, role: string) => ({ userId: user._id, userName: user.fullName, userEmail: user.email, role })
const team = (members = [member(OWNER, 'OWNER'), member(READER, 'READER')]) => ({
  id: TEAM_ID,
  name: 'Platform',
  orgId: ORG_A,
  memberCount: members.length,
  members,
  createdByUser: { userId: OWNER._id, userName: OWNER.fullName },
})

describe('Teams over HTTP', () => {
  const backend = new FakeBackend()
  const tokens = new AuthTokenService(JWT_SECRET, SCOPED_JWT_SECRET)
  const oauthGrants = new Map<string, { userId: string; orgId: string; scope: string }>()
  const pictureLookups: Array<Record<string, unknown>> = []
  let server: http.Server
  let origin = ''

  const session = (user: FakeUser): string =>
    tokens.generateToken({
      userId: user._id,
      orgId: user.orgId,
      email: user.email,
      fullName: user.fullName,
      accountType: 'business',
      role: user.role,
    })

  const oauth = (user: FakeUser, scope: string): string => {
    const token = jwt.sign({ tokenType: 'oauth', client_id: 'client-1', iss: 'pipeshub', jti: `${user._id}-${scope}` }, 'unused')
    oauthGrants.set(token, { userId: user._id, orgId: user.orgId, scope })
    return token
  }

  const send = async (
    method: string,
    path: string,
    token?: string,
    body?: unknown,
    extraHeaders: Record<string, string> = {},
  ): Promise<ApiResponse> => {
    const headers: Record<string, string> = { ...extraHeaders }
    if (token) headers.authorization = `Bearer ${token}`
    if (body !== undefined) headers['content-type'] = 'application/json'
    const res = await fetch(`${origin}/api/v1/teams${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const text = await res.text()
    return { status: res.status, body: text ? (JSON.parse(text) as Record<string, unknown>) : {} }
  }

  const sendRaw = (method: string, rawPath: string, token: string): Promise<ApiResponse> =>
    new Promise((resolve, reject) => {
      const req = http.request(
        { host: '127.0.0.1', port: Number(new URL(origin).port), method, path: `/api/v1/teams${rawPath}`, headers: { authorization: `Bearer ${token}` } },
        (res) => {
          const chunks: Buffer[] = []
          res.on('data', (c: Buffer) => chunks.push(c))
          res.on('end', () => resolve({ status: res.statusCode ?? 0, body: JSON.parse(Buffer.concat(chunks).toString('utf8')) }))
        },
      )
      req.on('error', reject)
      req.end()
    })

  const lastCall = (method: string, path: string): RecordedCall => single(backend.callsTo(method, path), `${method} ${path}`)

  before(async () => {
    await backend.start()
    const config: AppConfig = buildConfig(backend.url)
    const oauthService = {
      verifyAccessToken: async (token: string) => {
        const grant = oauthGrants.get(token)
        if (!grant) throw new Error('unknown oauth token')
        return { ...grant, client_id: 'client-1', fullName: 'OAuth Caller', accountType: 'business' }
      },
    } as unknown as OAuthTokenService
    const logger = Logger.getInstance({ service: 'teams-http-test' })
    const container = new Container()
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(new AuthMiddleware(logger, tokens, () => oauthService))
    container.bind<TeamsController>('TeamsController').toConstantValue(new TeamsController(config, logger))

    const app = express()
    app.use(express.json())
    app.use('/api/v1/teams', createTeamsRouter(container))
    app.use(ErrorMiddleware.handleError())
    server = http.createServer(app)
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  })

  after(async () => {
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
    await backend.stop()
  })

  // Stubbed per test: in a serial run another file's root-level
  // `afterEach(sinon.restore)` would undo a stub made once in `before`.
  beforeEach(() => {
    sinon.stub(Users, 'findOne').callsFake(((filter: Record<string, unknown>) =>
      query(
        USERS.find(
          (u) =>
            (filter._id === undefined || String(filter._id) === u._id) &&
            (filter.orgId === undefined || String(filter.orgId) === u.orgId),
        ) ?? null,
      )) as unknown as typeof Users.findOne)
    sinon.stub(UserActivities, 'findOne').callsFake((() => query(null)) as unknown as typeof UserActivities.findOne)
    sinon.stub(UserDisplayPicture, 'find').callsFake(((filter: Record<string, unknown>) => {
      pictureLookups.push(filter)
      const ids = ((filter.userId as { $in?: string[] })?.$in ?? []).filter((id) => filter.orgId === ORG_A && id === READER._id)
      return query(ids.map((userId) => ({ userId, pic: 'cGljdHVyZQ==', mimeType: 'image/png' })))
    }) as unknown as typeof UserDisplayPicture.find)
    backend.reset()
    pictureLookups.length = 0
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('who may do what', () => {
    it('turns away a request without a sign-in before reaching the team service', async () => {
      for (const [method, path] of [['POST', ''], ['GET', `/${TEAM_ID}`], ['PUT', `/${TEAM_ID}`], ['DELETE', `/${TEAM_ID}`], ['GET', '/user/teams']] as const) {
        const res = await send(method, path, undefined, method === 'POST' || method === 'PUT' ? { name: 'x' } : undefined)
        expect(res.status, `${method} ${path}`).to.equal(401)
      }
      expect(backend.calls).to.have.length(0)
    })

    it('lets an app with read access list and open teams but not change them', async () => {
      const token = oauth(OWNER, 'team:read')
      backend.on('GET', TEAM_PATH, { status: 200, body: { status: 'success', team: team() } })
      backend.on('GET', '/api/v1/entity/user/teams', { status: 200, body: { teams: [], pagination: { page: 1, limit: 10, total: 0, pages: 0 } } })
      expect((await send('GET', `/${TEAM_ID}`, token)).status).to.equal(200)
      expect((await send('GET', '/user/teams', token)).status).to.equal(200)

      for (const [method, path] of [['POST', ''], ['PUT', `/${TEAM_ID}`], ['DELETE', `/${TEAM_ID}`]] as const) {
        const res = await send(method, path, token, method === 'DELETE' ? undefined : { name: 'Renamed' })
        expect(res.status, `${method} ${path}`).to.equal(403)
      }
      expect(backend.calls.map((c) => c.method)).to.deep.equal(['GET', 'GET'])
    })

    it("forwards only the caller's own sign-in, not other headers the client sent", async () => {
      backend.on('GET', TEAM_PATH, { status: 200, body: { status: 'success', team: team() } })
      const token = session(READER)
      await send('GET', `/${TEAM_ID}`, token, undefined, { 'x-org-id': ORG_B, 'x-user-id': OUTSIDER._id, cookie: 'sid=abc' })

      const { headers } = lastCall('GET', TEAM_PATH)
      expect(headers.authorization).to.equal(`Bearer ${token}`)
      expect(headers).to.not.have.any.keys('x-org-id', 'x-user-id', 'cookie')
    })

    it('does not pass on an org, creator or unknown field from the request body', async () => {
      backend.on('POST', '/api/v1/entity/team', { status: 200, body: { status: 'success', data: team() } })
      const res = await send('POST', '', session(OWNER), {
        name: '  Platform  ',
        orgId: ORG_B,
        createdBy: OUTSIDER._id,
        isAdminTeam: true,
      })
      expect(res.status).to.equal(201)
      expect(lastCall('POST', '/api/v1/entity/team').body).to.deep.equal({ name: 'Platform' })
    })

    it("relays the team service's refusal when a member who is not an owner edits the team", async () => {
      backend.on('PUT', TEAM_PATH, { status: 403, body: { detail: 'User does not have permission to update this team' } })
      const res = await send('PUT', `/${TEAM_ID}`, session(READER), { name: 'Mine now' })
      expect(res.status).to.equal(403)
      expect(errorMessage(res)).to.equal('User does not have permission to update this team')
    })

    it("answers not found for another org's team", async () => {
      backend.on('DELETE', TEAM_PATH, { status: 404, body: { detail: 'This team could not be found.' } })
      const res = await send('DELETE', `/${TEAM_ID}`, session(OUTSIDER))
      expect(res.status).to.equal(404)
      expect(errorMessage(res)).to.equal('This team could not be found.')
    })

    it("looks up profile pictures only within the caller's org", async () => {
      backend.on('GET', `${TEAM_PATH}/users`, { status: 200, body: { team: team() } })
      await send('GET', `/${TEAM_ID}/users`, session(OUTSIDER))
      expect(single(pictureLookups).orgId).to.equal(ORG_B)
    })
  })

  describe('members', () => {
    it('sends the people picked for a new team and drops blank entries', async () => {
      backend.on('POST', '/api/v1/entity/team', { status: 200, body: { status: 'success', data: team() } })
      const res = await send('POST', '', session(OWNER), {
        name: 'Platform',
        userRoles: [{ userId: READER._id, role: 'READER' }, { userId: '  ', role: 'WRITER' }, { userId: OWNER._id }],
      })
      expect(res.status).to.equal(201)
      expect(lastCall('POST', '/api/v1/entity/team').body).to.deep.equal({
        name: 'Platform',
        userRoles: [{ userId: READER._id, role: 'READER' }],
      })
    })

    it('passes a person listed twice through unchanged; the team service keeps one membership per person', async () => {
      backend.on('PUT', TEAM_PATH, { status: 200, body: { status: 'success', team: team() } })
      const twice = [{ userId: READER._id, role: 'READER' }, { userId: READER._id, role: 'WRITER' }]
      expect((await send('PUT', `/${TEAM_ID}`, session(OWNER), { addUserRoles: twice })).status).to.equal(200)
      expect(lastCall('PUT', TEAM_PATH).body).to.deep.equal({ addUserRoles: twice })
    })

    it('sends additions, removals and role changes together', async () => {
      backend.on('PUT', TEAM_PATH, { status: 200, body: { status: 'success', team: team() } })
      const body = {
        addUserRoles: [{ userId: OUTSIDER._id, role: 'WRITER' }],
        removeUserIds: [READER._id],
        updateUserRoles: [{ userId: OWNER._id, role: 'OWNER' }],
      }
      expect((await send('PUT', `/${TEAM_ID}`, session(OWNER), body)).status).to.equal(200)
      expect(lastCall('PUT', TEAM_PATH).body).to.deep.equal(body)
    })

    for (const [what, body] of [
      ['a member id that is not a user id', { addUserRoles: [{ userId: 'bob', role: 'READER' }] }],
      ['a role that does not exist', { addUserRoles: [{ userId: READER._id, role: 'ADMIN' }] }],
      ['a removal id that is not a user id', { removeUserIds: ['../admin'] }],
      ['an empty name', { name: '   ' }],
      ['a name over 100 characters', { name: 'x'.repeat(101) }],
    ] as const) {
      it(`refuses ${what} before reaching the team service`, async () => {
        const res = await send('PUT', `/${TEAM_ID}`, session(OWNER), body)
        expect(res.status).to.equal(400)
        expect(backend.calls).to.have.length(0)
      })
    }

    it('explains why the last owner cannot be removed', async () => {
      backend.on('PUT', TEAM_PATH, {
        status: 400,
        body: { detail: 'Cannot remove all owners from the team. At least one owner must remain.' },
      })
      const res = await send('PUT', `/${TEAM_ID}`, session(OWNER), { removeUserIds: [OWNER._id] })
      expect(res.status).to.equal(400)
      expect(errorMessage(res)).to.equal('Cannot remove all owners from the team. At least one owner must remain.')
    })

    it('adds profile pictures to the members of the team it returns', async () => {
      for (const [method, path, reply] of [
        ['POST', '', { status: 'success', data: team() }],
        ['GET', `/${TEAM_ID}`, { status: 'success', team: team() }],
        ['PUT', `/${TEAM_ID}`, { status: 'success', team: team() }],
        ['GET', `/${TEAM_ID}/users`, { team: team() }],
      ] as const) {
        backend.reset()
        backend.on(method, path === '' ? '/api/v1/entity/team' : `/api/v1/entity/team${path}`, { status: 200, body: reply })
        const res = await send(method, path, session(OWNER), method === 'GET' ? undefined : { name: 'Platform' })
        expect(res.status, `${method} ${path}`).to.be.oneOf([200, 201])
        const returned = (res.body.team ?? res.body.data) as { members: Array<{ userId: string; profilePicture?: string }> }
        const reader = returned.members.find((m) => m.userId === READER._id)
        expect(reader?.profilePicture, `${method} ${path}`).to.equal('data:image/png;base64,cGljdHVyZQ==')
      }
    })

    it('passes on a team whose member list holds a null entry', async () => {
      const withNull = { ...team(), members: [null, member(READER, 'READER')] }
      for (const [method, path] of [['GET', `/${TEAM_ID}`], ['PUT', `/${TEAM_ID}`]] as const) {
        backend.reset()
        backend.on(method, TEAM_PATH, { status: 200, body: { status: 'success', team: withNull } })
        const res = await send(method, path, session(OWNER), method === 'PUT' ? { name: 'Platform' } : undefined)
        expect(res.status, `${method} ${path}`).to.equal(200)
        const returned = res.body.team as { members: Array<{ userId: string; profilePicture?: string } | null> }
        expect(returned.members[0], `${method} ${path}`).to.equal(null)
        expect(returned.members[1]?.profilePicture, `${method} ${path}`).to.equal('data:image/png;base64,cGljdHVyZQ==')
      }
    })

    it('still answers with the created team when profile pictures cannot be looked up', async () => {
      const unreachable: Query<never> = {
        select: () => unreachable,
        lean: () => unreachable,
        sort: () => unreachable,
        exec: () => Promise.reject(new Error('MongoServerSelectionError: connect ECONNREFUSED 10.0.4.2:27017')),
        then: (onFulfilled, onRejected) => unreachable.exec().then(onFulfilled, onRejected),
      }
      ;(UserDisplayPicture.find as sinon.SinonStub).callsFake(() => unreachable)
      backend.on('POST', '/api/v1/entity/team', { status: 200, body: { status: 'success', data: team() } })
      const res = await send('POST', '', session(OWNER), { name: 'Platform' })
      expect(res.status).to.equal(201)
      const created = res.body.data as { id: string; members: Array<{ userId: string; profilePicture?: string }> }
      expect(created.id).to.equal(TEAM_ID)
      expect(created.members.map((m) => m.profilePicture)).to.deep.equal([undefined, undefined])
      expect(backend.callsTo('POST', '/api/v1/entity/team')).to.have.length(1)

      for (const [method, path] of [['GET', `/${TEAM_ID}`], ['PUT', `/${TEAM_ID}`]] as const) {
        backend.on(method, TEAM_PATH, { status: 200, body: { status: 'success', team: team() } })
        const got = await send(method, path, session(OWNER), method === 'PUT' ? { name: 'Platform' } : undefined)
        expect(got.status, `${method} ${path}`).to.equal(200)
      }
    })
  })

  describe('deleting a team', () => {
    it("passes the deletion to the team service, which removes the team and every member's access", async () => {
      backend.on('DELETE', TEAM_PATH, { status: 200, body: { status: 'success', message: 'Team deleted successfully' } })
      const res = await send('DELETE', `/${TEAM_ID}`, session(OWNER))
      expect(res.status).to.equal(200)
      expect(res.body.message).to.equal('Team deleted successfully')
      expect(lastCall('DELETE', TEAM_PATH).body).to.equal(undefined)
    })

    it("relays that the org's default team cannot be deleted", async () => {
      const allTeam = `all_${ORG_A}`
      backend.on('DELETE', `/api/v1/entity/team/${allTeam}`, { status: 403, body: { detail: 'The default All team cannot be deleted' } })
      const res = await send('DELETE', `/${allTeam}`, session(OWNER))
      expect(res.status).to.equal(403)
      expect(errorMessage(res)).to.equal('The default All team cannot be deleted')
    })
  })

  describe('errors', () => {
    it('hides what the team service said when it failed on its side', async () => {
      backend.on('DELETE', TEAM_PATH, { status: 500, body: { detail: 'arangodb: write-write conflict on permission/8812 at 10.2.0.4' } })
      const res = await send('DELETE', `/${TEAM_ID}`, session(OWNER))
      expect(res.status).to.equal(500)
      expect(errorMessage(res)).to.not.match(/arango|10\.2\.0\.4|permission\//)
      expect(JSON.stringify(res.body)).to.not.include('stack')
    })

    it('says so plainly when the team service cannot be reached', async () => {
      backend.on('GET', TEAM_PATH, 'drop')
      const res = await send('GET', `/${TEAM_ID}`, session(OWNER))
      expect(res.status).to.be.within(500, 599)
      expect(errorMessage(res)).to.not.match(/ECONNRESET|socket|127\.0\.0\.1/)
    })
  })

  describe('pagination and search', () => {
    it("passes a team's member page, size and search on", async () => {
      backend.on('GET', `${TEAM_PATH}/users`, { status: 200, body: { team: team(), pagination: { totalCount: 2 } } })
      const res = await send('GET', `/${TEAM_ID}/users?page=2&limit=25&search=%20max%20`, session(OWNER))
      expect(res.status).to.equal(200)
      const { query: q } = lastCall('GET', `${TEAM_PATH}/users`)
      expect(Object.fromEntries(q)).to.deep.equal({ page: '2', limit: '25', search: 'max' })
    })

    it("passes the caller's team list filters on", async () => {
      backend.on('GET', '/api/v1/entity/user/teams', {
        status: 200,
        body: { teams: [team()], pagination: { page: 3, limit: 5, total: 11, pages: 3, hasNext: false, hasPrev: true } },
      })
      const res = await send('GET', `/user/teams?page=3&limit=5&created_by=${OWNER._id}&created_after=1700000000000`, session(READER))
      expect(res.status).to.equal(200)
      expect(res.body.pagination).to.include({ page: 3, total: 11 })
      const { query: q } = lastCall('GET', '/api/v1/entity/user/teams')
      expect(Object.fromEntries(q)).to.deep.equal({ page: '3', limit: '5', created_by: OWNER._id, created_after: '1700000000000' })
    })

    for (const [what, qs] of [
      ['a page of 0', 'page=0'],
      ['more than 100 per page', 'limit=101'],
      ['a page that is not a number', 'page=two'],
      ['a creator that is not a user id', 'created_by=bob'],
      ['a start time that is not a timestamp', 'created_after=yesterday'],
      ['a search that carries markup', 'search=%3Cscript%3E'],
    ] as const) {
      it(`refuses ${what} before reaching the team service`, async () => {
        const res = await send('GET', `/user/teams?${qs}`, session(READER))
        expect(res.status).to.equal(400)
        expect(backend.calls).to.have.length(0)
      })
    }

    it('shows an empty first page when the team service cannot list teams', async () => {
      backend.on('GET', '/api/v1/entity/user/teams', { status: 503, body: { detail: 'down' } })
      const res = await send('GET', '/user/teams', session(READER))
      expect(res.status).to.equal(200)
      expect(res.body).to.deep.equal({ teams: [], pagination: { page: 1, limit: 10, total: 0, pages: 0 } })
    })
  })

  describe('path parameters', () => {
    for (const segment of ['..', '%2E%2E', `${TEAM_ID}%2F..`, 'a%5Cb', `${TEAM_ID}%3Fx=1`, `${TEAM_ID}%23`, 'a%25b']) {
      it(`refuses "${segment}" as a team id before calling anything`, async () => {
        for (const [method, suffix] of [['GET', ''], ['PUT', ''], ['DELETE', ''], ['GET', '/users']] as const) {
          const res = await sendRaw(method, `/${segment}${suffix}`, session(OWNER))
          expect(res.status, `${method} ${suffix}`).to.equal(400)
          expect(errorMessage(res)).to.equal(INVALID_PATH_SEGMENT_MESSAGE)
        }
        expect(backend.calls).to.have.length(0)
      })
    }

    it('refuses a team id that is not a team key, before calling anything', async () => {
      const res = await send('GET', '/not-a-team', session(OWNER))
      expect(res.status).to.equal(400)
      expect(errorMessage(res)).to.include('Invalid team ID format')
      expect(backend.calls).to.have.length(0)
    })
  })
})
