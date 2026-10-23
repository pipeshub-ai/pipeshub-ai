import 'reflect-metadata'
import { expect } from 'chai'
import fs from 'fs'
import path from 'path'
import { Router } from 'express'
import * as es from '../../../../src/modules/enterprise_search/routes/es.routes'
import { createProjectsRouter } from '../../../../src/modules/projects/routes/project.routes'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'

/**
 * Runtime route enumeration. The real routers are built and walked, so one-line routes,
 * `router.route(path).get(...)`, inline arrows and routes added to other routers are all seen.
 * Every route behind a conversation, search or project router must be classified below, a
 * route that returns messages must run them through the redaction helper, and a route that
 * claims to return metadata only must not read message rows. A second check pins which
 * source files may read the stored messages at all.
 */

const stub = (): any => new Proxy(function () {}, { get: (_t, k) => (k === 'toString' ? () => '[stub]' : k === 'then' || typeof k === 'symbol' ? undefined : stub()), apply: () => stub(), construct: () => stub() })
const container: any = { get: () => stub(), isBound: () => true }

interface RouteInfo { key: string; source: string }

/** Every method and path mounted on a router (nested routers included) with its final handler's source. */
export const enumerate = (router: Router, prefix = ''): RouteInfo[] => {
  const out: RouteInfo[] = []
  const walk = (stack: any[], base: string) => {
    for (const layer of stack) {
      if (layer.route) {
        const source = layer.route.stack.map((l: any) => String(l.handle)).join('\n')
        for (const method of Object.keys(layer.route.methods)) out.push({ key: `${method.toUpperCase()} ${base}${layer.route.path}`, source })
      } else if (layer.name === 'router' && layer.handle?.stack) walk(layer.handle.stack, base)
    }
  }
  walk((router as any).stack, prefix)
  return out
}

