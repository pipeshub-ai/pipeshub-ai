import 'reflect-metadata'
import { expect } from 'chai'
import http from 'http'
import { AddressInfo } from 'net'
import express from 'express'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createUserGroupRouter } from '../../../../src/modules/user_management/routes/userGroups.routes'
import { createOrgRouter } from '../../../../src/modules/user_management/routes/org.routes'
import { createUserRouter } from '../../../../src/modules/user_management/routes/users.routes'
import { UserGroupController } from '../../../../src/modules/user_management/controller/userGroups.controller'
import { OrgController } from '../../../../src/modules/user_management/controller/org.controller'
import {
  USER_LOOKUP_FIELDS,
  UserController,
} from '../../../../src/modules/user_management/controller/users.controller'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service'
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware'
import {
  OPERATOR_KEY_MESSAGE,
  operatorKeyGuardMiddleware,
} from '../../../../src/libs/middlewares/operator-key-guard.middleware'
import { Logger } from '../../../../src/libs/services/logger.service'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { UserGroups } from '../../../../src/modules/user_management/schema/userGroup.schema'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'
import { UserActivities } from '../../../../src/modules/auth/schema/userActivities.schema'
import { OAuthTokenService } from '../../../../src/modules/oauth_provider/services/oauth_token.service'
import { iamJwtGenerator, iamUserLookupJwtGenerator } from '../../../../src/libs/utils/createJwt'

const JWT_SECRET = 'mongo-input-test-jwt-secret'
const SCOPED_JWT_SECRET = 'mongo-input-test-scoped-secret'
const ORG_ID = '64b000000000000000000a01'

const ADMIN = {
  _id: '64b0000000000000000000a1',
  orgId: ORG_ID,
  email: 'ada@acme.test',
  fullName: 'Ada Admin',
  firstName: 'Ada',
  role: 'admin',
  hasLoggedIn: true,
  mobile: '+15550100',
  address: { city: 'London' },
  slug: 'ada-admin',
  designation: 'CTO',
}

interface Query<T> {
  select(fields?: string): Query<T>
  lean(): Query<T>
  sort(): Query<T>
  skip(): Query<T>
  limit(): Query<T>
  exec(): Promise<T>
  then<R>(onFulfilled: (value: T) => R, onRejected?: (reason: unknown) => R): Promise<R>
}

// Applies a string projection the way Mongo would, so a handler that forgets
// to project sends the whole record.
const query = <T>(value: T, onSelect?: (fields: string) => void): Query<T> => {
  let projected: T = value
  const q: Query<T> = {
    select: (fields?: string) => {
      if (fields) {
        onSelect?.(fields)
        const keep = fields.split(/\s+/)
        const pick = (doc: Record<string, unknown>) =>
          Object.fromEntries(Object.entries(doc).filter(([k]) => keep.includes(k)))
        projected = (Array.isArray(value) ? value.map(pick) : value && pick(value as Record<string, unknown>)) as T
      }
      return q
    },
    lean: () => q,
    sort: () => q,
    skip: () => q,
    limit: () => q,
    exec: async () => projected,
    then: (onFulfilled, onRejected) => Promise.resolve(projected).then(onFulfilled, onRejected),
  }
  return q
}

interface Reply {
  status: number
  body: Record<string, unknown> | unknown[]
}

