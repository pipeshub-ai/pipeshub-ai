import { expect } from 'chai'
import { readFileSync } from 'fs'
import { join } from 'path'
import yaml from 'js-yaml'

// `PATCH /personal-access-tokens/{tokenId}` documents `{"knowledgeLimit": null}` as "everything".
// OpenAPI 3.0 applies `nullable` only to the schema that declares it, never into an allOf/oneOf/
// anyOf branch or a $ref, so a composed schema would refuse that null in every generated client.
describe('published spec: changing a personal access token limit', () => {
  const spec: any = yaml.load(
    readFileSync(join(__dirname, '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml'), 'utf8'),
  )
  const body = spec.components.schemas.UpdatePatKnowledgeLimitRequest
  const field = body.properties.knowledgeLimit

  it('requires knowledgeLimit, so a body without it is not read as everything', () => {
    expect(body.required).to.include('knowledgeLimit')
  })

  it('accepts null without a composed branch that would refuse it', () => {
    expect(field.nullable).to.equal(true)
    for (const key of ['$ref', 'allOf', 'oneOf', 'anyOf']) expect(field, key).to.not.have.property(key)
  })

  it('keeps the same shape as KnowledgeLimit', () => {
    const limit = spec.components.schemas.KnowledgeLimit
    expect(field.type).to.equal(limit.type)
    expect(field.additionalProperties).to.equal(limit.additionalProperties)
    expect(field.properties).to.deep.equal(limit.properties)
  })
})
