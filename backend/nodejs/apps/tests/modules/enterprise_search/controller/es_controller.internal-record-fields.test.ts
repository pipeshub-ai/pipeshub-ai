import 'reflect-metadata'
import * as fs from 'fs'
import * as path from 'path'
import { expect } from 'chai'
import sinon from 'sinon'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import {
  SemanticSearchRepository,
  setSemanticSearchRepository,
} from '../../../../src/libs/repositories/semantic-search'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { IAMServiceCommand } from '../../../../src/libs/commands/iam/iam.service.command'
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema'
import { apiRecord, apiStoredRecords } from '../../../../src/modules/enterprise_search/utils/internal-record-fields'
import { oid } from './chat-test-harness'

/**
 * Records a search carries (in its response, saved with it, and returned by the history routes)
 * leave out the fields the services keep for themselves, as the record endpoint does.
 */

const appConfig: any = { aiBackend: 'http://localhost:8000', iamBackend: 'http://iam.test', frontendUrl: 'http://app.test', jwtSecret: 'test-jwt-secret', scopedJwtSecret: 'test-scoped-secret' }
const USER = String(oid())
const ORG = String(oid())
const SEARCH_ID = String(oid())

const KEPT = { _key: 'rec-1', recordName: 'a.pdf' }
const INTERNAL = {
  firstQueuedAt: 1_700_000_000_000,
  failedAt: 1_700_000_100_000,
  indexingAttempts: 2,
  pageCount: 12,
  republishCount: 3,
  p_color: 'red',
}
const stored = () => ({ ...KEPT, ...INTERNAL })
const leaked = (value: unknown): string[] =>
  Object.keys(INTERNAL).filter((name) => JSON.stringify(value).includes(`\\"${name}\\"`) || JSON.stringify(value).includes(`"${name}"`))

const req = (extra: Record<string, unknown> = {}): any => ({
  headers: { authorization: 'Bearer t' },
  body: {},
  params: { searchId: SEARCH_ID },
  query: {},
  user: { userId: USER, orgId: ORG },
  context: { requestId: 'r' },
  on: sinon.stub(),
  ...extra,
})
const res = (): any => {
  const r: any = { status: sinon.stub(), json: sinon.stub() }
  r.status.returns(r)
  return r
}

const savedSearch = () => ({
  _id: SEARCH_ID,
  userId: USER,
  orgId: ORG,
  query: 'q',
  citationIds: [],
  sharedWith: [],
  records: { 'rec-1': JSON.stringify(stored()) },
})

const useRepository = (repository: Partial<SemanticSearchRepository>) =>
  setSemanticSearchRepository(repository as SemanticSearchRepository)

describe('the helper', () => {
  it('drops the internal names and the projected property keys, and nothing else', () => {
    expect(apiRecord(stored())).to.deep.equal(KEPT)
  })

  it('strips a saved search\'s records, JSON strings and objects, from a map or an object', () => {
    const out = apiStoredRecords(new Map([['a', JSON.stringify(stored())], ['b', 'not json']])) as Record<string, unknown>
    expect(JSON.parse(out.a as string)).to.deep.equal(KEPT)
    expect(out.b).to.equal('not json')
    expect(apiStoredRecords({ a: stored() })).to.deep.equal({ a: KEPT })
  })
})

describe('the helper, edge cases', () => {
  it('leaves non-records alone and tells a projected key from a lookalike', () => {
    expect(apiRecord(null)).to.equal(null)
    expect(apiRecord([stored()])).to.deep.equal([stored()]) // arrays are not records
    expect(apiRecord({ p_: 1, 'p_x-y': 2, P_x: 3, p_ok: 4 })).to.deep.equal({ p_: 1, 'p_x-y': 2, P_x: 3 })
    const out = apiStoredRecords(new Map<string, unknown>([['a', JSON.stringify(stored())], ['b', stored()], ['c', '{bad']])) as Record<string, unknown>
    expect(leaked(out)).to.deep.equal([])
    expect(out.c).to.equal('{bad')
    expect(apiStoredRecords(undefined)).to.equal(undefined)
    expect(apiStoredRecords({ a: '"x"', b: '[1]' })).to.deep.equal({ a: '"x"', b: '[1]' })
  })

  it('uses the projected-key pattern the Python side uses', () => {
    const ts = fs.readFileSync(path.join(__dirname, '../../../../src/modules/enterprise_search/utils/internal-record-fields.ts'), 'utf8')
    const py = fs.readFileSync(path.join(__dirname, '../../../../../../python/app/services/graph_db/common/utils.py'), 'utf8')
    const tsPattern = /PROJECTED_PROPERTY_KEY = \/(.+)\/;/.exec(ts)?.[1]
    const pyPattern = /_PROJECTED_PROPERTY_KEY = re\.compile\(r"(.+)"\)/.exec(py)?.[1]
    expect(tsPattern).to.be.a('string')
    expect(tsPattern).to.equal(pyPattern)
  })
})

