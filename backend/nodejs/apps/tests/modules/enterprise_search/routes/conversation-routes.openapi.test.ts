import { expect } from 'chai'
import { readFileSync } from 'fs'
import { join } from 'path'
import yaml from 'js-yaml'
import { COLLAB_ERROR_CODES } from '../../../../src/modules/enterprise_search/services/collaboration/domain/errors'
import { OPERATION_REQUIREMENTS } from '../../../../src/modules/enterprise_search/services/collaboration/access/conversation-access.policy'
import { CONVERSATION_ROUTES, ConversationRoute } from '../helpers/conversation-routes'

/** The published spec must say what the guards do: the same statuses, the same codes, on every documented conversation route. */

type Json = Record<string, any>
const SPEC = join(__dirname, '..', '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml')
const spec = yaml.load(readFileSync(SPEC, 'utf8')) as Json

/** The spec has no internal (scoped-token) routes. */
const PUBLIC = CONVERSATION_ROUTES.filter((r) => !r.internal)

const pathOf = (r: ConversationRoute): string =>
  (r.kind === 'agent' ? '/agents' : '/conversations') +
  r.path
    .replace('/:agentKey/conversations', r.kind === 'agent' ? '/{agentKey}/conversations' : '')
    .replace(':conversationId', '{conversationId}')
    .replace(':messageId', '{messageId}')

/** Where the spec spells a route differently from the router (`/archive` is a POST for agents, a PATCH for chats). */
const operationOf = (r: ConversationRoute): Json => {
  const item = spec.paths[pathOf(r)]
  expect(item, `${pathOf(r)} is documented`).to.not.equal(undefined)
  const op = item[r.method]
  expect(op, `${r.method.toUpperCase()} ${pathOf(r)} is documented`).to.not.equal(undefined)
  return op
}

const resolve = (response: Json): Json => {
  const ref = response.$ref as string | undefined
  if (!ref) return response
  const name = ref.replace('#/components/responses/', '')
  return spec.components.responses[name]
}

const codesIn = (text: string): string[] => (text.match(/`([A-Z][A-Z_]+)`/g) ?? []).map((t) => t.slice(1, -1)).filter((c) => (Object.values(COLLAB_ERROR_CODES) as string[]).includes(c))

/** Conversation codes a 403 on this operation may carry, read off the policy table. */
function forbiddenCodes(r: ConversationRoute): string[] {
  const req = OPERATION_REQUIREMENTS[r.op]
  const codes: string[] = []
  if (req.min !== 'read') codes.push(COLLAB_ERROR_CODES[req.belowMin])
  if (req.guard === 'asker') codes.push(COLLAB_ERROR_CODES.REGENERATE_NOT_ALLOWED)
  if (req.requiresActiveOwner === true) codes.push(COLLAB_ERROR_CODES.OWNER_INACTIVE)
  // The turn pipeline (PH-05): the sender's own project access, and the binding of a resume to its card.
  if (r.op === 'send' || r.op === 'regenerate') codes.push(COLLAB_ERROR_CODES.PROJECT_ACCESS_REQUIRED)
  if (r.op === 'send') codes.push(COLLAB_ERROR_CODES.RESUME_NOT_ALLOWED)
  return codes.sort()
}

const { BUSY, CHANGED, DUPLICATE_MESSAGE, RUN_LOST, CONNECTOR_SETUP_REQUIRED } = COLLAB_ERROR_CODES

/** 409 codes a turn route may carry: a non-streaming send also reports a lost lease, a regenerate has no message to repeat. */
const CONFLICT_CODES: Record<string, string[]> = {
  C1: [BUSY, CHANGED, DUPLICATE_MESSAGE, RUN_LOST],
  A1: [BUSY, CHANGED, DUPLICATE_MESSAGE, RUN_LOST],
  C3: [BUSY, CHANGED, DUPLICATE_MESSAGE],
  A2: [BUSY, CHANGED, DUPLICATE_MESSAGE],
  C11: [BUSY],
  A4: [BUSY],
}
/** Agent turns check that the agent's tools are connected first. */
const PRECONDITION_IDS = new Set(['A1', 'A2', 'A4'])
const TURN_IDS = new Set(Object.keys(CONFLICT_CODES))