describe('Mongo operator input over HTTP', () => {
  const tokens = new AuthTokenService(JWT_SECRET, SCOPED_JWT_SECRET)
  let server: http.Server
  let port = 0
  let groupLookups: sinon.SinonStub
  let orgUpdates: sinon.SinonStub
  let userLookups: sinon.SinonStub
  const selects: string[] = []

  const session = (): string =>
    tokens.generateToken({
      userId: ADMIN._id,
      orgId: ADMIN.orgId,
      email: ADMIN.email,
      fullName: ADMIN.fullName,
      accountType: 'business',
      role: 'admin',
    })

  // http.request rather than fetch: the email lookup is a GET with a body.
  const send = (
    method: string,
    path: string,
    { token, json, form }: { token?: string; json?: unknown; form?: string } = {},
  ): Promise<Reply> =>
    new Promise((resolve, reject) => {
      const headers: Record<string, string> = {}
      if (token) headers.authorization = `Bearer ${token}`
      let payload: string | undefined
      if (json !== undefined) {
        payload = JSON.stringify(json)
        headers['content-type'] = 'application/json'
      } else if (form !== undefined) {
        payload = form
        headers['content-type'] = 'application/x-www-form-urlencoded'
      }
      if (payload !== undefined) headers['content-length'] = String(Buffer.byteLength(payload))
      const req = http.request({ host: '127.0.0.1', port, method, path, headers }, (res) => {
        const chunks: Buffer[] = []
        res.on('data', (c: Buffer) => chunks.push(c))
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8')
          resolve({ status: res.statusCode ?? 0, body: text ? JSON.parse(text) : {} })
        })
      })
      req.on('error', reject)
      if (payload !== undefined) req.write(payload)
      req.end()
    })

  before(async () => {
    const logger = Logger.getInstance({ service: 'mongo-input-http-test' })
    const oauthService = {
      verifyAccessToken: async () => {
        throw new Error('no oauth tokens in this test')
      },
    } as unknown as OAuthTokenService
    const eventService = { start: async () => {}, publishEvent: async () => {}, stop: async () => {} }
    const config = { jwtSecret: JWT_SECRET, scopedJwtSecret: SCOPED_JWT_SECRET }

    const container = new Container()
    container.bind('AuthMiddleware').toConstantValue(new AuthMiddleware(logger, tokens, () => oauthService))
    container.bind('Logger').toConstantValue(logger)
    container.bind('AppConfig').toConstantValue(config)
    container.bind('UserGroupController').toConstantValue(new UserGroupController())
    container
      .bind('OrgController')
      .toConstantValue(new OrgController(...([config, {}, logger, eventService] as unknown as ConstructorParameters<typeof OrgController>)))
    container
      .bind('UserController')
      .toConstantValue(new UserController(...([config, {}, {}, logger, eventService, {}] as unknown as ConstructorParameters<typeof UserController>)))

    // Registered as app.ts registers them.
    const app = express()
    app.use(express.json())
    app.use(express.urlencoded({ extended: true }))
    app.use(operatorKeyGuardMiddleware)
    app.use('/api/v1/userGroups', createUserGroupRouter(container))
    app.use('/api/v1/org', createOrgRouter(container))
    app.use('/api/v1/users', createUserRouter(container))
    app.use(ErrorMiddleware.handleError())
    server = http.createServer(app)
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    port = (server.address() as AddressInfo).port
  })

  after(async () => {
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
  })

  // Stubbed per test: in a serial run another file's root-level
  // `afterEach(sinon.restore)` would undo a stub made once in `before`.
  beforeEach(() => {
    selects.length = 0
    sinon.stub(Users, 'findOne').callsFake((() => query({ ...ADMIN })) as unknown as typeof Users.findOne)
    sinon.stub(UserActivities, 'findOne').callsFake((() => query(null)) as unknown as typeof UserActivities.findOne)
    groupLookups = sinon.stub(UserGroups, 'findOne').callsFake((() => query(null)) as unknown as typeof UserGroups.findOne)
    sinon.stub(UserGroups, 'find').callsFake((() => query([])) as unknown as typeof UserGroups.find)
    sinon.stub(UserGroups, 'countDocuments').resolves(0)
    sinon.stub(Org, 'findOne').callsFake((() =>
      query({ _id: ORG_ID, registeredName: 'Acme' })) as unknown as typeof Org.findOne)
    orgUpdates = sinon.stub(Org, 'findByIdAndUpdate').callsFake(((_id: string, update: Record<string, unknown>) =>
      query({ _id: ORG_ID, registeredName: 'Acme', ...update })) as unknown as typeof Org.findByIdAndUpdate)
    userLookups = sinon.stub(Users, 'find').callsFake(((filter: { email?: unknown }) =>
      query(filter.email === ADMIN.email ? [{ ...ADMIN }] : [], (fields) => selects.push(fields))) as unknown as typeof Users.find)
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('POST /userGroups', () => {
    for (const [label, name] of [
      ['$ne', { $ne: null }],
      ['$regex', { $regex: '.*' }],
      ['an array', ['admin', 'everyone']],
    ] as const) {
      it(`refuses a name given as ${label} before any group lookup`, async () => {
        const res = await send('POST', '/api/v1/userGroups', { token: session(), json: { name, type: 'custom' } })
        expect(res.status).to.equal(400)
        expect(groupLookups.called).to.equal(false)
      })
    }

    it('refuses a form body that encodes an operator, name[$ne]=x', async () => {
      const res = await send('POST', '/api/v1/userGroups', { token: session(), form: 'name[$ne]=x&type=custom' })
      expect(res.status).to.equal(400)
      expect(groupLookups.called).to.equal(false)
    })
  })

  describe('PUT /org', () => {
    it('refuses an operator in place of the registered name', async () => {
      const res = await send('PUT', '/api/v1/org', { token: session(), json: { registeredName: { $where: '1' } } })
      expect(res.status).to.equal(400)
      expect(orgUpdates.called).to.equal(false)
    })

    it('refuses an operator inside the address', async () => {
      const res = await send('PUT', '/api/v1/org', { token: session(), json: { permanentAddress: { city: { $gt: '' } } } })
      expect(res.status).to.equal(400)
      expect(orgUpdates.called).to.equal(false)
    })

    it('drops a top-level $set and updates only the named field', async () => {
      const res = await send('PUT', '/api/v1/org', {
        token: session(),
        json: { $set: { isDeleted: true }, shortName: 'x' },
      })
      expect(res.status).to.equal(200)
      expect(orgUpdates.calledOnce).to.equal(true)
      expect(orgUpdates.firstCall.args[1]).to.deep.equal({ shortName: 'x' })
    })
  })

  describe('GET /users/email/exists', () => {
    const lookupToken = (email = ADMIN.email) => iamJwtGenerator(email, SCOPED_JWT_SECRET)

    it('answers with only the fields the auth service reads', async () => {
      const res = await send('GET', '/api/v1/users/email/exists', { token: lookupToken(), json: { email: ADMIN.email } })
      expect(res.status).to.equal(200)
      expect(selects).to.deep.equal([USER_LOOKUP_FIELDS])
      expect(res.body).to.deep.equal([
        { _id: ADMIN._id, orgId: ADMIN.orgId, email: ADMIN.email, fullName: ADMIN.fullName, role: 'admin', hasLoggedIn: true },
      ])
    })

    it('matches the token email whatever its case', async () => {
      const res = await send('GET', '/api/v1/users/email/exists', {
        token: lookupToken('Ada@Acme.test'),
        json: { email: 'ada@ACME.test' },
      })
      expect(res.status).to.equal(200)
      expect(userLookups.firstCall.args[0]).to.deep.equal({ email: ADMIN.email, isDeleted: false })
    })

    it('refuses an operator in place of the email', async () => {
      const res = await send('GET', '/api/v1/users/email/exists', { token: lookupToken(), json: { email: { $ne: null } } })
      expect(res.status).to.equal(400)
      expect(userLookups.called).to.equal(false)
    })

    it('refuses a session token', async () => {
      const res = await send('GET', '/api/v1/users/email/exists', { token: session(), json: { email: ADMIN.email } })
      expect(res.status).to.equal(401)
      expect(userLookups.called).to.equal(false)
    })

    it('refuses a lookup token minted for another email', async () => {
      const res = await send('GET', '/api/v1/users/email/exists', {
        token: lookupToken('mallory@evil.test'),
        json: { email: ADMIN.email },
      })
      expect(res.status).to.equal(403)
      expect(userLookups.called).to.equal(false)
    })

    it('refuses a lookup token that names a user rather than an email', async () => {
      const res = await send('GET', '/api/v1/users/email/exists', {
        token: iamUserLookupJwtGenerator(ADMIN._id, ORG_ID, SCOPED_JWT_SECRET),
        json: { email: ADMIN.email },
      })
      expect(res.status).to.equal(403)
      expect(userLookups.called).to.equal(false)
    })
  })

  describe('operator-shaped query parameter names', () => {
    for (const qs of ['email[$ne]=x', 'a.b=1', 'f[a][$gt]=1', '$where=1', 'search=x&filter[0][$ne]=1']) {
      it(`refuses ?${qs}`, async () => {
        const res = await send('GET', `/api/v1/userGroups?${qs}`, { token: session() })
        expect(res.status).to.equal(400)
        expect((res.body as { error?: { message?: string } }).error?.message).to.equal(OPERATOR_KEY_MESSAGE)
      })
    }

    for (const path of ['/API/v1/userGroups', '/Api/V1/UserGroups']) {
      it(`refuses ?$where=1 on ${path}, which Express routes to the same handler`, async () => {
        const res = await send('GET', `${path}?$where=1`, { token: session() })
        expect(res.status).to.equal(400)
        expect((res.body as { error?: { message?: string } }).error?.message).to.equal(OPERATOR_KEY_MESSAGE)
      })
    }

    it('lets ordinary pagination and a search with dots in its value through', async () => {
      const res = await send('GET', '/api/v1/userGroups?page=1&limit=10&search=v1.2%20report.pdf', { token: session() })
      expect(res.status).to.equal(200)
    })

    it('leaves request bodies to the route schema, so JSON Schema keys still reach it', async () => {
      const res = await send('PUT', '/api/v1/org', {
        token: session(),
        json: { shortName: 'x', $schema: 'https://json-schema.org/draft/2020-12/schema', 'a.b': 1 },
      })
      expect(res.status).to.equal(200)
      expect(orgUpdates.firstCall.args[1]).to.deep.equal({ shortName: 'x' })
    })
  })
})

describe('operatorKeyGuardMiddleware outside the API', () => {
  it('leaves page and asset URLs alone', () => {
    const next = sinon.spy()
    operatorKeyGuardMiddleware(
      { path: '/toolsets/oauth/callback/Gmail', query: { 'openid.mode': 'id_res' } } as unknown as express.Request,
      {} as express.Response,
      next,
    )
    expect(next.calledOnceWithExactly()).to.equal(true)
  })

  for (const path of ['/mcp', '/MCP', '/Mcp/session']) {
    it(`guards the MCP endpoint at ${path}`, () => {
      const next = sinon.spy()
      operatorKeyGuardMiddleware(
        { path, query: { $where: '1' } } as unknown as express.Request,
        {} as express.Response,
        next,
      )
      expect(next.firstCall.args[0]).to.have.property('statusCode', 400)
    })
  }
})
