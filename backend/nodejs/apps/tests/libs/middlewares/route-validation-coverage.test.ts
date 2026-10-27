import 'reflect-metadata'
import { expect } from 'chai'
import fs from 'fs'
import path from 'path'
import sinon from 'sinon'
import { Container } from 'inversify'
import { ValidationMiddleware } from '../../../src/libs/middlewares/validation.middleware'

const SRC = path.resolve(__dirname, '../../../src')

const WRITE_METHODS = ['post', 'put', 'patch'] as const

// Write routes that run without ValidationMiddleware today. Each one either
// takes no body, validates it some other way, or is a follow-up to give a Zod
// schema. A route added to a router must not land here without a reason: a
// handler that reads req.body unchecked is how a `{"$ne": null}` reaches a
// Mongo filter.
const ROUTES_WITHOUT_VALIDATION = new Set<string>([
  // Config-change notices from other PipesHub services: scoped token, no body read.
  'createSemanticSearchRouter POST /updateAppConfig',
  'createMailServiceRouter POST /updateSmtpConfig',
  'createStorageRouter POST /updateAppConfig',
  'createConnectorRouter POST /updateAppConfig',
  'createUserRouter POST /updateAppConfig',
  'createUserAccountRouter POST /updateAppConfig',

  // Multipart uploads: the file processor checks the file, and no field of the
  // body reaches a query.
  'createConfigurationManagerRouter POST /connectors/googleWorkspaceCredentials',
  'createConfigurationManagerRouter POST /internal/connectors/googleWorkspaceCredentials',
  'createChatSpeechRouter POST /transcribe',
  'createOrgRouter PUT /logo',
  'createUserRouter PUT /dp',
  'createUserRouter POST /bulk/invite/upload',

  // Identity flows: these read their subject from a verified token rather than the body.
  'createUserAccountRouter POST /refresh/token',
  'createUserAccountRouter POST /logout/manual',
  'createUserAccountRouter POST /password/reset/token',
  'createUserAccountRouter PUT /validateEmailChange',

  // JSON-RPC, parsed and checked by the MCP SDK against each tool's schema.
  'createMCPRouter POST /',

  // Passed through to a Python service, which validates the body with its own
  // models; Node reads nothing from it but path params.
  'createAgentConversationalRouter POST /:agentKey/conversations/internal/:conversationId/messages/stream',
  'createAgentConversationalRouter POST /:agentKey/conversations/internal/stream',
  'createChatSpeechRouter POST /speak',
  'createConnectorRouter POST /vector-store/cleanup',
  'createConnectorRouter POST /vector-store/reindex',
  'createMcpServersRouter POST /oauth/discover',
  'createMcpServersRouter POST /instances',
  'createMcpServersRouter PUT /instances/:instanceId',
  'createMcpServersRouter PUT /instances/:instanceId/my-tool-rules',
  'createMcpServersRouter POST /instances/:instanceId/authenticate',
  'createMcpServersRouter PUT /instances/:instanceId/credentials',
  'createMcpServersRouter POST /instances/:instanceId/auto-authenticate',
  'createMcpServersRouter POST /instances/:instanceId/reauthenticate',
  'createMcpServersRouter POST /instances/:instanceId/oauth/refresh',
  'createMcpServersRouter PUT /instances/:instanceId/oauth-config',
  'createMcpServersRouter POST /agents/:agentKey/instances/:instanceId/authenticate',
  'createMcpServersRouter PUT /agents/:agentKey/instances/:instanceId/credentials',
  'createMcpServersRouter POST /agents/:agentKey/instances/:instanceId/reauthenticate',
  'createApiToolsetsRouter POST /validate',
  'createApiToolsetsRouter POST /instances',
  'createApiToolsetsRouter PUT /instances/:instanceId',
  'createApiToolsetsRouter PUT /instances/:instanceId/credentials',
  'createApiToolsetsRouter PUT /instances/:instanceId/shared-credentials',
  'createApiToolsetsRouter POST /instances/:instanceId/test',
  'createSkillsRouter POST /candidates/:candidateId/approve',
  'createSkillsRouter POST /candidates/:candidateId/reject',
  'createSkillsRouter POST /import/npm/preview',
  'createSkillsRouter POST /import/url/preview',
  'createSkillsRouter POST /import/upload/preview',
  'createSkillsRouter POST /import/finalize',
  'createSkillsRouter POST /',
  'createSkillsRouter PUT /:name',
  'createSkillsRouter PATCH /:name/body',
  'createSkillsRouter POST /:name/deprecate',
  'createSkillsRouter POST /:name/disable',
  'createSkillsRouter POST /:name/enable',
  'createSkillsRouter POST /:name/rollback',
  'createSkillsRouter PUT /:name/resource',
  'createToolsetsRouter PUT /instances/:instanceId',
  'createToolsetsRouter POST /instances/:instanceId/authenticate',
  'createToolsetsRouter POST /instances/:instanceId/reauthenticate',
  'createToolsetsRouter PUT /oauth-configs/:toolsetType/:oauthConfigId',
  'createToolsetsRouter POST /agents/:agentKey/instances/:instanceId/authenticate',
  'createToolsetsRouter POST /agents/:agentKey/instances/:instanceId/reauthenticate',

  // Follow-ups: these read body fields in Node and should get a Zod schema.
  'createOrgAuthConfigRouter POST /',
  'createUserAccountRouter POST /password/forgot',
  'createConfigurationManagerRouter PUT /prompts/system',
  'createConfigurationManagerRouter PUT /ai-models/roles',
  'createConfigurationManagerRouter POST /ai-models/prepare-model',
  'createConnectorRouter POST /getTokenFromCode',
  'createConnectorRouter POST /internal/refreshIndividualConnectorToken',
  'createMailServiceRouter POST /emails/sendEmail',
])

