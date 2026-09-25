import 'reflect-metadata'
import http, { IncomingHttpHeaders } from 'http'
import { AddressInfo } from 'net'
import express, { Router } from 'express'
import jwt from 'jsonwebtoken'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createKnowledgeBaseRouter } from '../../../../src/modules/knowledge_base/routes/kb.routes'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service'
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware'
import { Logger } from '../../../../src/libs/services/logger.service'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { UserActivities } from '../../../../src/modules/auth/schema/userActivities.schema'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'
import { OAuthTokenService } from '../../../../src/modules/oauth_provider/services/oauth_token.service'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import { EncryptionService } from '../../../../src/libs/encryptor/encryptor'
import { loadConfigurationManagerConfig } from '../../../../src/modules/configuration_manager/config/config'
import { configPaths } from '../../../../src/modules/configuration_manager/paths/paths'
import { endpoint as ENDPOINTS_KEY } from '../../../../src/modules/storage/constants/constants'
import { FakeBackend, RecordedCall } from '../../tokens_manager/routes/connectors-http-harness'

export { FakeBackend, RecordedCall }

export const JWT_SECRET = 'kb-http-test-jwt-secret'
export const SCOPED_JWT_SECRET = 'kb-http-test-scoped-secret'

export const ORG_A = '64c000000000000000000a01'
export const ORG_B = '64c000000000000000000b01'

export const KB_ID = '3f2c9e7a-1b4d-4c8e-9a6f-2d5e8b7c1a90'
export const OTHER_KB_ID = '9b1d5c3e-7a2f-4e6b-8c0d-1f3a5e7b9c2d'
export const FOLDER_ID = '5e8b7c1a-2d4f-4a6c-9e1b-3c5d7f9a1b3e'
export const RECORD_ID = 'b7c1a90e-4d2f-4c8e-9a6f-8e2d5c3f1a7b'

export interface KbUser {
  _id: string
  orgId: string
  email: string
  fullName: string
  role: 'admin' | 'member'
}

export const ADMIN: KbUser = { _id: '64c0000000000000000000a1', orgId: ORG_A, email: 'ada@acme.test', fullName: 'Ada Admin', role: 'admin' }
export const MEMBER: KbUser = { _id: '64c0000000000000000000a2', orgId: ORG_A, email: 'max@acme.test', fullName: 'Max Member', role: 'member' }
export const OUTSIDER: KbUser = { _id: '64c0000000000000000000b2', orgId: ORG_B, email: 'olga@globex.test', fullName: 'Olga Outsider', role: 'member' }
const USERS: KbUser[] = [ADMIN, MEMBER, OUTSIDER]

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

/** The key-value store as the router reads it: service endpoints and encrypted platform settings. */
export class FakeKeyValueStore {
  readonly values = new Map<string, string>()

  async get<T>(key: string): Promise<T | null> {
    return (this.values.get(key) as T | undefined) ?? null
  }

  async set<T>(key: string, value: T): Promise<void> {
    this.values.set(key, String(value))
  }

  async delete(key: string): Promise<void> {
    this.values.delete(key)
  }

  private readonly watchers = new Map<string, Array<() => void>>()

  async watchKey(key: string, onChange: () => void): Promise<void> {
    this.watchers.set(key, [...(this.watchers.get(key) ?? []), onChange])
  }

  /** Tells watchers `key` changed, as the store does when an admin edits it. */
  changed(key: string): void {
    for (const onChange of this.watchers.get(key) ?? []) onChange()
  }
}

export interface KbHarness {
  origin: string
  baseUrl: string
  backend: FakeBackend
  kv: FakeKeyValueStore
  config: AppConfig
  tokens: AuthTokenService
  oauthGrants: Map<string, { userId: string; orgId: string; scope: string }>
  router: Router
  /** Stores the per-file upload limit the way the platform settings page does. */
  setMaxUploadBytes(bytes: number): void
  close(): Promise<void>
}

/**
 * The real knowledge-base router, auth middleware, validation and error
 * middleware, mounted as app.ts mounts them. The connector service and the
 * storage service are one FakeBackend reached over real HTTP; Mongo user
 * lookups and the key-value store are faked in memory.
 */
