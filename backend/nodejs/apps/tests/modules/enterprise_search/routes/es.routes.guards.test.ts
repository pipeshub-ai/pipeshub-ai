import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import { RequestHandler, Router } from 'express'
import {
  createAgentConversationalRouter,
  createConversationalRouter,
} from '../../../../src/modules/enterprise_search/routes/es.routes'
import { createProjectsRouter } from '../../../../src/modules/projects/routes/project.routes'
import { createArtifactsRouter } from '../../../../src/modules/artifacts/routes/artifacts.routes'
import { COLLAB_TYPES } from '../../../../src/modules/enterprise_search/services/collaboration/collab.types'
import { guardMarkOf } from '../../../../src/modules/enterprise_search/services/collaboration/http/conversation-guards'
import * as hydrate from '../../../../src/modules/enterprise_search/services/collaboration/http/hydrate-scoped-user.middleware'
import { ConversationOperation } from '../../../../src/modules/enterprise_search/services/collaboration/domain/types'
import { markingGuards } from '../helpers/guarded-chat'
import { turnDeps } from '../helpers/turn-deps'
import { COLLABORATION_ROUTES, CONVERSATION_ROUTES, MENTION_ROUTES, routeKey } from '../helpers/conversation-routes'
import { bindCollaborationStubs } from '../helpers/collaboration-world'

type Kind = 'chat' | 'agent'
type Mark = Record<string, unknown>
type Layer = { route?: { path: string; methods: Record<string, boolean>; stack: Array<{ handle: RequestHandler }> } }

const HYDRATE = Symbol('hydrateScopedUser')

const passthrough = () => sinon.stub().callsFake((_req, _res, next) => next())

function container(withGuards = true): Container {
  const c = new Container()
  c.bind('AuthMiddleware').toConstantValue({ authenticate: passthrough(), scopedTokenValidator: () => passthrough() })
  c.bind('AppConfig').toConstantValue({ aiBackend: 'http://ai', jwtSecret: 'j', scopedJwtSecret: 's' })
  if (withGuards) c.bind(COLLAB_TYPES.ConversationGuards).toConstantValue(markingGuards())
  c.bind(COLLAB_TYPES.ConversationTurnDeps).toConstantValue(turnDeps())
  bindCollaborationStubs(c)
  return c
}

interface RouteRow {
  key: string
  path: string
  /** Mark of each handler in mount order; `undefined` for plain middleware. */
  marks: Array<Mark | undefined>
  hydrateAt: number
  length: number
}

function rowsOf(router: Router): RouteRow[] {
  return (router.stack as Layer[])
    .filter((l) => l.route)
    .map((l) => {
      const route = l.route!
      const handlers = route.stack.map((s) => s.handle)
      return {
        key: `${Object.keys(route.methods)[0]!.toUpperCase()} ${route.path}`,
        path: route.path,
        marks: handlers.map((h) => guardMarkOf(h) as Mark | undefined),
        hydrateAt: handlers.findIndex((h) => (h as unknown as Record<symbol, boolean>)[HYDRATE] === true),
        length: handlers.length,
      }
    })
}

const authorize = (op: ConversationOperation, kind: Kind): Mark => ({ op, kind })
const lease = (kind: Kind): Mark => ({ lease: true, kind })

/** Ops whose route also runs `runLease()`: sends (PH-05 5c) and regenerate (5e). Resume rides on the send routes. */
const LEASED_OPS = new Set<ConversationOperation>(['send', 'regenerate'])

/** The guards a conversation route carries, in mount order. */
const expectedGuards = (op: ConversationOperation, kind: Kind): Mark[] => [authorize(op, kind), ...(LEASED_OPS.has(op) ? [lease(kind)] : [])]
const CALLER: Mark = { caller: true }
const list = (kind: Kind | 'any'): Mark => ({ list: true, kind })

/** A note takes no lease: `notes` is authorized for `send` and stops there (PR-10.4). */
const guardsForRoute = (r: (typeof CONVERSATION_ROUTES)[number]): Mark[] => (r.path.endsWith('/notes') ? [authorize(r.op, r.kind)] : expectedGuards(r.op, r.kind))

