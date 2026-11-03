import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { readFileSync } from 'fs'
import { join } from 'path'
import yaml from 'js-yaml'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import {
  entityNeighbors,
  grepRecords,
  searchEntities,
} from '../../../../src/modules/enterprise_search/controller/exact_search.controller'
import {
  enterpriseSearchEntitiesSchema,
  enterpriseSearchEntityNeighborsSchema,
  enterpriseSearchGrepSchema,
} from '../../../../src/modules/enterprise_search/validators/es_validators'

const appConfig: any = { aiBackend: 'http://ai:8000' }

const run = async (handler: any, body: any) => {
  const req: any = { body, headers: { authorization: 'Bearer t' }, context: { requestId: 'r1' } }
  const res: any = { statusCode: 0, payload: undefined }
  res.status = (code: number) => { res.statusCode = code; return res }
  res.json = (data: any) => { res.payload = data; return res }
  let error: any
  await handler(req, res, (e: any) => { error = e })
  return { res, error }
}

describe('entity search controller', () => {
  let execute: sinon.SinonStub
  let uris: string[]
  let bodies: string[]

  beforeEach(() => {
    uris = []
    bodies = []
    execute = sinon.stub(AIServiceCommand.prototype, 'execute')
    const ctor = sinon.stub(AIServiceCommand.prototype as any, 'sanitizeBody')
    ctor.callsFake((b: any) => { bodies.push(JSON.stringify(b)); return JSON.stringify(b) })
  })
  afterEach(() => sinon.restore())

  const answer = (statusCode: number, data: any) =>
    execute.callsFake(async function (this: any) { uris.push(this.url ?? ''); return { statusCode, data } })

  it('forwards only the grep fields, so a body cannot name another user or organisation, and returns the AI service answer as is', async () => {
    answer(200, { status: 'success', records: [{ recordId: 'r1' }] })
    const { res, error } = await run(grepRecords(appConfig), { pattern: 'a.b', regex: true, orgId: 'other-org', userId: 'u', evil: 'x' })
    expect(error).to.equal(undefined)
    expect(res.statusCode).to.equal(200)
    expect(res.payload.records).to.have.length(1)
    expect(JSON.parse(bodies[0] ?? '{}')).to.deep.equal({ pattern: 'a.b', regex: true })
  })

  it('passes an empty grep answer through: a caller without access gets 0 matches', async () => {
    answer(200, { status: 'success', records: [], totalRecords: 0 })
    expect((await run(grepRecords(appConfig), { pattern: 'secret' })).res.payload.records).to.deep.equal([])
  })

  it('turns an AI service refusal of a pattern into an error for the error handler', async () => {
    answer(400, { detail: 'Invalid regular expression: x' })
    const { res, error } = await run(grepRecords(appConfig), { pattern: '(' })
    expect(res.payload).to.equal(undefined)
    expect(error).to.be.instanceOf(Error)
  })

  it('forwards only the entity fields, so a body cannot name another user or organisation', async () => {
    answer(200, { status: 'success', results: [{ entityId: 'e1' }] })
    const { res, error } = await run(searchEntities(appConfig), {
      query: 'payments', limit: 5, orgId: 'other-org', userId: 'other-user', evil: 'x',
    })
    expect(error).to.equal(undefined)
    expect(res.statusCode).to.equal(200)
    expect(JSON.parse(bodies[0] ?? '{}')).to.deep.equal({ query: 'payments', limit: 5 })
    answer(200, { status: 'success', records: [] })
    await run(entityNeighbors(appConfig), { entityId: 'e', entityType: 'topic', orgId: 'other-org', userId: 'u' })
    expect(JSON.parse(bodies[1] ?? '{}')).to.deep.equal({ entityId: 'e', entityType: 'topic' })
  })

  it('passes an empty answer through: a caller without access gets 0 entities', async () => {
    answer(200, { status: 'success', results: [] })
    expect((await run(searchEntities(appConfig), { query: 'secret' })).res.payload.results).to.deep.equal([])
    answer(200, { status: 'success', records: [], relatedEntities: [] })
    const out = (await run(entityNeighbors(appConfig), { entityId: 'e', entityType: 'topic' })).res.payload
    expect(out.records).to.deep.equal([])
    expect(out.relatedEntities).to.deep.equal([])
  })

  it('turns an AI service refusal into an error for the error handler', async () => {
    answer(400, { detail: 'Named-entity lookup is not enabled.' })
    const { res, error } = await run(entityNeighbors(appConfig), { entityId: 'e', entityType: 'named_entity' })
    expect(res.payload).to.equal(undefined)
    expect(error).to.be.instanceOf(Error)
  })

  it('reports an unreachable AI service as 503 and a slow one as 504, not as a 500', async () => {
    execute.rejects(Object.assign(new Error('fetch failed'), { cause: { code: 'ECONNREFUSED' } }))
    const refused = await run(searchEntities(appConfig), { query: 'x' })
    expect(refused.error.statusCode).to.equal(503)
    execute.rejects(Object.assign(new Error('timeout of 30000ms exceeded'), { name: 'TimeoutError' }))
    for (const handler of [searchEntities(appConfig), entityNeighbors(appConfig)]) {
      const slow = await run(handler, { query: 'x', entityId: 'e', entityType: 'topic' })
      expect(slow.error.statusCode).to.equal(504)
    }
  })
})