/** The first send creates the conversation, so it has no conversation id in its path and a different set of 409s. */
const FIRST_SENDS = [
  { id: 'streamChat', path: '/conversations/stream', success: '200', codes: [DUPLICATE_MESSAGE], request: 'ConversationStreamRequest' },
  { id: 'createConversation', path: '/conversations/create', success: '201', codes: [DUPLICATE_MESSAGE, RUN_LOST], request: 'CreateConversationRequest' },
  { id: 'streamAgentConversation', path: '/agents/{agentKey}/conversations/stream', success: '200', codes: [DUPLICATE_MESSAGE], request: 'AgentStreamCreateConversationRequest' },
  { id: 'createAgentConversation', path: '/agents/{agentKey}/conversations', success: '201', codes: [DUPLICATE_MESSAGE, RUN_LOST], request: 'AgentCreateConversationRequest' },
]

/** The properties of a schema, following `$ref` and `allOf`. */
function propertiesOf(schema: Json): string[] {
  if (schema.$ref) return propertiesOf(spec.components.schemas[(schema.$ref as string).replace('#/components/schemas/', '')])
  const own = Object.keys(schema.properties ?? {})
  return [...own, ...(schema.allOf ?? []).flatMap((part: Json) => propertiesOf(part))]
}

const SEND_FIELDS = ['clientMessageId', 'baseSeq', 'filesShared', 'shareToolResults', 'resume']
const FIRST_SEND_FIELDS = ['clientMessageId', 'filesShared', 'shareToolResults']

const bodySchemaOf = (op: Json): Json => op.requestBody.content['application/json'].schema

/** Conversation codes a 503 on this operation may carry. */
function unavailableCodes(r: ConversationRoute): string[] {
  const codes: string[] = [COLLAB_ERROR_CODES.TEAM_RESOLUTION_UNAVAILABLE]
  if (OPERATION_REQUIREMENTS[r.op].requiresActiveOwner === true) codes.push(COLLAB_ERROR_CODES.OWNER_STATUS_UNAVAILABLE)
  return codes.sort()
}