const tableFor = (kind: Kind): Record<string, Mark[]> =>
  Object.fromEntries(
    [...CONVERSATION_ROUTES, ...COLLABORATION_ROUTES, ...MENTION_ROUTES].filter((r) => r.kind === kind).map((r) => [routeKey(r), guardsForRoute(r)]),
  )
const CHAT_ROUTES = tableFor('chat')
const AGENT_ROUTES = tableFor('agent')
const AGENT = '/:agentKey/conversations'

/** Routes without `:conversationId` that carry a caller or list guard; anything else must have no mark at all. */
const CHAT_OTHER: Record<string, Mark | null> = {
  'POST /create': CALLER,
  'POST /internal/create': CALLER,
  'POST /stream': CALLER,
  'POST /internal/stream': CALLER,
  'GET /': list('chat'),
  'GET /show/archives': list('chat'),
  'GET /show/archives/search': list('chat'),
  // Attachments: the Python service checks the owner (allowlisted, PH-04 §3).
  'POST /attachments/upload': null,
  'POST /internal/attachments/upload': null,
  'DELETE /attachments/:recordId': null,
}

const AGENT_OTHER: Record<string, Mark | null> = {
  'GET /conversations/show/archives': CALLER, // grouped archive list is owner-only inside the handler
  [`POST ${AGENT}`]: CALLER,
  [`POST ${AGENT}/stream`]: CALLER,
  [`POST ${AGENT}/internal/stream`]: CALLER,
  [`GET ${AGENT}`]: list('agent'),
  [`GET ${AGENT}/show/archives`]: list('agent'),
  [`POST ${AGENT}/attachments/upload`]: null,
  [`POST ${AGENT}/internal/attachments/upload`]: null,
  [`DELETE ${AGENT}/attachments/:recordId`]: null,
  // Agent definition CRUD and usage: no conversation involved.
  'POST /create': null,
  'GET /handle-availability': null,
  'GET /:agentKey': null,
  'PUT /:agentKey': null,
  'DELETE /:agentKey': null,
  'GET /': null,
  'GET /web-search-usage/:provider': null,
  'GET /model-usage/:model_key': null,
}

const guardsOf = (row: RouteRow): Mark[] => row.marks.filter((m): m is Mark => m !== undefined)

function assertConversationRoute(row: RouteRow, expected: Mark[]): void {
  expect(guardsOf(row), `${row.key}: its guards, authorize first`).to.deep.equal(expected)
  const at = row.marks.findIndex((m) => m !== undefined)
  expect(at, `${row.key}: after auth, scopes and validation`).to.be.greaterThan(1)
  expect(at + expected.length, `${row.key}: the guards sit directly before the handler`).to.equal(row.length - 1)
  if (row.path.includes('/internal/')) {
    expect(row.hydrateAt, `${row.key}: scoped caller is hydrated first`).to.be.greaterThan(-1)
    expect(row.hydrateAt, `${row.key}: hydrateScopedUser runs before the guard`).to.be.lessThan(at)
  }
}

function assertRouter(rows: RouteRow[], conversationRoutes: Record<string, Mark[]>, others: Record<string, Mark | null>): void {
  const seen = new Set<string>()
  for (const row of rows) {
    seen.add(row.key)
    if (row.path.includes(':conversationId')) {
      expect(conversationRoutes, `${row.key} is in the PH-04 §3 table`).to.have.property(row.key)
      assertConversationRoute(row, conversationRoutes[row.key]!)
      continue
    }
    expect(others, `${row.key} must be either guarded or on the explicit allowlist`).to.have.property(row.key)
    const expected = others[row.key]
    expect(guardsOf(row), row.key).to.deep.equal(expected === null ? [] : [expected])
    if (expected !== null) expect(row.marks[row.length - 2], `${row.key}: guard directly before the handler`).to.deep.equal(expected)
  }
  for (const key of [...Object.keys(conversationRoutes), ...Object.keys(others)]) {
    expect(seen.has(key), `${key} is registered`).to.equal(true)
  }
}