// `\)?` also matches an imported call as CommonJS emits it: `(0, utils_1.getMessages)(...)`.
const READS_MESSAGES = /ChatSessionMessage\.(?:find|findOne|aggregate)\b|getMessages\)?\s*\(|attachMessages\)?\s*\(/
/** Helpers that read message rows but return neither their content nor anything derived from it (attachment record ids; one message to attach feedback to; the ids of the messages being deleted; the ids of the sessions whose own turns match a search). */
const INTERNAL_READS = new Set(['loadConversationChatAttachmentRecordIds', 'performFeedbackUpdate', 'deleteConversation', 'findSessionIdsMatchingContent'])
const REDACTS = /redact(?:MessagesFor|ConversationFor|SearchesFor)/
const esSrc = (rel: string): string => fs.readFileSync(path.resolve(__dirname, '../../../../src/modules/enterprise_search', rel), 'utf8')
const functionText = (file: string, fn: string): string => {
  const text = esSrc(file)
  const start = text.search(new RegExp(`^(?:export )?(?:const|async function|function) ${fn}\\b`, 'm'))
  expect(start, `${fn} is declared in ${file}`).to.be.greaterThan(-1)
  const rest = text.slice(start + 1)
  const next = rest.search(/\n(?:export )?(?:const|async function|function) \w+/)
  return rest.slice(0, next < 0 ? undefined : next)
}

/** Top-level helpers of the files that read stored messages, by name, so a handler's calls can be followed. */
const HELPERS = new Map<string, string>()
for (const file of ['controller/es_controller.ts', 'utils/utils.ts']) {
  const text = esSrc(file)
  for (const part of text.split(/\n(?=(?:export )?(?:const|async function|function) \w+)/)) {
    const name = /^(?:export )?(?:const|async function|function) (\w+)/.exec(part)?.[1]
    if (name) HELPERS.set(name, part)
  }
}
/** Whether this code reads message rows itself or through any helper it calls. */
const readsMessages = (code: string, seen = new Set<string>()): boolean =>
  READS_MESSAGES.test(code) ||
  [...code.matchAll(/\b(\w+)\)?\s*\(/g)].some(([, name]) => {
    const helper = HELPERS.get(name!)
    if (!helper || seen.has(name!) || INTERNAL_READS.has(name!)) return false
    seen.add(name!)
    return readsMessages(helper, seen)
  })

/** R: returns messages; `via` names where a delegating handler does the redaction. O: only the caller's own new turn. M: metadata only. */
export type Class = 'R' | 'O' | 'M' | { via: string }
const NON_STREAMING: Class = { via: 'utils/non-streaming-chat.ts' }
const regen: Class = { via: 'controller/es_controller.ts#regenerateAnswersInternal' }
export const CONVERSATIONS: Record<string, Class> = {
  'POST /create': NON_STREAMING, 'POST /internal/create': NON_STREAMING,
  'POST /:conversationId/messages': NON_STREAMING, 'POST /internal/:conversationId/messages': NON_STREAMING,
  'POST /stream': 'O', 'POST /internal/stream': 'O',
  'POST /:conversationId/messages/stream': 'R', 'POST /internal/:conversationId/messages/stream': { via: 'controller/es_controller.ts#addMessageStream' },
  'GET /:conversationId': 'R', 'GET /show/archives': 'R',
  'POST /:conversationId/message/:messageId/regenerate': regen,
  'POST /attachments/upload': 'M', 'POST /internal/attachments/upload': 'M', 'DELETE /attachments/:recordId': 'M',
  'GET /': 'M', 'DELETE /:conversationId': 'M', 'POST /:conversationId/share': 'M', 'POST /:conversationId/unshare': 'M',
  'PUT /:conversationId/project': 'M', 'PATCH /:conversationId/project-visibility': 'M', 'POST /:conversationId/cancel': 'M',
  'PATCH /:conversationId/title': 'M', 'POST /:conversationId/message/:messageId/feedback': 'M',
  'PATCH /:conversationId/archive': 'M', 'PATCH /:conversationId/unarchive': 'M', 'GET /show/archives/search': 'M',
  // Owner only; the copy keeps each turn's author and drops other authors' details (`copySessionUpToMessage`).
  'POST /:conversationId/message/:messageId/fork': 'M',
  'PATCH /:conversationId/pin': 'M', 'PATCH /:conversationId/unpin': 'M',
}
const SEARCH: Record<string, Class> = {
  'POST /': 'O', 'GET /': 'R', 'GET /:searchId': 'R', 'DELETE /:searchId': 'M', 'DELETE /': 'M',
  'PATCH /:searchId/share': 'M', 'PATCH /:searchId/unshare': 'M', 'PATCH /:searchId/archive': 'M', 'PATCH /:searchId/unarchive': 'M',
  'POST /updateAppConfig': 'M',
}
const AGENT_BASE = '/:agentKey/conversations'
export const AGENTS: Record<string, Class> = {
  'GET /conversations/show/archives': 'M',
  [`POST ${AGENT_BASE}`]: NON_STREAMING, [`POST ${AGENT_BASE}/:conversationId/messages`]: NON_STREAMING,
  [`POST ${AGENT_BASE}/stream`]: 'O', [`POST ${AGENT_BASE}/internal/stream`]: 'O',
  [`POST ${AGENT_BASE}/:conversationId/messages/stream`]: 'R',
  [`POST ${AGENT_BASE}/internal/:conversationId/messages/stream`]: { via: 'controller/es_controller.ts#addMessageStreamToAgentConversation' },
  [`POST ${AGENT_BASE}/:conversationId/message/:messageId/regenerate`]: regen,
  [`POST ${AGENT_BASE}/:conversationId/message/:messageId/fork`]: 'M',
  // The route handler wraps `getAgentConversationByIdOf`, which redacts every message for the viewer.
  [`GET ${AGENT_BASE}/:conversationId`]: { via: 'controller/es_controller.ts#getAgentConversationByIdOf' },
  [`POST ${AGENT_BASE}/:conversationId/share`]: 'M', [`POST ${AGENT_BASE}/:conversationId/unshare`]: 'M',
  [`POST ${AGENT_BASE}/internal/attachments/upload`]: 'M', [`POST ${AGENT_BASE}/attachments/upload`]: 'M', [`DELETE ${AGENT_BASE}/attachments/:recordId`]: 'M',
  [`POST ${AGENT_BASE}/:conversationId/cancel`]: 'M', [`POST ${AGENT_BASE}/:conversationId/message/:messageId/feedback`]: 'M',
  [`GET ${AGENT_BASE}`]: 'M', [`DELETE ${AGENT_BASE}/:conversationId`]: 'M', [`PATCH ${AGENT_BASE}/:conversationId/title`]: 'M',
  [`PUT ${AGENT_BASE}/:conversationId/project`]: 'M', [`PATCH ${AGENT_BASE}/:conversationId/project-visibility`]: 'M',
  [`POST ${AGENT_BASE}/:conversationId/archive`]: 'M', [`POST ${AGENT_BASE}/:conversationId/unarchive`]: 'M', [`GET ${AGENT_BASE}/show/archives`]: 'M',
  'POST /create': 'M', 'GET /:agentKey': 'M', 'PUT /:agentKey': 'M', 'DELETE /:agentKey': 'M', 'GET /': 'M',
  'GET /web-search-usage/:provider': 'M', 'GET /model-usage/:model_key': 'M',
}
const PROJECTS: Record<string, Class> = Object.fromEntries(
  [
    'POST /', 'GET /', 'GET /:projectId', 'PATCH /:projectId', 'DELETE /:projectId', 'POST /:projectId/archive', 'POST /:projectId/unarchive',
    'POST /:projectId/pin', 'POST /:projectId/unpin', 'GET /:projectId/conversations', 'GET /:projectId/members', 'PUT /:projectId/members',
    'DELETE /:projectId/members/:memberUserId', 'POST /:projectId/knowledge-base',
  ].map((k) => [k, 'M' as Class]),
)

/** What is wrong with these routes, given the classification of their router. */
export const audit = (routes: RouteInfo[], table: Record<string, Class>): string[] => {
  const problems: string[] = []
  for (const { key, source } of routes) {
    const cls = table[key]
    if (cls === undefined) problems.push(`${key}: not classified (does it return conversation content?)`)
    else if (cls === 'M' && readsMessages(source)) problems.push(`${key}: classified metadata-only but reads message rows`)
    else if (cls === 'R' && !REDACTS.test(source)) problems.push(`${key}: returns messages but does not redact`)
    else if (typeof cls === 'object') {
      const [file, fn] = cls.via.split('#')
      const text = fn ? functionText(file!, fn) : esSrc(file!)
      if (!REDACTS.test(text)) problems.push(`${key}: delegates to ${cls.via}, which does not redact`)
    }
  }
  for (const key of Object.keys(table)) if (!routes.some((r) => r.key === key)) problems.push(`${key}: classified but no longer mounted`)
  return problems
}

const routers: Array<[string, () => Router, Record<string, Class>]> = [
  ['createConversationalRouter', () => es.createConversationalRouter(container), CONVERSATIONS],
  ['createSemanticSearchRouter', () => es.createSemanticSearchRouter(container), SEARCH],
  ['createAgentConversationalRouter', () => es.createAgentConversationalRouter(container), AGENTS],
  ['createProjectsRouter', () => createProjectsRouter(container), PROJECTS],
]

/** Source files under `root` (relative paths) that mention the stored chat message model, leaving out the top-level directories in `skip`. */
export const chatMessageReaders = (root: string, skip: string[] = []): string[] => {
  const readers: string[] = []
  const scan = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        if (dir !== root || !skip.includes(entry.name)) scan(full)
      } else if (/\.ts$/.test(entry.name) && /\bChatSessionMessage\b/.test(fs.readFileSync(full, 'utf8'))) readers.push(path.relative(root, full))
    }
  }
  scan(root)
  return readers
}
/** Top-level directories that hold an `.audited-separately` file: another test pins their readers. */
const auditedSeparately = (root: string): string[] =>
  fs.readdirSync(root, { withFileTypes: true }).filter((e) => e.isDirectory() && fs.existsSync(path.join(root, e.name, '.audited-separately'))).map((e) => e.name)

