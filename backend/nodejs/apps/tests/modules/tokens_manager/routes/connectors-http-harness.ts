import 'reflect-metadata'
import { expect } from 'chai'
import http, { IncomingHttpHeaders } from 'http'
import { AddressInfo } from 'net'
import express, { Router } from 'express'
import jwt from 'jsonwebtoken'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createConnectorRouter } from '../../../../src/modules/tokens_manager/routes/connectors.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service'
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware'
import { Logger } from '../../../../src/libs/services/logger.service'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { UserActivities } from '../../../../src/modules/auth/schema/userActivities.schema'
import { CrawlingSchedulerService } from '../../../../src/modules/crawling_manager/services/crawling_service'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'
import { OAuthTokenService } from '../../../../src/modules/oauth_provider/services/oauth_token.service'
import { TokenScopes } from '../../../../src/libs/enums/token-scopes.enum'

export const JWT_SECRET = 'connectors-http-test-jwt-secret'
export const SCOPED_JWT_SECRET = 'connectors-http-test-scoped-secret'

export const ORG_A = '64b000000000000000000a01'
export const ORG_B = '64b000000000000000000b01'
export const ADMIN_ID = '64b0000000000000000000a1'
export const MEMBER_ID = '64b0000000000000000000a2'

export interface FakeUser {
  _id: string
  orgId: string
  email: string
  fullName: string
  role: 'admin' | 'member'
}

export const ADMIN: FakeUser = { _id: ADMIN_ID, orgId: ORG_A, email: 'admin@acme.test', fullName: 'Ada Admin', role: 'admin' }
export const MEMBER: FakeUser = { _id: MEMBER_ID, orgId: ORG_A, email: 'member@acme.test', fullName: 'Max Member', role: 'member' }
export const USERS: FakeUser[] = [ADMIN, MEMBER]

export interface RecordedCall {
  method: string
  path: string
  query: URLSearchParams
  headers: IncomingHttpHeaders
  body: unknown
}

export type Reply =
  | {
      status: number
      body?: unknown
      headers?: Record<string, string>
      /** Sent verbatim instead of JSON, for services that answer with bytes or HTML. */
      raw?: string
    }
  | 'drop'
type ReplyFn = (call: RecordedCall) => Reply

/**
 * Stands in for the Python connector service and the configuration manager:
 * both are reached over real HTTP by the service commands under test, so the
 * URL, headers and body they send are what gets asserted.
 */
export class FakeBackend {
  readonly calls: RecordedCall[] = []
  private readonly replies = new Map<string, ReplyFn>()
  private server?: http.Server
  url = ''

  async start(): Promise<void> {
    this.server = http.createServer((req, res) => {
      const chunks: Buffer[] = []
      req.on('data', (c: Buffer) => chunks.push(c))
      req.on('end', () => {
        const parsed = new URL(req.url ?? '/', 'http://backend')
        const raw = Buffer.concat(chunks).toString('utf8')
        let body: unknown = raw
        try {
          body = raw ? JSON.parse(raw) : undefined
        } catch {
          body = raw
        }
        const call: RecordedCall = {
          method: req.method ?? 'GET',
          path: parsed.pathname,
          query: parsed.searchParams,
          headers: req.headers,
          body,
        }
        this.calls.push(call)
        const fn = this.replies.get(`${call.method} ${call.path}`)
        const reply: Reply = fn ? fn(call) : { status: 404, body: { detail: 'not stubbed in test' } }
        if (reply === 'drop') {
          req.socket.destroy()
          return
        }
        res.writeHead(reply.status, { 'content-type': 'application/json', ...reply.headers })
        res.end(reply.raw ?? JSON.stringify(reply.body === undefined ? {} : reply.body))
      })
    })
    await new Promise<void>((resolve) => this.server!.listen(0, '127.0.0.1', resolve))
    this.url = `http://127.0.0.1:${(this.server!.address() as AddressInfo).port}`
  }

  on(method: string, path: string, reply: Reply | ReplyFn): void {
    this.replies.set(`${method} ${path}`, typeof reply === 'function' ? reply : () => reply)
  }

  callsTo(method: string, path: string): RecordedCall[] {
    return this.calls.filter((c) => c.method === method && c.path === path)
  }