describe('conversation routers: guard mounting (SEC-16)', () => {
  beforeEach(() => {
    sinon.stub(hydrate, 'hydrateScopedUser').callsFake(() =>
      Object.assign(((_req, _res, next) => next()) as RequestHandler, { [HYDRATE]: true }),
    )
  })
  afterEach(() => sinon.restore())

  it('chat router: C1-C26 carry their authorize guard (and runLease on sends), the rest are caller/list or allowlisted', () => {
    const rows = rowsOf(createConversationalRouter(container()))
    expect(rows.filter((r) => r.path.includes(':conversationId'))).to.have.length(26)
    assertRouter(rows, CHAT_ROUTES, CHAT_OTHER)
  })

  it('agent router: A1-A23 carry their authorize guard (and runLease on sends), the rest are caller/list or allowlisted', () => {
    const rows = rowsOf(createAgentConversationalRouter(container()))
    expect(rows.filter((r) => r.path.includes(':conversationId'))).to.have.length(23)
    assertRouter(rows, AGENT_ROUTES, AGENT_OTHER)
  })

  it('PH05: runLease follows authorize on exactly C1-C4, C11 and A1-A4', () => {
    const leased = (router: Router) =>
      rowsOf(router)
        .filter((r) => r.marks.some((m) => m?.lease === true))
        .map((r) => r.key)
        .sort()
    expect(leased(createConversationalRouter(container()))).to.deep.equal(
      [
        'POST /:conversationId/messages',
        'POST /:conversationId/messages/stream',
        'POST /internal/:conversationId/messages',
        'POST /internal/:conversationId/messages/stream',
        'POST /:conversationId/message/:messageId/regenerate',
      ].sort(),
    )
    expect(leased(createAgentConversationalRouter(container()))).to.deep.equal(
      [
        `POST ${AGENT}/:conversationId/messages`,
        `POST ${AGENT}/:conversationId/messages/stream`,
        `POST ${AGENT}/internal/:conversationId/messages/stream`,
        `POST ${AGENT}/:conversationId/message/:messageId/regenerate`,
      ].sort(),
    )
  })

  it('projects router: the conversation list is the only guarded route and uses listScope', () => {
    const rows = rowsOf(createProjectsRouter(container()))
    const guarded = rows.filter((r) => guardsOf(r).length > 0)
    expect(guarded.map((r) => [r.key, guardsOf(r)])).to.deep.equal([['GET /:projectId/conversations', [list('any')]]])
    expect(guarded[0]!.marks[guarded[0]!.length - 2]).to.deep.equal(list('any'))
    expect(rows.some((r) => r.path.includes(':conversationId'))).to.equal(false)
  })

  it('the artifacts gallery joins conversation titles only behind the list guard, right before the handler', () => {
    const rows = rowsOf(createArtifactsRouter(container()))
    expect(rows.map((r) => [r.key, guardsOf(r)])).to.deep.equal([
      ['GET /', [list('any')]],
      ['GET /:artifactId/versions', []],
      ['GET /:artifactId', [list('any')]],
    ])
    for (const r of rows.filter((row) => guardsOf(row).length > 0)) {
      expect(r.marks[r.length - 2]).to.deep.equal(list('any'))
    }
  })

  it('the three internal routes validate the scoped token and hydrate the user, then the guard', () => {
    const internal = [
      ...rowsOf(createConversationalRouter(container())),
      ...rowsOf(createAgentConversationalRouter(container())),
    ].filter((r) => r.path.includes('/internal/') && r.path.includes(':conversationId'))
    expect(internal.map((r) => r.key)).to.have.length(3)
    for (const r of internal) expect(r.hydrateAt, r.key).to.be.greaterThan(-1)
  })

  it('PH04-01: a container without the guards binding fails at boot, for every router', () => {
    for (const build of [createConversationalRouter, createAgentConversationalRouter, createProjectsRouter]) {
      expect(() => build(container(false)), build.name).to.throw()
    }
  })
})