// Anything a router factory asks its container for: callable, and every
// property is another stand-in, so building a router needs no real services.
// `then` stays undefined so nothing mistakes a stand-in for a promise.
const standIn = (): any =>
  new Proxy(function standInFn() {}, {
    get: (_target, prop) => {
      if (prop === 'then') return undefined
      if (prop === Symbol.toPrimitive) return () => ''
      if (prop === Symbol.iterator) return undefined
      return standIn()
    },
    apply: () => standIn(),
    construct: () => standIn(),
  })

const fakeContainer = (): Container =>
  ({
    get: () => standIn(),
    getAll: () => [],
    isBound: () => true,
    getNamed: () => standIn(),
    getTagged: () => standIn(),
  }) as unknown as Container

const routeFiles = (): string[] =>
  (fs.readdirSync(path.join(SRC, 'modules'), { recursive: true }) as string[])
    .filter((file) => /(^|\/)routes\/[^/]+\.ts$/.test(file) || /(^|\/)[^/]+\.routes\.ts$/.test(file))
    .map((file) => path.join(SRC, 'modules', file))
    .sort()

interface Layer {
  route?: { path: string; methods: Record<string, boolean>; stack: Layer[] }
  handle: ((...args: unknown[]) => unknown) & { stack?: Layer[] }
  regexp?: RegExp
  name?: string
}

const mountPrefix = (layer: Layer): string => {
  const source = layer.regexp?.source ?? ''
  const match = source.match(/^\^\\\/(.*?)\\\/\?\(\?=\\\/\|\$\)$/)
  return match?.[1] ? `/${match[1].replace(/\\\//g, '/')}` : ''
}

const collectWriteRoutes = (
  stack: Layer[],
  validators: WeakSet<object>,
  prefix: string,
  inheritedValidation: boolean,
  out: Array<{ route: string; validated: boolean }>,
): void => {
  let routerWideValidation = inheritedValidation
  for (const layer of stack) {
    if (layer.route) {
      const validated =
        routerWideValidation || layer.route.stack.some((l) => validators.has(l.handle))
      for (const method of WRITE_METHODS) {
        if (layer.route.methods[method]) {
          out.push({ route: `${method.toUpperCase()} ${prefix}${layer.route.path}`, validated })
        }
      }
    } else if (Array.isArray(layer.handle?.stack)) {
      collectWriteRoutes(layer.handle.stack, validators, prefix + mountPrefix(layer), routerWideValidation, out)
    } else if (validators.has(layer.handle)) {
      routerWideValidation = true
    }
  }
}

describe('Every write route validates its input', () => {
  const routes: Array<{ file: string; route: string; validated: boolean }> = []
  const unbuildable: string[] = []

  before(async function () {
    this.timeout(60000)
    const validators = new WeakSet<object>()
    const original = ValidationMiddleware.validate.bind(ValidationMiddleware)
    // The health router reads its config, which insists on a key, when built.
    const secretKey = process.env.SECRET_KEY
    process.env.SECRET_KEY = secretKey ?? 'route-validation-coverage-test-key'
    const validate = sinon.stub(ValidationMiddleware, 'validate').callsFake((schema, options) => {
      const handler = original(schema, options)
      validators.add(handler)
      return handler
    })
    try {
      for (const file of routeFiles()) {
        const mod = (await import(file)) as Record<string, unknown>
        const factories = Object.entries(mod).filter(
          ([name, value]) => typeof value === 'function' && /^create\w*Router$/.test(name),
        )
        for (const [name, factory] of factories) {
          let router: { stack: Layer[] }
          try {
            router = (factory as (...c: Container[]) => { stack: Layer[] })(fakeContainer(), fakeContainer())
          } catch (error) {
            unbuildable.push(`${name}: ${error instanceof Error ? error.message : String(error)}`)
            continue
          }
          const found: Array<{ route: string; validated: boolean }> = []
          collectWriteRoutes(router.stack, validators, '', false, found)
          const rel = path.relative(SRC, file)
          for (const r of found) routes.push({ file: rel, route: `${name} ${r.route}`, validated: r.validated })
        }
      }
    } finally {
      validate.restore()
      if (secretKey === undefined) delete process.env.SECRET_KEY
      else process.env.SECRET_KEY = secretKey
    }
  })

  it('finds the write routes of every router factory', () => {
    expect(unbuildable, 'router factories that could not be built from stand-ins').to.deep.equal([])
    expect(routes.length).to.be.greaterThan(100)
  })

  it('puts ValidationMiddleware on every POST, PUT and PATCH route that is not allowlisted', () => {
    const missing = routes.filter((r) => !r.validated && !ROUTES_WITHOUT_VALIDATION.has(r.route)).map((r) => r.route)
    expect(missing, 'add a Zod schema with ValidationMiddleware.validate, or allowlist it with a reason').to.deep.equal([])
  })

  it('keeps the allowlist to routes that still exist and still lack validation', () => {
    const unvalidated = new Set(routes.filter((r) => !r.validated).map((r) => r.route))
    const stale = [...ROUTES_WITHOUT_VALIDATION].filter((r) => !unvalidated.has(r))
    expect(stale, 'remove these from ROUTES_WITHOUT_VALIDATION').to.deep.equal([])
  })
})
