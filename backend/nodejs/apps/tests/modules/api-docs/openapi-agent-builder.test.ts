import { expect } from 'chai'
import { readFileSync } from 'fs'
import { join } from 'path'
import yaml from 'js-yaml'

/** PH-11.4: the create-from-draft contract and the handle check are in the published spec. */
type Json = Record<string, any>
const spec = yaml.load(readFileSync(join(__dirname, '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml'), 'utf8')) as Json

describe('OpenAPI: agents created from a chat draft', () => {
  const create = spec.paths['/agents/create'].post

  it('draftRef is on the create request as two object ids', () => {
    const draftRef = spec.components.schemas.AgentCreateRequest.properties.draftRef
    expect(draftRef.required).to.have.members(['conversationId', 'messageId'])
    expect(draftRef.properties.conversationId.format).to.equal('objectId')
  })

  it('create documents the new refusals, the 404 and the 409', () => {
    expect(Object.keys(create.responses)).to.include.members(['400', '401', '404', '409'])
    for (const code of ['INVALID_KNOWLEDGE', 'INVALID_TOOLSET', 'SERVICE_ACCOUNT_NOT_ALLOWED', 'HANDLE_RESERVED', 'HANDLE_INVALID']) {
      expect(create.responses['400'].description, code).to.contain(code)
    }
    expect(create.responses['404'].description).to.contain('CONVERSATION_NOT_FOUND')
  })

  it('the handle check is documented with its reasons', () => {
    const op = spec.paths['/agents/handle-availability'].get
    expect(op.parameters.map((p: Json) => p.name)).to.deep.equal(['handle'])
    const body = op.responses['200'].content['application/json'].schema
    expect(body.properties.reason.enum).to.have.members(['invalid', 'reserved', 'taken'])
  })

  it('a mentionable can be an agent with a handle', () => {
    const item = spec.components.schemas.MentionableItem
    expect(item.properties.type.enum).to.include('agent')
    expect(item.properties.handle.type).to.equal('string')
  })
})
