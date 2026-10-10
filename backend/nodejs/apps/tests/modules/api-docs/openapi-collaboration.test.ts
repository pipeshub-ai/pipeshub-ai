import 'reflect-metadata'
import { expect } from 'chai'
import { readFileSync } from 'fs'
import { join } from 'path'
import { Router } from 'express'
import { Container } from 'inversify'
import yaml from 'js-yaml'
import { mountCollaborationRoutes } from '../../../src/modules/enterprise_search/routes/collaboration.routes'
import { COLLAB_ERROR_CODES } from '../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { COLLABORATION_ROUTES, COLLABORATION_SCOPES } from '../enterprise_search/helpers/conversation-routes'
import { bindCollaborationStubs } from '../enterprise_search/helpers/collaboration-world'
import { markingGuards } from '../enterprise_search/helpers/guarded-chat'
import { collaborationSchemas } from '../../../src/modules/enterprise_search/validators/collaboration.validators'

/** NF-03: every collaboration route, both kinds, is in the published spec with its scope, its errors and its shapes. */

type Json = Record<string, any>
const SPEC = join(__dirname, '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml')
const spec = yaml.load(readFileSync(SPEC, 'utf8')) as Json

const routerOf = (kind: 'chat' | 'agent'): Router => {
  const container = new Container()
  container.bind('AuthMiddleware').toConstantValue({ authenticate: (_q: unknown, _s: unknown, n: () => void) => n() })
  const guards = markingGuards()
  container.bind(Symbol.for('collab.ConversationGuards')).toConstantValue(guards)
  bindCollaborationStubs(container)
  const router = Router()
  mountCollaborationRoutes(router, container, kind)
  return router
}

const toSpecPath = (kind: 'chat' | 'agent', path: string): string =>
  (kind === 'agent' ? '/agents' : '/conversations') +
  path
    .replace('/:agentKey/conversations', kind === 'agent' ? '/{agentKey}/conversations' : '')
    .replace(':conversationId', '{conversationId}')
    .replace(':principalId', '{principalId}')

const mounted = (kind: 'chat' | 'agent') =>
  (routerOf(kind).stack as Array<{ route?: { path: string; methods: Record<string, boolean> } }>)
    .filter((l) => l.route)
    .map((l) => ({ method: Object.keys(l.route!.methods)[0]!, path: l.route!.path }))

const resolve = (response: Json): Json => (response.$ref ? spec.components.responses[(response.$ref as string).replace('#/components/responses/', '')] : response)
const codesIn = (text: string): string[] =>
  [...new Set((text.match(/`([A-Z][A-Z_]+)`/g) ?? []).map((t) => t.slice(1, -1)).filter((c) => (Object.values(COLLAB_ERROR_CODES) as string[]).includes(c)))].sort()

const SCOPE_FOR_OP: Record<string, string> = COLLABORATION_SCOPES

