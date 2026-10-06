import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { readFileSync } from 'fs'
import { join } from 'path'
import { Container } from 'inversify'
import { Router } from 'express'
import yaml from 'js-yaml'
import { createAgentConversationalRouter, createConversationalRouter } from '../../../src/modules/enterprise_search/routes/es.routes'
import { createNotificationRouter } from '../../../src/modules/notification/routes/notification.routes'
import { createAuthzRouter } from '../../../src/modules/authz/routes/authz.routes'
import { createAuthzInternalRouter } from '../../../src/modules/authz/routes/authz.internal.routes'
import { COLLAB_TYPES } from '../../../src/modules/enterprise_search/services/collaboration/collab.types'
import { COLLAB_ERROR_CODES } from '../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { MENTION_ERROR_CODES } from '../../../src/modules/enterprise_search/services/collaboration/mentions/mention.errors'
import { MUTED_SESSIONS_LIMIT_CODE } from '../../../src/modules/notification/controllers/notification-preferences.controller'
import { CONVERSATION_ROUTES, COLLABORATION_ROUTES, MENTION_ROUTES, ConversationRoute } from '../enterprise_search/helpers/conversation-routes'
import { bindCollaborationStubs } from '../enterprise_search/helpers/collaboration-world'
import { markingGuards } from '../enterprise_search/helpers/guarded-chat'
import { turnDeps } from '../enterprise_search/helpers/turn-deps'

/**
 * NF-03: every route that PH-04..PH-11 registered is in the published spec, read off the routers themselves (not
 * off a hand-kept list), and the error codes the spec promises for it are codes the server really sends.
 */

type Json = Record<string, any>
type Layer = { route?: { path: string; methods: Record<string, boolean> } }

const spec = yaml.load(readFileSync(join(__dirname, '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml'), 'utf8')) as Json

const passthrough = () => sinon.stub().callsFake((_req, _res, next) => next())

function containerFor(): Container {
  const c = new Container()
  c.bind('AuthMiddleware').toConstantValue({ authenticate: passthrough(), scopedTokenValidator: () => passthrough() })
  c.bind('AppConfig').toConstantValue({ aiBackend: 'http://ai', jwtSecret: 'j', scopedJwtSecret: 's' })
  c.bind(COLLAB_TYPES.ConversationGuards).toConstantValue(markingGuards())
  c.bind(COLLAB_TYPES.ConversationTurnDeps).toConstantValue(turnDeps())
  bindCollaborationStubs(c)
  return c
}

interface Mounted {
  method: string
  /** Path as the router spells it. */
  path: string
  /** Path as the spec spells it. */
  specPath: string
}

function mountedOn(router: Router, prefix: string): Mounted[] {
  return (router.stack as Layer[])
    .filter((l) => l.route)
    .flatMap((l) => Object.keys(l.route!.methods).map((method) => ({ method, path: l.route!.path, specPath: prefix + l.route!.path.replace(/:(\w+)/g, '{$1}') })))
    .filter((r) => r.specPath !== prefix + '/')
}

const isInternal = (r: Mounted): boolean => r.path.includes('/internal/') || r.path.startsWith('/internal')

/** Routes that predate PH-04 and were never in the spec; not part of this project's documentation pass. */
const PRE_EXISTING_UNDOCUMENTED = new Set(['get /agents/web-search-usage/{provider}', 'get /agents/model-usage/{model_key}'])

let chatRoutes: Mounted[]
let agentRoutes: Mounted[]
let notificationRoutes: Mounted[]
let authzRoutes: Mounted[]

before(() => {
  const chatC = containerFor()
  chatRoutes = mountedOn(createConversationalRouter(chatC), '/conversations')
  agentRoutes = mountedOn(createAgentConversationalRouter(containerFor()), '/agents')

  const noteC = new Container()
  noteC.bind('AuthMiddleware').toConstantValue({ authenticate: passthrough() })
  noteC.bind(COLLAB_TYPES.FeatureFlags).toConstantValue({ isEnabled: async () => true })
  noteC.bind(COLLAB_TYPES.ConversationGuards).toConstantValue(markingGuards())
  notificationRoutes = mountedOn(createNotificationRouter(noteC), '/notifications')

  const authzC = new Container()
  authzC.bind('AuthMiddleware').toConstantValue({ authenticate: passthrough(), scopedTokenValidator: () => passthrough() })
  authzC.bind(COLLAB_TYPES.FeatureFlags).toConstantValue({ isEnabled: async () => true })
  authzC.bind(COLLAB_TYPES.ConversationGuards).toConstantValue(markingGuards())
  authzC.bind(COLLAB_TYPES.ChatContentCheckService).toConstantValue({ check: async () => undefined })
  authzC.bind(COLLAB_TYPES.AuthzController).toConstantValue({ explain: () => undefined, preview: () => undefined, check: () => undefined })
  authzRoutes = [...mountedOn(createAuthzRouter(authzC), '/authz'), ...mountedOn(createAuthzInternalRouter(authzC), '/authz')]
})