describe('entity request schemas', () => {
  it('accepts a minimal grep and bounds the pattern, the work and the answer', () => {
    expect(enterpriseSearchGrepSchema.safeParse({ body: { pattern: 'x' } }).success).to.equal(true)
    for (const body of [
      { pattern: '' },
      { pattern: 'a\nb' },
      { pattern: 'a\0b' },
      { pattern: 'x'.repeat(501) },
      { pattern: 'x', limit: 101 },
      { pattern: 'x', contextLines: 11 },
      { pattern: 'x', maxMatchesPerRecord: 51 },
      { pattern: 'x', timeBudgetMs: 100 },
      { pattern: 'x', timeBudgetMs: 31000 },
      { pattern: 'x', paths: Array(21).fill('a') },
      { pattern: 'x', filters: { nope: ['a'] } },
    ]) {
      expect(enterpriseSearchGrepSchema.safeParse({ body }).success, JSON.stringify(body)).to.equal(false)
    }
  })

  it('limits entity types and neighbour sizes', () => {
    expect(enterpriseSearchEntitiesSchema.safeParse({ body: { query: 'x', entityTypes: ['topic'] } }).success).to.equal(true)
    expect(enterpriseSearchEntitiesSchema.safeParse({ body: { query: 'x', entityTypes: ['galaxy'] } }).success).to.equal(false)
    expect(enterpriseSearchEntitiesSchema.safeParse({ body: { query: 'x', limit: 26 } }).success).to.equal(false)
    expect(enterpriseSearchEntityNeighborsSchema.safeParse({ body: { entityId: 'e', entityType: 'topic' } }).success).to.equal(true)
    expect(enterpriseSearchEntityNeighborsSchema.safeParse({ body: { entityId: 'e' } }).success).to.equal(false)
    expect(enterpriseSearchEntityNeighborsSchema.safeParse({ body: { entityId: 'e', entityType: 'topic', recordLimit: 51 } }).success).to.equal(false)
  })
})

describe('entity routes match the published spec', () => {
  const spec: any = yaml.load(
    readFileSync(join(__dirname, '..', '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml'), 'utf8'),
  )

  it('documents both routes behind a bearer token and the semantic scopes', () => {
    for (const path of ['/search/grep', '/search/entities', '/search/entities/neighbors']) {
      const op = spec.paths[path].post
      expect(op.operationId, path).to.be.a('string')
      expect(Object.keys(op.responses), path).to.include.members(['200', '400', '401', '403', '503'])
      expect(JSON.stringify(op.responses['403']), path).to.contain('semantic:read')
    }
  })

  it('lists exactly the entity types the request schemas accept, and no field for an organisation or user', () => {
    const types: string[] = spec.components.schemas.EntityType.enum
    for (const t of types) {
      expect(enterpriseSearchEntityNeighborsSchema.safeParse({ body: { entityId: 'e', entityType: t } }).success, t).to.equal(true)
    }
    expect(enterpriseSearchEntityNeighborsSchema.safeParse({ body: { entityId: 'e', entityType: 'galaxy' } }).success).to.equal(false)
    for (const name of ['GrepRequest', 'EntitySearchRequest', 'EntityNeighborsRequest']) {
      const fields = Object.keys(spec.components.schemas[name].properties)
      expect(fields.filter((f) => /^(org|user)/i.test(f)), name).to.deep.equal([])
    }
  })
})