describe('OpenAPI: collaboration routes (NF-03)', () => {
  for (const kind of ['chat', 'agent'] as const) {
    it(`every ${kind} collaboration route the router mounts is documented under the same method`, () => {
      const routes = mounted(kind)
      expect(routes).to.have.length(8)
      for (const { method, path } of routes) {
        const item = spec.paths[toSpecPath(kind, path)]
        expect(item, `${method} ${path} is in the spec`).to.not.equal(undefined)
        expect(item[method], `${method.toUpperCase()} ${toSpecPath(kind, path)}`).to.not.equal(undefined)
      }
    })
  }

  for (const r of COLLABORATION_ROUTES) {
    const specPath = toSpecPath(r.kind, r.path)
    describe(`${r.id} ${r.method.toUpperCase()} ${specPath}`, () => {
      const op = (): Json => spec.paths[specPath][r.method]

      it('asks for its scope, and no other, and is tagged for its kind', () => {
        const scopes = (op().security as Json[]).flatMap((s) => s.oauth2 ?? [])
        expect(scopes).to.deep.equal([SCOPE_FOR_OP[r.op]])
        expect(op().tags).to.deep.equal([r.kind === 'chat' ? 'Conversations' : 'Agents'])
      })

      it('has a unique operationId', () => {
        const id = op().operationId as string
        const same = Object.values(spec.paths).flatMap((item) => Object.values(item as Json)).filter((o) => (o as Json)?.operationId === id)
        expect(same).to.have.length(1)
      })

      it('documents 404 as the not-found response and an error envelope on every error', () => {
        expect(op().responses['404'].$ref).to.equal('#/components/responses/ConversationNotFound')
        for (const status of ['403', '404']) {
          expect(resolve(op().responses[status]).content['application/json'].schema.$ref).to.equal('#/components/schemas/ConversationErrorResponse')
        }
      })

      it('documents each route\'s own error codes', () => {
        const mutating = ['invite', 'manageCollaborators', 'settings', 'transfer', 'leave'].includes(r.op)
        if (mutating) {
          expect(codesIn(resolve(op().responses['429']).description)).to.deep.equal(['RATE_LIMITED'])
        }
        if (r.op === 'invite') {
          expect(codesIn(resolve(op().responses['409']).description)).to.deep.equal(['COLLABORATOR_LIMIT'])
          expect(codesIn(resolve(op().responses['400']).description)).to.deep.equal(['INVALID_PRINCIPAL', 'ORG_WIDE_CONFIRMATION_REQUIRED'])
          expect(codesIn(resolve(op().responses['403']).description)).to.deep.equal(['CONVERSATION_OWNER_ONLY', 'CONVERSATION_READ_ONLY'])
        }
        if (r.op === 'transfer') {
          expect(codesIn(resolve(op().responses['403']).description)).to.deep.equal(['CONVERSATION_OWNER_ONLY', 'PROJECT_ACCESS_REQUIRED'])
        }
        if (r.op === 'leave') {
          expect(codesIn(resolve(op().responses['403']).description)).to.deep.equal(['CONVERSATION_OWNER_ONLY'])
        }
        if (r.op === 'settings' || r.op === 'manageCollaborators') {
          expect(codesIn(resolve(op().responses['403']).description)).to.deep.equal(['CONVERSATION_OWNER_ONLY'])
        }
      })
    })
  }

  it('the feed documents its 304 and its two cursors', () => {
    for (const path of ['/conversations/{conversationId}/feed', '/agents/{agentKey}/conversations/{conversationId}/feed']) {
      const get = spec.paths[path].get
      expect(get.responses['304']).to.not.equal(undefined)
      expect(get.parameters.map((p: Json) => p.name)).to.include.members(['afterSeq', 'rev'])
      expect(resolve(get.responses['429']).description).to.match(/RATE_LIMITED/)
    }
  })

  it('the request and response schemas exist and match the validators', () => {
    const schemas = spec.components.schemas
    for (const name of ['PutCollaboratorsRequest', 'CollaborationSettings', 'TransferOwnershipRequest', 'CollaboratorDto', 'CollaboratorsView', 'CollaboratorsSummary', 'FeedResponse', 'Readiness', 'LeaveConversationResponse']) {
      expect(schemas[name], name).to.not.equal(undefined)
    }
    const put = schemas.PutCollaboratorsRequest.properties
    expect(put.collaborators).to.include({ minItems: 1, maxItems: 50 })
    expect(put.note.maxLength).to.equal(500)
    expect(schemas.CollaboratorRequest.properties.principalType.enum).to.deep.equal(['user', 'team'])
    expect(Object.keys(schemas.CollaboratorsSummary.properties).sort()).to.deep.equal(['collaboratorCount', 'myAccess', 'owner', 'respondMode'])
    expect(schemas.Readiness.properties.reasons.items.enum).to.have.members(['CONVERSATION_READ_ONLY', 'OWNER_INACTIVE', 'PROJECT_ACCESS_REQUIRED', 'CONNECTOR_SETUP_REQUIRED', 'AGENT_UNAVAILABLE'])
    expect(schemas.CollaboratorDto.properties.state.enum).to.deep.equal(['active', 'former_member', 'deleted_team'])
    const row = { principalType: 'user', principalId: 'a'.repeat(24), accessLevel: 'read' }
    const accepted = (n: number) => collaborationSchemas.chat.put.safeParse({ params: { conversationId: 'a'.repeat(24) }, body: { collaborators: Array.from({ length: n }, (_, i) => ({ ...row, principalId: i.toString(16).padStart(24, '0') })) } }).success
    expect(accepted(50)).to.equal(true)
    expect(accepted(51)).to.equal(false)
  })

  it('conversation:share is a documented OAuth scope in both flows, and legacy share and unshare are deprecated', () => {
    const flows = spec.components.securitySchemes.oauth2.flows
    for (const flow of Object.values(flows) as Json[]) expect(flow.scopes).to.have.property('conversation:share')
    expect(spec.paths['/conversations/{conversationId}/share'].post.deprecated).to.equal(true)
    expect(spec.paths['/conversations/{conversationId}/unshare'].post.deprecated).to.equal(true)
  })

  it('the error code table lists the collaboration codes with their statuses', () => {
    const enumCodes = spec.components.schemas.ConversationErrorCode.enum as string[]
    for (const code of ['COLLABORATOR_LIMIT', 'INVALID_PRINCIPAL', 'RATE_LIMITED', 'ORG_WIDE_CONFIRMATION_REQUIRED', 'TEAM_RESOLUTION_UNAVAILABLE']) expect(enumCodes).to.include(code)
    expect(spec.components.schemas.ConversationErrorCode.description).to.match(/`RATE_LIMITED` \| 429/)
  })

  it('archive is documented for both kinds as a per-person action', () => {
    expect(spec.paths['/conversations/{conversationId}/archive'].patch.description).to.match(/per person/)
    expect(spec.paths['/agents/{agentKey}/conversations/{conversationId}/archive'].post.description).to.match(/per\s+person/)
    expect(spec.paths['/agents/{agentKey}/conversations/{conversationId}/unarchive'].post).to.not.equal(undefined)
  })
})