const resolve = (response: Json): Json => (response.$ref ? spec.components.responses[(response.$ref as string).replace('#/components/responses/', '')] : response)

const KNOWN_CODES: string[] = [...Object.values(COLLAB_ERROR_CODES), ...Object.values(MENTION_ERROR_CODES), MUTED_SESSIONS_LIMIT_CODE]
const backticked = (text: string): string[] => (text.match(/`([A-Z][A-Z_]+)`/g) ?? []).map((t) => t.slice(1, -1))
const knownCodesIn = (text: string): string[] => [...new Set(backticked(text).filter((c) => KNOWN_CODES.includes(c)))].sort()

/** Every code the operation names under a status, following `$ref`. */
const codesAt = (op: Json, status: string): string[] => {
  const response = op.responses?.[status]
  expect(response, `status ${status} is documented`).to.not.equal(undefined)
  return knownCodesIn(String(resolve(response).description ?? ''))
}

const operation = (specPath: string, method: string): Json => {
  const item = spec.paths[specPath]
  expect(item, `${specPath} is in the spec`).to.not.equal(undefined)
  const op = item[method]
  expect(op, `${method.toUpperCase()} ${specPath} is in the spec`).to.not.equal(undefined)
  return op
}

const specPathOf = (r: ConversationRoute): string =>
  (r.kind === 'agent' ? '/agents' : '/conversations') +
  r.path
    .replace('/:agentKey/conversations', r.kind === 'agent' ? '/{agentKey}/conversations' : '')
    .replace(':conversationId', '{conversationId}')
    .replace(':messageId', '{messageId}')
    .replace(':principalId', '{principalId}')

