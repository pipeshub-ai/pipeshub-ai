import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { readFileSync } from 'fs'
import { join } from 'path'
import yaml from 'js-yaml'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { ValidationMiddleware } from '../../../../src/libs/middlewares/validation.middleware'
import {
  coMentionedEntities,
  entityCapabilities,
  entityNeighbors,
  entityRecords,
  grepRecords,
  recordsByEntities,
  recordsByPerson,
  recordsByValue,
  searchEntities,
} from '../../../../src/modules/enterprise_search/controller/exact_search.controller'
import {
  enterpriseSearchCoMentionedSchema,
  enterpriseSearchEntityRecordsSchema,
  enterpriseSearchRecordsByEntitiesSchema,
  enterpriseSearchRecordsByPersonSchema,
  enterpriseSearchRecordsByValueSchema,
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

  it('keeps the AI service\'s 501 (no local file storage) a 501, with its words', async () => {
    answer(501, { detail: 'Exact-text search needs local file storage.' })
    const { error } = await run(grepRecords(appConfig), { pattern: 'x' })
    expect(error.statusCode).to.equal(501)
    expect(error.message).to.contain('local file storage')
  })

  it('refuses person and organization with 400 before the AI service is called, and forwards named_entity, as the routes chain them', async () => {
    // the validator then the controller, in the order es.routes.ts mounts them; the extraction flag is read only by the AI service
    const route = async (schema: any, handler: any, body: any) => {
      const req: any = { body, query: {}, params: {}, headers: {}, path: '/', method: 'POST' }
      let error: any
      await ValidationMiddleware.validate(schema)(req, {} as any, (e?: any) => { error = e })
      return error ?? (await run(handler, req.body)).error
    }
    answer(200, { status: 'success', results: [], records: [] })
    for (const kind of ['person', 'organization']) {
      expect((await route(enterpriseSearchEntitiesSchema, searchEntities(appConfig), { query: 'x', entityTypes: [kind] }))?.statusCode, kind).to.equal(400)
      expect((await route(enterpriseSearchEntityNeighborsSchema, entityNeighbors(appConfig), { entityId: 'e', entityType: kind }))?.statusCode, kind).to.equal(400)
    }
    expect(bodies).to.deep.equal([])
    expect(await route(enterpriseSearchEntitiesSchema, searchEntities(appConfig), { query: 'x', entityTypes: ['named_entity'] })).to.equal(undefined)
    expect(await route(enterpriseSearchEntityNeighborsSchema, entityNeighbors(appConfig), { entityId: 'e', entityType: 'named_entity' })).to.equal(undefined)
    expect(bodies.map((b) => JSON.parse(b))).to.deep.equal([
      { query: 'x', entityTypes: ['named_entity'] },
      { entityId: 'e', entityType: 'named_entity' },
    ])
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

describe('entity tool routes', () => {
  let execute: sinon.SinonStub
  let calls: Array<{ uri: string; method: string; body: unknown }>

  beforeEach(() => {
    calls = []
    execute = sinon.stub(AIServiceCommand.prototype, 'execute')
    execute.callsFake(async function (this: any) {
      calls.push({ uri: this.uri ?? this.url, method: this.method, body: this.body })
      return { statusCode: 200, data: { text: 'ok' } }
    })
  })
  afterEach(() => sinon.restore())

  const ROUTES: Array<[string, any, string, Record<string, unknown>]> = [
    ['entities/records', entityRecords, 'listEntityRecords', { entityId: 'e', entityType: 'person', limit: 5, cursor: 'c' }],
    ['entities/co-mentioned', coMentionedEntities, 'listCoMentionedEntities', { entityIds: ['a'], kinds: ['person'] }],
    ['records/by-person', recordsByPerson, 'listRecordsByPerson', { person: 'me', roles: ['assignee'] }],
    ['records/by-value', recordsByValue, 'listRecordsByValue', { amountMin: 1, currency: 'USD' }],
    ['records/by-entities', recordsByEntities, 'searchRecordsByEntities', { mentions: [{ name: 'Acme' }], match: 'all' }],
  ]
  for (const [path, handler, , body] of ROUTES) {
    it(`forwards ${path} with only its fields, so a body cannot name another user or organisation`, async () => {
      const { res, error } = await run(handler(appConfig), { ...body, orgId: 'other-org', userId: 'u', evil: 'x' })
      expect(error).to.equal(undefined)
      expect(res.statusCode).to.equal(200)
      expect(calls[0].method).to.equal('POST')
      expect(calls[0].uri).to.equal(`http://ai:8000/api/v1/search/${path}`)
      expect(JSON.parse(String(calls[0].body))).to.deep.equal(body)
    })
  }

  it('asks for the capabilities with a GET and no body', async () => {
    execute.callsFake(async function (this: any) {
      calls.push({ uri: this.uri ?? this.url, method: this.method, body: this.body })
      return { statusCode: 200, data: { recordsByPerson: true } }
    })
    const { res } = await run(entityCapabilities(appConfig), { orgId: 'other-org' })
    expect(res.payload).to.deep.equal({ recordsByPerson: true })
    expect(calls[0].method).to.equal('GET')
    expect(calls[0].body).to.equal(undefined)
    expect(calls[0].uri).to.equal('http://ai:8000/api/v1/search/entities/capabilities')
  })

  it('passes a lookup the deployment does not serve on as an error, not an empty answer', async () => {
    execute.resolves({ statusCode: 404, data: { detail: 'Named-entity lookup is not enabled.' } })
    const { res, error } = await run(recordsByValue(appConfig), { amountMin: 1 })
    expect(res.payload).to.equal(undefined)
    expect(error).to.be.instanceOf(Error)
  })

  const spec: any = yaml.load(
    readFileSync(join(__dirname, '..', '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml'), 'utf8'),
  )
  const VALIDATORS: Array<[string, any, string]> = [
    ['/search/entities/records', enterpriseSearchEntityRecordsSchema, 'EntityRecordsRequest'],
    ['/search/entities/co-mentioned', enterpriseSearchCoMentionedSchema, 'CoMentionedEntitiesRequest'],
    ['/search/records/by-person', enterpriseSearchRecordsByPersonSchema, 'RecordsByPersonRequest'],
    ['/search/records/by-value', enterpriseSearchRecordsByValueSchema, 'RecordsByValueRequest'],
    ['/search/records/by-entities', enterpriseSearchRecordsByEntitiesSchema, 'RecordsByEntitiesRequest'],
  ]
  for (const [path, validator, name] of VALIDATORS) {
    it(`validates ${path} with exactly the published fields, behind the semantic scopes`, () => {
      expect(Object.keys(validator.shape.body.shape).sort()).to.deep.equal(Object.keys(spec.components.schemas[name].properties).sort())
      const op = spec.paths[path].post
      expect(op.requestBody.content['application/json'].schema.$ref).to.equal(`#/components/schemas/${name}`)
      expect(JSON.stringify(op.responses['403'])).to.contain('semantic:read')
      expect(Object.keys(spec.components.schemas[name].properties).filter((f) => /^(org|user)/i.test(f))).to.deep.equal([])
    })
  }

  it('accepts what each lookup needs and refuses what it cannot take', () => {
    const ok = (schema: any, body: unknown) => schema.safeParse({ body }).success
    expect(ok(enterpriseSearchEntityRecordsSchema, { entityId: 'e', entityType: 'organization' })).to.equal(true)
    expect(ok(enterpriseSearchEntityRecordsSchema, { entityId: 'e', entityType: 'galaxy' })).to.equal(false)
    expect(ok(enterpriseSearchEntityRecordsSchema, { entityId: 'e', entityType: 'topic', limit: 51 })).to.equal(false)
    expect(ok(enterpriseSearchCoMentionedSchema, { entityIds: [] })).to.equal(false)
    expect(ok(enterpriseSearchRecordsByPersonSchema, { person: '' })).to.equal(false)
    expect(ok(enterpriseSearchRecordsByValueSchema, { amountMin: 'lots' })).to.equal(false)
    expect(ok(enterpriseSearchRecordsByEntitiesSchema, { mentions: [] })).to.equal(false)
    expect(ok(enterpriseSearchRecordsByEntitiesSchema, { mentions: [{ name: 'Acme' }], match: 'some' })).to.equal(false)
    expect(ok(enterpriseSearchRecordsByEntitiesSchema, { mentions: Array(5).fill({ name: 'a' }) })).to.equal(true)
    expect(ok(enterpriseSearchRecordsByEntitiesSchema, { mentions: Array(6).fill({ name: 'a' }) })).to.equal(false)
  })
})
