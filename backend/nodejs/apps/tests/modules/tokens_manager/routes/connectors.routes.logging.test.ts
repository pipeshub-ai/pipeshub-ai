import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  ADMIN,
  Harness,
  call,
  sessionToken,
  startHarness,
} from './connectors-http-harness'
import axios from 'axios'
import * as axiosRetryModule from 'axios-retry'
import { Logger } from '../../../../src/libs/services/logger.service'
import { SERVICE_UNAVAILABLE_MESSAGE } from '../../../../src/libs/errors/backend-error'

const admin = ADMIN

// These modules take their logger once, at load, and a mocha worker shares
// module state across files, so whatever logger they got first is not ours to
// observe. Load private copies that log into the recorder, then put the cached
// originals back so later files in the same worker are unaffected. The routes
// module also calls axiosRetry() on the process-wide axios client when it
// loads; that call is skipped here so the copy adds no interceptors.
const RELOADED = [
  'libs/commands/connector_service/connector.service.command.ts',
  'libs/commands/configuration_manager/cm.service.command.ts',
  'modules/tokens_manager/utils/connector.utils.ts',
  'modules/tokens_manager/services/connectors-config.service.ts',
  'modules/tokens_manager/controllers/connector.controllers.ts',
  'modules/tokens_manager/routes/connectors.routes.ts',
]
type RoutesModule = typeof import('../../../../src/modules/tokens_manager/routes/connectors.routes')
const privateConnectorRouter = (): RoutesModule['createConnectorRouter'] => {
  const before = new Set(Object.keys(require.cache))
  const originals = new Map<string, NodeJS.Module | undefined>()
  for (const key of before) {
    if (RELOADED.some((suffix) => key.endsWith(suffix))) {
      originals.set(key, require.cache[key])
      delete require.cache[key]
    }
  }
  const strays: string[] = []
  let routes: RoutesModule
  const retry = sinon
    .stub(axiosRetryModule, 'default')
    .returns({ requestInterceptorId: -1, responseInterceptorId: -1 })
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    routes = require('../../../../src/modules/tokens_manager/routes/connectors.routes') as RoutesModule
  } finally {
    retry.restore()
    for (const key of Object.keys(require.cache)) {
      if (originals.has(key)) require.cache[key] = originals.get(key)
      else if (!before.has(key)) {
        delete require.cache[key]
        if (!RELOADED.some((suffix) => key.endsWith(suffix))) strays.push(key)
      }
    }
  }
  // Anything else loaded here would keep the recorder as its logger.
  if (strays.length > 0) throw new Error(`unexpected modules loaded: ${strays.join(', ')}`)
  return routes.createConnectorRouter
}

type Interceptors = { handlers: Array<unknown> }
const interceptorCounts = (): [number, number] => [
  (axios.interceptors.request as unknown as Interceptors).handlers.filter((h) => h !== null).length,
  (axios.interceptors.response as unknown as Interceptors).handlers.filter((h) => h !== null).length,
]

const CONNECTOR_ID = '3f2c9e7a-1b4d-4c8e-9a6f-2d5e8b7c1a90'
const JWT_SHAPE = /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/

const serialise = (value: unknown): string => {
  const seen = new WeakSet<object>()
  return JSON.stringify(value, (_key, v: unknown) => {
    if (v instanceof Error) return { message: v.message, stack: v.stack, cause: (v as { cause?: unknown }).cause }
    if (v !== null && typeof v === 'object') {
      if (seen.has(v)) return '[Circular]'
      seen.add(v)
    }
    return v
  })
}

describe('Connector routes: failed service calls keep credentials out of the logs', () => {
  let h: Harness
  let logged: Array<{ label: string; text: string }>

  // Report which log call leaked, never the leaked value itself.
  const leaking = (test: (text: string) => boolean): string[] =>
    logged.filter((entry) => test(entry.text)).map((entry) => entry.label)

  beforeEach(async () => {
    logged = []
    const record =
      (level: string) =>
      (message: string, meta?: unknown): void => {
        logged.push({ label: `${level}: ${message}`, text: `${message} ${serialise(meta)}` })
      }
    const recorder = { error: record('error'), warn: record('warn'), info: record('info'), debug: record('debug') }
    const getInstance = sinon.stub(Logger, 'getInstance').returns(recorder as unknown as Logger)
    const createRouter = privateConnectorRouter()
    getInstance.restore()
    h = await startHarness({ createRouter })
  })

  it('loads its private copy without adding retry interceptors to the shared axios client', () => {
    const before = interceptorCounts()

    privateConnectorRouter()
    privateConnectorRouter()

    expect(interceptorCounts()).to.deep.equal(before)
  })

  afterEach(async () => {
    sinon.restore()
    await h.close()
  })

  it("does not log the caller's token or the connector secret when the connector service is unreachable", async () => {
    h.backend.on('PUT', `/api/v1/connectors/${CONNECTOR_ID}/config/auth`, 'drop')
    const token = sessionToken(h, admin)

    const r = await call(h, 'PUT', `/${CONNECTOR_ID}/config/auth`, token, {
      auth: { clientId: 'client-abc', clientSecret: 'client-secret-do-not-log' },
    })

    expect(r.status).to.equal(503)
    expect(r.body.error).to.include({ message: SERVICE_UNAVAILABLE_MESSAGE })
    expect(logged.map((e) => e.label)).to.include('error: Connector service command failed')
    expect(
      leaking((text) => text.includes(token) || JWT_SHAPE.test(text) || text.includes('client-secret-do-not-log')),
    ).to.deep.equal([])
  })

  it('does not log the OAuth callback code or state when the connector service is unreachable', async () => {
    h.backend.on('GET', '/api/v1/connectors/oauth/callback', 'drop')

    const r = await call(h, 'GET', '/oauth/callback?code=oauth-code-do-not-log&state=oauth-state-do-not-log', sessionToken(h, admin))

    expect(r.status).to.equal(503)
    expect(logged.map((e) => e.label)).to.include('error: Connector service command failed')
    expect(leaking((text) => text.includes('oauth-code-do-not-log') || text.includes('oauth-state-do-not-log'))).to.deep.equal([])
  })

  it('does not log the internal config token when the configuration manager is unreachable', async () => {
    h.backend.on('GET', '/api/v1/configurationManager/internal/connectors/googleWorkspaceOauthConfig', 'drop')

    const r = await call(h, 'POST', '/getTokenFromCode', sessionToken(h, admin), { tempCode: 'code-1' })

    expect(r.status).to.be.within(500, 599)
    expect(logged.map((e) => e.label)).to.include('error: Configuration Manager service command failed')
    expect(leaking((text) => JWT_SHAPE.test(text))).to.deep.equal([])
  })
})
