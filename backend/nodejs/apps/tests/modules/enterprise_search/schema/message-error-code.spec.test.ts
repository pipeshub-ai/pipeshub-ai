import 'reflect-metadata'
import { expect } from 'chai'
import { readFileSync } from 'fs'
import { join } from 'path'
import yaml from 'js-yaml'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'

const SPEC = join(__dirname, '..', '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml')

// `Message` allows no other properties, so a field stored on a row and left out of the spec is off-contract.
describe('the stored errorCode is in the published spec', () => {
  const message = (yaml.load(readFileSync(SPEC, 'utf8')) as any).components.schemas.Message

  it('a message may carry errorCode, as the row stores it', () => {
    expect(message.additionalProperties).to.equal(false)
    expect(ChatSessionMessage.schema.path('errorCode').instance).to.equal('String')
    expect(message.properties.errorCode?.type).to.equal('string')
  })

  it('says what a client does with it: not offer to try again', () => {
    expect(message.properties.errorCode?.description).to.match(/ai_budget_exceeded/)
    expect(message.properties.errorCode?.description).to.match(/try again/i)
  })
})
