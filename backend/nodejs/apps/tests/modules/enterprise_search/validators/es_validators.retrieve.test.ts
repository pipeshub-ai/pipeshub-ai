import 'reflect-metadata'
import { expect } from 'chai'
import { enterpriseSearchRetrieveSchema } from '../../../../src/modules/enterprise_search/validators/es_validators'
import { ValidationUtils } from '../../../../src/libs/utils/validation.utils'

// What a POST /api/v1/retrieve caller is told when a request is refused.

const refusal = (body: unknown): Array<[string, string]> => {
  const result = enterpriseSearchRetrieveSchema.safeParse({ body })
  if (result.success) throw new Error('expected the request to be refused')
  return ValidationUtils.formatZodError(result.error).map((e) => [e.field, e.message])
}

const accepts = (body: unknown): boolean =>
  enterpriseSearchRetrieveSchema.safeParse({ body }).success

describe('enterpriseSearchRetrieveSchema', () => {
  it('accepts a query with the fields it documents', () => {
    expect(
      accepts({
        query: 'rollback',
        limit: 5,
        maxChunksPerRecord: 2,
        rewriteQuery: false,
        filters: { apps: ['11111111-1111-4111-8111-111111111111'] },
        modifiedAfter: '2026-01-01',
      }),
    ).to.equal(true)
  })

  it('refuses a missing, an empty and a spaces-only query with the same words', () => {
    expect(refusal({})).to.deep.equal([['body.query', 'Query is required.']])
    expect(refusal({ query: '' })).to.deep.equal([['body.query', 'Query is required.']])
    expect(refusal({ query: '   ' })).to.deep.equal([['body.query', 'Query is required.']])
    expect(refusal({ query: '\t\n ' })).to.deep.equal([['body.query', 'Query is required.']])
  })

  it('keeps a query with spaces around it as it was sent', () => {
    const result = enterpriseSearchRetrieveSchema.safeParse({ body: { query: '  rollback ' } })
    expect(result.success && result.data.body.query).to.equal('  rollback ')
  })

  it('refuses a field it does not take, by name, instead of ignoring it', () => {
    const [maxChunk] = refusal({ query: 'q', maxChunkPerRecord: 1 })
    expect(maxChunk?.[1]).to.include('maxChunkPerRecord')
    expect(refusal({ query: 'q', foo: 'bar' })[0]?.[1]).to.include('foo')
  })

  it('says filters.apps takes connector instance ids when given a connector type', () => {
    const [[field, message]] = refusal({ query: 'q', filters: { apps: ['JIRA'] } })
    expect(field).to.equal('body.filters.apps.0')
    expect(message).to.include('filters.apps')
    expect(message).to.include('connector instance id')
    expect(message).to.include('GET /api/v1/connectors/active')
  })

  for (const [key, kind] of [
    ['kb', 'collection id'],
    ['recordGroups', 'record group id'],
    ['records', 'record id'],
    ['recordsExact', 'record id'],
  ] as const) {
    it(`names filters.${key} and the id it takes when an id is not a UUID`, () => {
      const [[field, message]] = refusal({ query: 'q', filters: { [key]: ['nope'] } })
      expect(field).to.equal(`body.filters.${key}.0`)
      expect(message).to.equal(`Each id in filters.${key} must be a ${kind} (a UUID)`)
    })
  }
})