describe('records in search responses and history carry no internal field', () => {
  afterEach(() => {
    setSemanticSearchRepository()
    sinon.restore()
  })

  const aiAnswers = (data: Record<string, unknown>) =>
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async function (this: any) {
      if (this.uri.endsWith('/chat/records/access')) {
        return { statusCode: 200, data: { accessible: ['rec-1'], accessibleUrls: [], unmatchedUrls: [] }, msg: '' } as any
      }
      return { statusCode: 200, data } as any
    })

  const searchData = () => ({
    searchResults: [{ content: 'x', chunkIndex: 0, citationType: 'text', metadata: {} }],
    records: [stored()],
    virtual_to_record_map: { v1: stored() },
  })

  it('POST /search returns none, and saves none with the search', async () => {
    aiAnswers(searchData())
    sinon.stub(Citation, 'insertMany').resolves([{ _id: oid() }] as any)
    let created: any
    useRepository({
      create: async (input: any) => {
        created = input
        return { _id: SEARCH_ID } as any
      },
    })
    const r = res()
    const next = sinon.stub()

    await controller.search(appConfig)(req({ body: { query: 'q', limit: 10 } }), r, next)

    sinon.assert.notCalled(next)
    const sent = r.json.firstCall.args[0].searchResponse
    expect(sent.records[0]).to.include(KEPT)
    expect(sent.virtual_to_record_map.v1).to.include(KEPT)
    expect(leaked(sent)).to.deep.equal([])
    expect(leaked(Object.fromEntries(created.records))).to.deep.equal([])
    expect([...created.records.values()].length).to.equal(1)
  })

  it('a date-bounded search returns none in the matches written outside its dates', () => {
    const out = controller.buildSearchResponseForClient({
      searchResults: [],
      records: [stored()],
      virtual_to_record_map: { v: stored() },
      outsideDates: { note: 'n', searchResults: [], records: [stored()] },
    } as never)

    expect(out.outsideDates).to.have.property('note', 'n')
    expect((out.outsideDates as { records: unknown[] }).records[0]).to.deep.equal(KEPT)
    expect(leaked(out)).to.deep.equal([])
  })

  it('POST /search without saving returns none', async () => {
    aiAnswers(searchData())
    const r = res()
    await controller.search(appConfig)(req({ body: { query: 'q', limit: 10, saveHistory: false } }), r, sinon.stub())
    expect(leaked(r.json.firstCall.args[0].searchResponse)).to.deep.equal([])
  })

  it('GET /search/:id returns none from a stored copy', async () => {
    aiAnswers({})
    useRepository({ findVisible: async () => savedSearch() as any })
    const r = res()
    await controller.getSearchById(appConfig)(req(), r, sinon.stub())

    const [out] = r.json.firstCall.args[0]
    expect(JSON.parse(out.records['rec-1'])).to.deep.equal(KEPT)
    expect(leaked(out)).to.deep.equal([])
  })

  it('GET /search (history) returns none from a stored copy', async () => {
    aiAnswers({})
    useRepository({ listVisible: async () => ({ rows: [savedSearch() as any], total: 1 }) })
    const r = res()
    await controller.searchHistory(appConfig)(req(), r, sinon.stub())

    const [out] = r.json.firstCall.args[0].searchHistory
    expect(JSON.parse(out.records['rec-1'])).to.deep.equal(KEPT)
    expect(leaked(out)).to.deep.equal([])
  })

  it('PATCH /search/:id/share returns none from a stored copy', async () => {
    sinon.stub(IAMServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as any)
    useRepository({
      findVisible: async () => savedSearch() as any,
      setSharing: async () => ({ ...savedSearch(), isShared: true, toJSON() { return { ...savedSearch(), isShared: true } } }) as any,
    })
    const r = res()
    const next = sinon.stub()
    await controller.shareSearch(appConfig)(req({ body: { userIds: [String(oid())] } }), r, next)

    sinon.assert.notCalled(next)
    const out = r.json.firstCall.args[0]
    expect(out.isShared).to.equal(true)
    expect(JSON.parse(out.records['rec-1'])).to.deep.equal(KEPT)
    expect(leaked(out)).to.deep.equal([])
  })
})