  reset(): void {
    this.calls.length = 0
    this.replies.clear()
  }

  async stop(): Promise<void> {
    await new Promise<void>((resolve) => this.server?.close(() => resolve()))
  }
}

export interface PublishedEvent {
  eventType: string
  payload: Record<string, unknown>
}

export class RecordingProducer {
  readonly published: PublishedEvent[] = []
  started = 0
  stopped = 0
  async start(): Promise<void> {
    this.started += 1
  }
  async stop(): Promise<void> {
    this.stopped += 1
  }
  async publishEvent(event: PublishedEvent): Promise<void> {
    this.published.push(event)
  }
}

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

/** Honours the `_id` and `orgId` of a filter, so a lookup scoped to another org finds nothing. */
const matchUser = (filter: Record<string, unknown>): FakeUser | null =>
  USERS.find(
    (u) =>
      (filter._id === undefined || String(filter._id) === u._id) &&
      (filter.orgId === undefined || String(filter.orgId) === u.orgId),
  ) ?? null

export interface OAuthGrant {
  userId: string
  orgId: string
  scope: string
}

export interface Harness {
  /** Scheme, host and port only, for routers mounted outside /api/v1/connectors. */
  origin: string
  baseUrl: string
  backend: FakeBackend
  config: AppConfig
  entityEvents: RecordingProducer
  syncEvents: RecordingProducer
  tokens: AuthTokenService
  oauthGrants: Map<string, OAuthGrant>
  close(): Promise<void>
}

export const buildConfig = (backendUrl: string): AppConfig =>
  ({
    jwtSecret: JWT_SECRET,
    scopedJwtSecret: SCOPED_JWT_SECRET,
    frontendUrl: 'https://app.acme.test',
    cmBackend: backendUrl,
    connectorBackend: backendUrl,
    aiBackend: backendUrl,
    storage: { storageType: 'local', endpoint: backendUrl },
  }) as unknown as AppConfig

/**
 * The real router, auth middleware, token verification, validation and error
 * middleware, mounted the way app.ts mounts them. Mongo reads are faked at the
 * model; every other PipesHub service is the FakeBackend.
 */
export interface HarnessOptions {
  createRouter?: typeof createConnectorRouter
  /** Other routers that share the connector container, mounted as app.ts mounts them. */
  extraRouters?: Array<{ mountPath: string; create: (container: Container) => Router }>
}

export const startHarness = async ({
  createRouter = createConnectorRouter,
  extraRouters = [],
}: HarnessOptions = {}): Promise<Harness> => {
  const backend = new FakeBackend()
  await backend.start()
  // PR #3449 pins connector calls to this origin; set it so these tests hold either way.
  const previousConnectorBackend = process.env.CONNECTOR_BACKEND
  process.env.CONNECTOR_BACKEND = backend.url

  const config = buildConfig(backend.url)
  const tokens = new AuthTokenService(JWT_SECRET, SCOPED_JWT_SECRET)
  const oauthGrants = new Map<string, OAuthGrant>()
  const oauthService = {
    verifyAccessToken: async (token: string) => {
      const grant = oauthGrants.get(token)
      if (!grant) throw new Error('unknown oauth token')
      return { ...grant, client_id: 'client-1', fullName: 'OAuth Caller', accountType: 'business' }
    },
  } as unknown as OAuthTokenService
  const authMiddleware = new AuthMiddleware(Logger.getInstance(), tokens, () => oauthService)

  const entityEvents = new RecordingProducer()
  const syncEvents = new RecordingProducer()
  const container = new Container()
  container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(authMiddleware)
  container.bind<AppConfig>('AppConfig').toConstantValue(config)
  container.bind('EntitiesEventProducer').toConstantValue(entityEvents)
  container.bind('RecordsEventProducer').toConstantValue(new RecordingProducer())
  container.bind('SyncEventProducer').toConstantValue(syncEvents)
  const crawlingContainer = new Container()
  crawlingContainer.bind(CrawlingSchedulerService).toConstantValue({
    scheduleJob: async () => undefined,
    removeJob: async () => undefined,
    getJobStatus: async () => null,
  } as unknown as CrawlingSchedulerService)

  sinon
    .stub(Users, 'findOne')
    .callsFake(((filter: Record<string, unknown>) => query(matchUser(filter))) as unknown as typeof Users.findOne)
  sinon
    .stub(UserActivities, 'findOne')
    .callsFake((() => query(null)) as unknown as typeof UserActivities.findOne)

  const app = express()
  app.use(express.json())
  app.use('/api/v1/connectors', createRouter(container, crawlingContainer))
  for (const extra of extraRouters) app.use(extra.mountPath, extra.create(container))
  app.use(ErrorMiddleware.handleError())
  const server = http.createServer(app)
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const port = (server.address() as AddressInfo).port
  const origin = `http://127.0.0.1:${port}`
  const baseUrl = `${origin}/api/v1/connectors`

  return {
    origin,
    baseUrl,
    backend,
    config,
    entityEvents,
    syncEvents,
    tokens,
    oauthGrants,
    close: async () => {
      await new Promise<void>((resolve) => server.close(() => resolve()))
      await backend.stop()
      if (previousConnectorBackend === undefined) delete process.env.CONNECTOR_BACKEND
      else process.env.CONNECTOR_BACKEND = previousConnectorBackend
    },
  }
}