export const startKbHarness = async (): Promise<KbHarness> => {
  const backend = new FakeBackend()
  await backend.start()
  const previousConnectorBackend = process.env.CONNECTOR_BACKEND
  process.env.CONNECTOR_BACKEND = backend.url
  const previousSecretKey = process.env.SECRET_KEY
  process.env.SECRET_KEY = previousSecretKey ?? 'kb-http-test-secret-key'

  const config = {
    jwtSecret: JWT_SECRET,
    scopedJwtSecret: SCOPED_JWT_SECRET,
    frontendUrl: 'https://app.acme.test',
    cmBackend: backend.url,
    connectorBackend: backend.url,
    aiBackend: backend.url,
    storage: { storageType: 'local', endpoint: backend.url },
  } as unknown as AppConfig

  const kv = new FakeKeyValueStore()
  kv.values.set(ENDPOINTS_KEY, JSON.stringify({ storage: { endpoint: backend.url } }))

  const tokens = new AuthTokenService(JWT_SECRET, SCOPED_JWT_SECRET)
  const oauthGrants = new Map<string, { userId: string; orgId: string; scope: string }>()
  const oauthService = {
    verifyAccessToken: async (token: string) => {
      const grant = oauthGrants.get(token)
      if (!grant) throw new Error('unknown oauth token')
      return { ...grant, client_id: 'client-1', fullName: 'OAuth Caller', accountType: 'business' }
    },
  } as unknown as OAuthTokenService
  const authMiddleware = new AuthMiddleware(Logger.getInstance(), tokens, () => oauthService)

  const container = new Container()
  container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue(authMiddleware)
  container.bind<AppConfig>('AppConfig').toConstantValue(config)
  container.bind<KeyValueStoreService>('KeyValueStoreService').toConstantValue(kv as unknown as KeyValueStoreService)

  sinon.stub(Users, 'findOne').callsFake(((filter: Record<string, unknown>) =>
    query(
      USERS.find(
        (u) =>
          (filter._id === undefined || String(filter._id) === u._id) &&
          (filter.orgId === undefined || String(filter.orgId) === u.orgId),
      ) ?? null,
    )) as unknown as typeof Users.findOne)
  sinon.stub(UserActivities, 'findOne').callsFake((() => query(null)) as unknown as typeof UserActivities.findOne)

  const router = createKnowledgeBaseRouter(container)
  const app = express()
  app.use(express.json())
  app.use('/api/v1/knowledgeBase', router)
  app.use(ErrorMiddleware.handleError())
  const server = http.createServer(app)
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`

  return {
    origin,
    baseUrl: `${origin}/api/v1/knowledgeBase`,
    backend,
    kv,
    config,
    tokens,
    oauthGrants,
    router,
    setMaxUploadBytes: (bytes: number) => {
      const cm = loadConfigurationManagerConfig()
      kv.values.set(
        configPaths.platform.settings,
        EncryptionService.getInstance(cm.algorithm, cm.secretKey).encrypt(
          JSON.stringify({ fileUploadMaxSizeBytes: bytes }),
        ),
      )
    },
    close: async () => {
      await new Promise<void>((resolve) => server.close(() => resolve()))
      await backend.stop()
      if (previousConnectorBackend === undefined) delete process.env.CONNECTOR_BACKEND
      else process.env.CONNECTOR_BACKEND = previousConnectorBackend
      if (previousSecretKey === undefined) delete process.env.SECRET_KEY
      else process.env.SECRET_KEY = previousSecretKey
    },
  }
}

export const sessionToken = (h: KbHarness, user: KbUser): string =>
  h.tokens.generateToken({
    userId: user._id,
    orgId: user.orgId,
    email: user.email,
    fullName: user.fullName,
    accountType: 'business',
    role: user.role,
  })

/** An OAuth access token for `user` carrying only `scope`; its verification is faked, its claims are real. */
export const oauthToken = (h: KbHarness, user: KbUser, scope: string): string => {
  const token = jwt.sign({ tokenType: 'oauth', client_id: 'client-1', iss: 'pipeshub', jti: `${user._id}-${scope}` }, 'unused')
  h.oauthGrants.set(token, { userId: user._id, orgId: user.orgId, scope })
  return token
}

export interface ApiResponse {
  status: number
  headers: Headers
  text: string
  body: Record<string, unknown>
}

const parseBody = (text: string): Record<string, unknown> => {
  try {
    const parsed: unknown = text ? JSON.parse(text) : {}
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : { value: parsed }
  } catch {
    return { raw: text }
  }
}

export interface CallOptions {
  token?: string
  json?: unknown
  form?: FormData
  headers?: Record<string, string>
}

export const call = async (h: KbHarness, method: string, path: string, opts: CallOptions = {}): Promise<ApiResponse> => {
  const headers: Record<string, string> = { ...opts.headers }
  if (opts.token) headers.authorization = `Bearer ${opts.token}`
  let body: string | FormData | undefined
  if (opts.form) {
    body = opts.form
  } else if (opts.json !== undefined) {
    headers['content-type'] = 'application/json'
    body = JSON.stringify(opts.json)
  }
  const res = await fetch(`${h.baseUrl}${path}`, { method, headers, body })
  const text = await res.text()
  return { status: res.status, headers: res.headers, text, body: parseBody(text) }
}

/**
 * Sends `rawPath` exactly as written. fetch() applies URL parsing first, which
 * resolves `%2E%2E` and `.` segments before Express ever sees them.
 */
export const rawCall = (h: KbHarness, method: string, rawPath: string, token: string, json?: unknown): Promise<ApiResponse> =>
  new Promise((resolve, reject) => {
    const payload = json === undefined ? undefined : JSON.stringify(json)
    const req = http.request(
      {
        host: '127.0.0.1',
        port: Number(new URL(h.origin).port),
        method,
        path: `/api/v1/knowledgeBase${rawPath}`,
        headers: {
          authorization: `Bearer ${token}`,
          ...(payload === undefined
            ? {}
            : { 'content-type': 'application/json', 'content-length': String(Buffer.byteLength(payload)) }),
        },
      },
      (res) => {
        const chunks: Buffer[] = []
        res.on('data', (c: Buffer) => chunks.push(c))
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8')
          const headers = new Headers()
          for (const [k, v] of Object.entries(res.headers as IncomingHttpHeaders)) {
            if (typeof v === 'string') headers.set(k, v)
          }
          resolve({ status: res.statusCode ?? 0, headers, text, body: parseBody(text) })
        })
      },
    )
    req.on('error', reject)
    if (payload !== undefined) req.write(payload)
    req.end()
  })

export const errorMessage = (r: ApiResponse): string =>
  String((r.body.error as { message?: unknown } | undefined)?.message ?? r.body.message ?? '')

export interface SseEvent {
  event: string
  data: Record<string, unknown>
}

/** The named events of an upload's Server-Sent Events response, comments dropped. */
export const sseEvents = (text: string): SseEvent[] =>
  text
    .split('\n\n')
    .map((block) => block.split('\n'))
    .filter((lines) => lines.some((l) => l.startsWith('event: ')))
    .map((lines) => {
      const event = (lines.find((l) => l.startsWith('event: ')) ?? '').slice('event: '.length)
      const data = lines.find((l) => l.startsWith('data: '))
      return { event, data: data ? (JSON.parse(data.slice('data: '.length)) as Record<string, unknown>) : {} }
    })

/** Text that must never reach a person reading an error. */
export const INTERNAL_DETAIL = [/Traceback/i, /\bat [\w.<>]+ \(/, /127\.0\.0\.1/, /ECONNREFUSED/, /status code \d{3}/i, /\bstack\b/i]

/** Backend calls other than to `except`, so a test can say nothing else was reached. */
export const callsExcept = (h: KbHarness, ...except: string[]): string[] =>
  h.backend.calls.map((c) => `${c.method} ${c.path}`).filter((c) => !except.includes(c))

export const RECORD_GROUP_ID = 'rg-7c1a90e4d2f'

export const pdfForm = (...names: string[]): FormData => {
  const form = new FormData()
  for (const name of names) form.append('files', new Blob(['%PDF-1.4 tiny'], { type: 'application/pdf' }), name)
  return form
}

export interface KbRoute {
  method: string
  /** As declared on the router, to check this table covers every route. */
  pattern: string
  path: string
  json?: unknown
  form?: () => FormData
  scope: 'kb:read' | 'kb:write' | 'kb:delete' | 'kb:upload'
  /** The first call the route makes to the connector service, when it makes one. */
  forwards?: string
  /** What the connector answers with for the route to succeed. */
  reply?: { status: number; body?: unknown; raw?: string }
}

export const KB_ROUTES: KbRoute[] = [
  { method: 'POST', pattern: '/', path: '/', json: { kbName: 'Handbook' }, scope: 'kb:write', forwards: 'POST /api/v1/kb/', reply: { status: 200, body: { id: KB_ID, name: 'Handbook' } } },
  { method: 'GET', pattern: '/', path: '/', scope: 'kb:read', forwards: 'GET /api/v1/kb/', reply: { status: 200, body: { knowledgeBases: [] } } },
  { method: 'GET', pattern: '/demo-data/status', path: '/demo-data/status', scope: 'kb:read', forwards: 'GET /api/v1/demo-data/status', reply: { status: 200, body: { include: true } } },
  { method: 'PUT', pattern: '/demo-data/preference', path: '/demo-data/preference', json: { include: false }, scope: 'kb:write', forwards: 'PUT /api/v1/demo-data/preference', reply: { status: 200, body: { include: false } } },
  { method: 'GET', pattern: '/knowledge-hub/nodes', path: '/knowledge-hub/nodes', scope: 'kb:read', forwards: 'GET /api/v1/knowledge-hub/nodes', reply: { status: 200, body: { items: [] } } },
  { method: 'GET', pattern: '/knowledge-hub/nodes/:parentType/:parentId', path: `/knowledge-hub/nodes/kb/${KB_ID}`, scope: 'kb:read', forwards: `GET /api/v1/knowledge-hub/nodes/kb/${KB_ID}`, reply: { status: 200, body: { items: [] } } },
  { method: 'GET', pattern: '/record/:recordId', path: `/record/${RECORD_ID}`, scope: 'kb:read', forwards: `GET /api/v1/records/${RECORD_ID}`, reply: { status: 200, body: { record: { id: RECORD_ID } } } },
  { method: 'PUT', pattern: '/record/:recordId', path: `/record/${RECORD_ID}`, json: { recordName: 'Renamed' }, scope: 'kb:write', forwards: `PUT /api/v1/kb/record/${RECORD_ID}`, reply: { status: 200, body: { updatedRecord: { id: RECORD_ID, recordName: 'Renamed' } } } },
  { method: 'DELETE', pattern: '/record/:recordId', path: `/record/${RECORD_ID}`, scope: 'kb:delete', forwards: `DELETE /api/v1/records/${RECORD_ID}`, reply: { status: 200, body: { success: true } } },
  { method: 'GET', pattern: '/stream/record/:recordId', path: `/stream/record/${RECORD_ID}`, scope: 'kb:read', forwards: `GET /api/v1/stream/record/${RECORD_ID}`, reply: { status: 200, raw: 'file bytes' } },
  { method: 'POST', pattern: '/reindex/record/:recordId', path: `/reindex/record/${RECORD_ID}`, json: {}, scope: 'kb:write', forwards: `POST /api/v1/records/${RECORD_ID}/reindex`, reply: { status: 200, body: { success: true } } },
  { method: 'POST', pattern: '/reindex/record-group/:recordGroupId', path: `/reindex/record-group/${RECORD_GROUP_ID}`, json: {}, scope: 'kb:write', forwards: `POST /api/v1/record-groups/${RECORD_GROUP_ID}/reindex`, reply: { status: 200, body: { success: true } } },
  { method: 'GET', pattern: '/limits', path: '/limits', scope: 'kb:read' },
  { method: 'GET', pattern: '/:kbId', path: `/${KB_ID}`, scope: 'kb:read', forwards: `GET /api/v1/kb/${KB_ID}`, reply: { status: 200, body: { id: KB_ID, userRole: 'OWNER' } } },
  { method: 'PUT', pattern: '/:kbId', path: `/${KB_ID}`, json: { kbName: 'Renamed' }, scope: 'kb:write', forwards: `PUT /api/v1/kb/${KB_ID}`, reply: { status: 200, body: { id: KB_ID } } },
  { method: 'DELETE', pattern: '/:kbId', path: `/${KB_ID}`, scope: 'kb:delete', forwards: `DELETE /api/v1/kb/${KB_ID}`, reply: { status: 200, body: { success: true } } },
  { method: 'POST', pattern: '/:kbId/upload', path: `/${KB_ID}/upload`, form: () => pdfForm('a.pdf'), scope: 'kb:upload', forwards: `GET /api/v1/kb/${KB_ID}`, reply: { status: 200, body: { id: KB_ID, userRole: 'OWNER' } } },
  { method: 'POST', pattern: '/:kbId/folder', path: `/${KB_ID}/folder`, json: { folderName: 'Specs' }, scope: 'kb:write', forwards: `POST /api/v1/kb/${KB_ID}/folder`, reply: { status: 201, body: { id: FOLDER_ID } } },
  { method: 'PUT', pattern: '/:kbId/folder/:folderId', path: `/${KB_ID}/folder/${FOLDER_ID}`, json: { folderName: 'Specs v2' }, scope: 'kb:write', forwards: `PUT /api/v1/kb/${KB_ID}/folder/${FOLDER_ID}`, reply: { status: 200, body: { success: true } } },
  { method: 'DELETE', pattern: '/:kbId/folder/:folderId', path: `/${KB_ID}/folder/${FOLDER_ID}`, scope: 'kb:delete', forwards: `DELETE /api/v1/kb/${KB_ID}/folder/${FOLDER_ID}`, reply: { status: 200, body: { success: true } } },
  { method: 'POST', pattern: '/:kbId/permissions', path: `/${KB_ID}/permissions`, json: { userIds: [MEMBER._id], teamIds: [], role: 'READER' }, scope: 'kb:write', forwards: `POST /api/v1/kb/${KB_ID}/permissions`, reply: { status: 201, body: { success: true } } },
  { method: 'GET', pattern: '/:kbId/permissions', path: `/${KB_ID}/permissions`, scope: 'kb:read', forwards: `GET /api/v1/kb/${KB_ID}/permissions`, reply: { status: 200, body: { permissions: [], totalCount: 0 } } },
  { method: 'PUT', pattern: '/:kbId/permissions', path: `/${KB_ID}/permissions`, json: { userIds: [MEMBER._id], teamIds: [], role: 'WRITER' }, scope: 'kb:write', forwards: `PUT /api/v1/kb/${KB_ID}/permissions`, reply: { status: 200, body: { userIds: [MEMBER._id], teamIds: [], newRole: 'WRITER' } } },
  { method: 'DELETE', pattern: '/:kbId/permissions', path: `/${KB_ID}/permissions`, json: { userIds: [MEMBER._id], teamIds: [] }, scope: 'kb:delete', forwards: `DELETE /api/v1/kb/${KB_ID}/permissions`, reply: { status: 200, body: { userIds: [MEMBER._id], teamIds: [] } } },
  { method: 'PUT', pattern: '/:kbId/record/:recordId/move', path: `/${KB_ID}/record/${RECORD_ID}/move`, json: { newParentId: FOLDER_ID }, scope: 'kb:write', forwards: `PUT /api/v1/kb/${KB_ID}/record/${RECORD_ID}/move`, reply: { status: 200, body: { success: true } } },
]

export const callRoute = (h: KbHarness, route: KbRoute, token?: string, headers?: Record<string, string>): Promise<ApiResponse> =>
  call(h, route.method, route.path, { token, json: route.json, form: route.form?.(), headers })

export const stubRoute = (h: KbHarness, route: KbRoute): void => {
  if (!route.forwards || !route.reply) return
  const [method, path] = route.forwards.split(' ') as [string, string]
  h.backend.on(method, path, route.reply)
}

/** Every route the router declares, as `METHOD /pattern`. */
export const declaredRoutes = (router: Router): string[] =>
  router.stack.flatMap((layer) => {
    const route = (layer as unknown as { route?: { path: string; methods: Record<string, boolean> } }).route
    if (!route) return []
    return Object.keys(route.methods).map((m) => `${m.toUpperCase()} ${route.path}`)
  })