describe('OpenAPI completeness (NF-03)', () => {
  describe('every public route the routers register is documented', () => {
    const cases: Array<[string, () => Mounted[]]> = [
      ['conversation router', () => chatRoutes],
      ['agent router', () => agentRoutes],
      ['notification router', () => notificationRoutes],
      ['authz routers', () => authzRoutes],
    ]
    for (const [name, routes] of cases) {
      it(`${name}: each route has a spec entry under its own method`, () => {
        const list = routes().filter((r) => !isInternal(r) && !PRE_EXISTING_UNDOCUMENTED.has(`${r.method} ${r.specPath}`))
        expect(list.length, `${name} registers routes`).to.be.greaterThan(0)
        const missing = list.filter((r) => spec.paths[r.specPath]?.[r.method] === undefined).map((r) => `${r.method.toUpperCase()} ${r.specPath}`)
        expect(missing, `undocumented routes on the ${name}`).to.deep.equal([])
      })
    }

    it('the authz internal check is documented too (a service route, but the spec lists it)', () => {
      expect(authzRoutes.some((r) => r.specPath === '/authz/internal/check' && r.method === 'post')).to.equal(true)
      expect(spec.paths['/authz/internal/check'].post).to.not.equal(undefined)
    })

    it('the route tables the other suites iterate over are exactly what the routers register', () => {
      const chat = new Set(chatRoutes.map((r) => `${r.method} ${r.specPath}`))
      const agent = new Set(agentRoutes.map((r) => `${r.method} ${r.specPath}`))
      const tables = [...CONVERSATION_ROUTES, ...COLLABORATION_ROUTES, ...MENTION_ROUTES].filter((r) => !r.internal)
      const notRegistered = tables.filter((r) => {
        const key = `${r.method} ${specPathOf(r)}`
        return !(r.kind === 'chat' ? chat : agent).has(key)
      })
      expect(notRegistered.map((r) => r.id), 'table rows no router registers').to.deep.equal([])
      const known = new Set(tables.map((r) => `${r.method} ${specPathOf(r)}`))
      const outsideTables = [...chat, ...agent].filter((k) => k.includes('{conversationId}') && !k.includes('/internal/') && !known.has(k))
      expect(outsideTables, 'conversation routes the tables do not list, so no suite checks them').to.deep.equal([])
    })

    it('operation ids of the routes added in PH-04..PH-11 are unique', () => {
      const seen = new Map<string, string>()
      const dup: string[] = []
      for (const [path, item] of Object.entries(spec.paths as Json)) {
        for (const [method, op] of Object.entries(item as Json)) {
          const id = (op as Json)?.operationId as string | undefined
          if (id === undefined) continue
          if (seen.has(id)) dup.push(`${id}: ${seen.get(id)} and ${method} ${path}`)
          seen.set(id, `${method} ${path}`)
        }
      }
      expect(dup).to.deep.equal([])
    })
  })

  describe('error codes', () => {
    it('every server error code is documented on at least one route', () => {
      const text = JSON.stringify(spec.paths) + JSON.stringify(spec.components.responses)
      const absent = KNOWN_CODES.filter((c) => !text.includes(c))
      expect(absent).to.deep.equal([])
    })

    it('ConversationErrorCode lists the mention codes in its table beside the conversation codes', () => {
      const description = String(spec.components.schemas.ConversationErrorCode.description)
      for (const code of Object.values(MENTION_ERROR_CODES)) expect(description, code).to.include(`\`${code}\``)
    })
  })

  describe('mentions routes (PH-10)', () => {
    for (const r of MENTION_ROUTES) {
      const path = specPathOf(r)
      describe(`${r.id} ${r.method.toUpperCase()} ${path}`, () => {
        const op = (): Json => operation(path, r.method)

        it('is tagged for its kind, asks for one scope and says the flags gate it', () => {
          expect(op().tags).to.deep.equal([r.kind === 'chat' ? 'Conversations' : 'Agents'])
          const scopes = (op().security as Json[]).flatMap((s) => s.oauth2 ?? [])
          expect(scopes).to.deep.equal([r.path.endsWith('/notes') ? 'conversation:chat' : 'conversation:read'])
          expect(op().description).to.include('ENABLE_CHAT_MENTIONS')
          expect(op().description).to.include('ENABLE_COLLABORATIVE_CHATS')
        })

        it('404 is the not-found response', () => {
          expect(op().responses['404'].$ref).to.equal('#/components/responses/ConversationNotFound')
        })

        if (r.path.endsWith('/notes')) {
          it('documents what a refused mention looks like and every refusal a note can meet', () => {
            expect(codesAt(op(), '400')).to.include('MENTION_NOT_ALLOWED')
            expect(codesAt(op(), '503')).to.include('MENTION_DIRECTORY_UNAVAILABLE')
            expect(codesAt(op(), '422')).to.deep.equal(['MESSAGE_NOT_NOTE'])
            expect(codesAt(op(), '409')).to.deep.equal(['DUPLICATE_MESSAGE'])
            expect(codesAt(op(), '403')).to.include.members(['CONVERSATION_READ_ONLY', 'MENTION_NOT_ALLOWED', 'MENTION_SA_AGENT_SHARED'])
          })
          it('takes the note request and returns the note, 200 for a repeat', () => {
            expect(op().requestBody.content['application/json'].schema.$ref).to.equal('#/components/schemas/CreateNoteRequest')
            expect(op().responses['201']).to.not.equal(undefined)
            expect(op().responses['200']).to.not.equal(undefined)
          })
        } else {
          it('takes q and a limit of at most 20', () => {
            const params = op().parameters as Json[]
            expect(params.map((p) => p.name)).to.include.members(['q', 'limit'])
            expect(params.find((p) => p.name === 'limit')!.schema.maximum).to.equal(20)
          })
        }
      })
    }

    it('every route that sends a message names MESSAGE_IS_NOTE and the mention refusals', () => {
      const sends = [...CONVERSATION_ROUTES].filter((r) => !r.internal && ['C1', 'C3', 'A1', 'A2'].includes(r.id))
      expect(sends).to.have.length(4)
      for (const r of sends) {
        const op = operation(specPathOf(r), r.method)
        const text = JSON.stringify(op) + JSON.stringify(Object.values(op.responses).map((x) => resolve(x as Json)))
        for (const code of ['MESSAGE_IS_NOTE', 'MENTION_NOT_ALLOWED', 'MENTION_DIRECTORY_UNAVAILABLE']) expect(text, `${r.id} names ${code}`).to.include(code)
      }
    })

    it('the request schemas carry mentions and the respond mode', () => {
      const { schemas } = spec.components
      expect(schemas.MentionRef.properties.type.enum).to.include.members(['user', 'team', 'assistant', 'agent'])
      expect(schemas.RespondMode.enum).to.deep.equal(['smart', 'mention_only', 'always'])
      expect(schemas.CollaborationSettings.properties.respondMode.$ref).to.equal('#/components/schemas/RespondMode')
    })
  })

  describe('notification preferences and muting (PH-06, PH-10)', () => {
    const preferenceRoutes = (): Mounted[] => notificationRoutes.filter((r) => r.path.startsWith('/preferences'))

    it('the router registers the five preference routes', () => {
      expect(preferenceRoutes().map((r) => `${r.method} ${r.path}`).sort()).to.deep.equal(
        ['delete /preferences/muted-sessions/:sessionId', 'get /preferences', 'patch /preferences', 'patch /preferences/tips', 'put /preferences/muted-sessions/:sessionId'].sort(),
      )
    })

    it('each is authenticated and says the flag gates it', () => {
      for (const r of preferenceRoutes()) {
        const op = operation(r.specPath, r.method)
        expect(op.security, `${r.method} ${r.specPath}`).to.not.equal(undefined)
        expect(String(op.description)).to.match(/ENABLE_COLLABORATIVE_CHATS/)
        expect(op.responses['404'], 'the flag-off 404').to.not.equal(undefined)
      }
    })

    it('muting documents the 500-chat limit as a 409 MUTED_SESSIONS_LIMIT', () => {
      const put = operation('/notifications/preferences/muted-sessions/{sessionId}', 'put')
      expect(codesAt(put, '409')).to.deep.equal([MUTED_SESSIONS_LIMIT_CODE])
      expect(String(resolve(put.responses['409']).description)).to.match(/500/)
    })

    it('the tip route is documented as needing the mentions flag', () => {
      expect(String(operation('/notifications/preferences/tips', 'patch').description)).to.include('ENABLE_CHAT_MENTIONS')
    })
  })

  describe('agents created from a chat (PH-11)', () => {
    it('create documents every refusal the draft path can give', () => {
      const create = operation('/agents/create', 'post')
      const text = JSON.stringify(Object.values(create.responses).map((x) => resolve(x as Json)))
      for (const code of ['INVALID_KNOWLEDGE', 'INVALID_TOOLSET', 'SERVICE_ACCOUNT_NOT_ALLOWED', 'HANDLE_RESERVED', 'HANDLE_TAKEN', 'CONVERSATION_NOT_FOUND']) {
        expect(text, code).to.include(code)
      }
      expect(String(create.description)).to.include('ENABLE_CHAT_AGENT_BUILDER')
    })

    it('handle-availability is registered on the agents router and documented', () => {
      expect(agentRoutes.some((r) => r.method === 'get' && r.path === '/handle-availability')).to.equal(true)
      expect(operation('/agents/handle-availability', 'get').security).to.not.equal(undefined)
    })
  })

  describe('conversation routes carry the lifecycle each phase added', () => {
    it('every public conversation route documents 401, 404 and 503 (team resolution)', () => {
      const missing: string[] = []
      for (const r of [...CONVERSATION_ROUTES, ...COLLABORATION_ROUTES].filter((x) => !x.internal)) {
        const op = operation(specPathOf(r), r.method)
        for (const status of ['401', '404', '503']) if (op.responses[status] === undefined) missing.push(`${r.id} ${status}`)
      }
      expect(missing).to.deep.equal([])
    })

    it('429 is documented on every route behind a keyed rate limiter', () => {
      const limited = COLLABORATION_ROUTES.filter((r) => ['invite', 'manageCollaborators', 'settings', 'transfer', 'leave', 'read'].includes(r.op) && (r.method !== 'get' || r.path.endsWith('/feed')))
      for (const r of limited) expect(operation(specPathOf(r), r.method).responses['429'], `${r.id} 429`).to.not.equal(undefined)
      expect(operation('/authz/explain', 'get').responses['429']).to.not.equal(undefined)
      expect(operation('/authz/explain/preview', 'post').responses['429']).to.not.equal(undefined)
    })
  })
})