describe('OpenAPI: conversation routes document the guard outcomes', () => {
  it('ConversationErrorCode lists exactly the codes of the typed domain errors', () => {
    expect([...spec.components.schemas.ConversationErrorCode.enum].sort()).to.deep.equal(Object.values(COLLAB_ERROR_CODES).sort())
  })

  it('the 26 public conversation and agent-conversation routes are all documented', () => {
    expect(PUBLIC).to.have.length(26)
    for (const r of PUBLIC) operationOf(r)
  })

  for (const r of PUBLIC) {
    describe(`${r.id} ${r.method.toUpperCase()} ${pathOf(r)}`, () => {
      it('404 is the not-found response, and no description claims otherwise', () => {
        const op = operationOf(r)
        expect(op.responses['404'].$ref).to.equal('#/components/responses/ConversationNotFound')
        expect(codesIn(resolve(op.responses['404']).description)).to.deep.equal([COLLAB_ERROR_CODES.NOT_FOUND])
      })

      it(`403 carries exactly the conversation codes ${JSON.stringify(forbiddenCodes(r))}`, () => {
        const op = operationOf(r)
        expect(op.responses['403'], '403 is documented').to.not.equal(undefined)
        expect(codesIn(resolve(op.responses['403']).description).sort()).to.deep.equal(forbiddenCodes(r))
      })

      it(`503 carries exactly the conversation codes ${JSON.stringify(unavailableCodes(r))}`, () => {
        const op = operationOf(r)
        const description = resolve(op.responses['503']).description as string
        expect(codesIn(description).sort()).to.deep.equal(unavailableCodes(r))
      })

      it(`409 carries exactly the codes ${JSON.stringify(CONFLICT_CODES[r.id] ?? [])}`, () => {
        const op = operationOf(r)
        if (!TURN_IDS.has(r.id)) {
          expect(op.responses['409'], 'only turn routes conflict').to.equal(undefined)
          return
        }
        expect(codesIn(resolve(op.responses['409']).description).sort()).to.deep.equal([...CONFLICT_CODES[r.id]!].sort())
        expect(resolve(op.responses['409']).content['application/json'].schema.$ref).to.equal('#/components/schemas/ConversationErrorResponse')
      })

      it(`412 ${PRECONDITION_IDS.has(r.id) ? 'carries CONNECTOR_SETUP_REQUIRED' : 'is not documented'}`, () => {
        const op = operationOf(r)
        if (!PRECONDITION_IDS.has(r.id)) {
          expect(op.responses['412']).to.equal(undefined)
          return
        }
        expect(codesIn(resolve(op.responses['412']).description)).to.deep.equal([CONNECTOR_SETUP_REQUIRED])
      })

      it(TURN_IDS.has(r.id) ? 'the success response carries X-Run-Id' : 'no X-Run-Id is promised', () => {
        const op = operationOf(r)
        const headers = op.responses['200']?.headers ?? {}
        if (TURN_IDS.has(r.id)) {
          expect(headers['X-Run-Id']?.$ref).to.equal('#/components/headers/X-Run-Id')
        } else {
          expect(headers).to.not.have.property('X-Run-Id')
        }
      })

      if (r.op === 'send') {
        it(`the request documents ${SEND_FIELDS.join(', ')}`, () => {
          expect(propertiesOf(bodySchemaOf(operationOf(r)))).to.include.members(SEND_FIELDS)
        })
      }

      it('error bodies use the conversation error envelope', () => {
        const op = operationOf(r)
        for (const status of ['403', '404', '503']) {
          const schema = resolve(op.responses[status]).content['application/json'].schema
          expect(schema.$ref, status).to.equal('#/components/schemas/ConversationErrorResponse')
        }
      })

      it('does not describe the owner as the only possible actor with a stale "initiator" wording', () => {
        expect(String(operationOf(r).description ?? '')).to.not.match(/initiator-only/i)
      })
    })
  }

  describe('the first send (the conversation does not exist yet)', () => {
    for (const f of FIRST_SENDS) {
      describe(`${f.id} POST ${f.path}`, () => {
        const op = (): Json => spec.paths[f.path].post

        it(`409 carries exactly the codes ${JSON.stringify(f.codes)}`, () => {
          expect(codesIn(resolve(op().responses['409']).description).sort()).to.deep.equal([...f.codes].sort())
        })

        it(`${f.success} carries X-Run-Id`, () => {
          expect(op().responses[f.success].headers['X-Run-Id'].$ref).to.equal('#/components/headers/X-Run-Id')
        })

        it(`the request documents ${FIRST_SEND_FIELDS.join(', ')} but neither baseSeq nor resume`, () => {
          const properties = propertiesOf(bodySchemaOf(op()))
          expect(properties).to.include.members(FIRST_SEND_FIELDS)
          expect(properties).to.not.include.members(['baseSeq'])
          expect(properties).to.not.include.members(['resume'])
        })
      })
    }
  })

  it('X-Run-Id is a documented response header and the request fields have schemas', () => {
    expect(spec.components.headers['X-Run-Id'].schema).to.deep.include({ type: 'string', format: 'uuid' })
    expect(spec.components.schemas.ClientMessageId).to.deep.include({ type: 'string', minLength: 1, maxLength: 64 })
    expect(spec.components.schemas.BaseSeq).to.deep.include({ type: 'integer', minimum: -1 })
    expect(spec.components.schemas.ResumeRequest.required).to.deep.equal(['toolCallMessageId'])
  })

  it('cancel documents that only the live run is cancelled with the flag on', () => {
    for (const path of ['/conversations/{conversationId}/cancel', '/agents/{agentKey}/conversations/{conversationId}/cancel']) {
      expect(spec.paths[path].post.description).to.match(/cancelled": false/)
    }
  })
})