export const sessionToken = (h: Harness, user: FakeUser): string =>
  h.tokens.generateToken({
    userId: user._id,
    orgId: user.orgId,
    email: user.email,
    fullName: user.fullName,
    accountType: 'business',
    role: user.role,
  })

export const fetchConfigToken = (h: Harness, user: FakeUser): string =>
  h.tokens.generateScopedToken({ userId: user._id, orgId: user.orgId, scopes: [TokenScopes.FETCH_CONFIG] })

/** An OAuth access token for `user` carrying only `scope`; its verification is faked, its claims are real. */
export const oauthToken = (h: Harness, user: FakeUser, scope: string): string => {
  const token = jwt.sign({ tokenType: 'oauth', client_id: 'client-1', iss: 'pipeshub', jti: `${user._id}-${scope}` }, 'unused')
  h.oauthGrants.set(token, { userId: user._id, orgId: user.orgId, scope })
  return token
}

export interface ApiResponse {
  status: number
  body: Record<string, unknown>
}

export const call = async (
  h: Harness,
  method: string,
  path: string,
  token?: string,
  body?: unknown,
): Promise<ApiResponse> => {
  const headers: Record<string, string> = {}
  if (token) headers.authorization = `Bearer ${token}`
  if (body !== undefined) headers['content-type'] = 'application/json'
  const res = await fetch(`${h.baseUrl}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const text = await res.text()
  let parsed: Record<string, unknown> = {}
  try {
    parsed = text ? (JSON.parse(text) as Record<string, unknown>) : {}
  } catch {
    parsed = { raw: text }
  }
  return { status: res.status, body: parsed }
}

export const errorMessage = (r: ApiResponse): string =>
  String((r.body.error as { message?: unknown } | undefined)?.message ?? r.body.message ?? '')

/** The one element of `items`, failing the test when there are none or several. */
export const single = <T>(items: readonly T[], what = 'items'): T => {
  expect(items, what).to.have.length(1)
  return items[0] as T
}

/**
 * Sends `rawPath` exactly as written. fetch() applies URL parsing first, which
 * resolves `%2E%2E` and `.` segments before Express ever sees them.
 */
export const rawCall = (
  h: Harness,
  method: string,
  rawPath: string,
  token: string,
  body?: unknown,
): Promise<ApiResponse> =>
  new Promise((resolve, reject) => {
    const payload = body === undefined ? undefined : JSON.stringify(body)
    const req = http.request(
      {
        host: '127.0.0.1',
        port: Number(new URL(h.origin).port),
        method,
        path: rawPath,
        headers: {
          authorization: `Bearer ${token}`,
          ...(payload === undefined ? {} : { 'content-type': 'application/json' }),
        },
      },
      (res) => {
        const chunks: Buffer[] = []
        res.on('data', (c: Buffer) => chunks.push(c))
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8')
          let parsed: Record<string, unknown> = { raw: text }
          try {
            parsed = JSON.parse(text) as Record<string, unknown>
          } catch {
            // Non-JSON bodies stay under `raw`.
          }
          resolve({ status: res.statusCode ?? 0, body: parsed })
        })
      },
    )
    req.on('error', reject)
    if (payload !== undefined) req.write(payload)
    req.end()
  })