describe('conversation routes and per-viewer redaction (runtime enumeration)', () => {
  for (const [name, build, table] of routers) {
    it(`${name}: every route is classified and its handler matches the class`, () => {
      const routes = enumerate(build())
      expect(routes.length, 'the walker found routes').to.be.greaterThan(5)
      expect(audit(routes, table)).to.deep.equal([])
    })
  }

  describe('the walker and audit catch what a regex would miss (planted routes)', () => {
    const planted = (build: (r: Router) => void) => {
      const r = Router()
      build(r)
      return audit(enumerate(r), {})
    }
    it('a one-line route', () => {
      expect(planted((r) => { r.get('/a', (_q, s) => s.json({})); r.get('/b', (_q, s) => s.json({})) })).to.have.length(2)
    })
    it('router.route(path).get(...)', () => {
      expect(planted((r) => { r.route('/x').get((_q, s) => s.json({})).post((_q, s) => s.json({})) })).to.have.length(2)
    })
    it('an inline arrow that reads message rows, even when classified metadata-only', () => {
      const r = Router()
      r.get('/export', async (_q, s) => s.json(await ChatSessionMessage.find({}).lean()))
      expect(audit(enumerate(r), { 'GET /export': 'M' })).to.deep.equal(['GET /export: classified metadata-only but reads message rows'])
    })
    it('a metadata-only route that reads message rows through a helper', () => {
      const r = Router()
      // Not executed: only the handler's source is read.
      r.get('/h', (_q: any, s: any) => s.json((globalThis as any).withTurnAttachments()))
      expect(audit(enumerate(r), { 'GET /h': 'M' })).to.deep.equal(['GET /h: classified metadata-only but reads message rows'])
    })
    it('a metadata-only route that calls an imported reader in CommonJS form', () => {
      const r = Router()
      // Not executed: compiled, these read `(0, globalThis.m.getMessages)()`, as an import would.
      r.get('/c', (_q: any, s: any) => s.json((0, (globalThis as any).m.getMessages)()))
      r.get('/d', (_q: any, s: any) => s.json((0, (globalThis as any).m.withTurnAttachments)()))
      expect(audit(enumerate(r), { 'GET /c': 'M', 'GET /d': 'M' })).to.deep.equal([
        'GET /c: classified metadata-only but reads message rows',
        'GET /d: classified metadata-only but reads message rows',
      ])
    })
    it('a returns-messages route that does not redact', () => {
      const r = Router()
      r.get('/m', (_q, s) => s.json({}))
      expect(audit(enumerate(r), { 'GET /m': 'R' })).to.deep.equal(['GET /m: returns messages but does not redact'])
    })
    it('a route nested in another router', () => {
      const outer = Router()
      const inner = Router()
      inner.get('/deep', (_q, s) => s.json({}))
      outer.use('/inner', inner)
      expect(enumerate(outer).map((x) => x.key)).to.deep.equal(['GET /deep'])
    })
    it('a new route added to a real router is reported', () => {
      const router = es.createConversationalRouter(container)
      router.get('/:conversationId/export', (_q, s) => s.json({}))
      expect(audit(enumerate(router), CONVERSATIONS)).to.deep.equal(['GET /:conversationId/export: not classified (does it return conversation content?)'])
    })
  })

  it('only the known source files read stored chat messages (another module reading them must be reviewed)', () => {
    const root = path.resolve(__dirname, '../../../../src')
    expect(chatMessageReaders(root, auditedSeparately(root)).sort()).to.deep.equal([
      'modules/configuration_manager/services/migrations/chat_sessions.migration.ts',
      'modules/enterprise_search/controller/es_controller.ts',
      'modules/enterprise_search/schema/chat.session.message.schema.ts',
      'modules/enterprise_search/schema/chat.session.schema.ts',
      'modules/enterprise_search/utils/fork-conversation.ts',
      'modules/enterprise_search/utils/utils.ts',
    ])
  })
})
