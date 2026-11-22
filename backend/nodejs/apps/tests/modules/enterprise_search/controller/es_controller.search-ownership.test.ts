import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import {
  SearchQuery,
  SemanticSearchRepository,
  setSemanticSearchRepository,
} from '../../../../src/libs/repositories/semantic-search'
import { FakeSSEResponse, oid } from './chat-test-harness'

const appConfig = { iamBackend: 'http://iam.test', frontendUrl: 'http://app.test' } as never
const SEARCH_ID = String(oid())
const OWNED_ONLY = { owned: true, shared: false }

describe('search writes are owner-only', () => {
  let queries: SearchQuery[]

  beforeEach(() => {
    queries = []
    const record = async (...args: unknown[]) => {
      const q = args.find((a) => a && typeof a === 'object' && !Array.isArray(a)) as
        | { query?: SearchQuery }
        | SearchQuery
      queries.push(('query' in q ? q.query : q) as SearchQuery)
      return null
    }
    setSemanticSearchRepository({
      findVisible: record,
      delete: record,
      deleteVisible: async (...args: unknown[]) => {
        queries.push(args[2] as SearchQuery)
        return 0
      },
    } as unknown as SemanticSearchRepository)
  })

  afterEach(() => {
    setSemanticSearchRepository()
    sinon.restore()
  })

  const request = (body: Record<string, unknown> = {}): never =>
    ({
      headers: {},
      params: { searchId: SEARCH_ID },
      query: { shared: 'true' },
      body,
      user: { userId: String(oid()), orgId: String(oid()) },
      context: { requestId: 'req-search-owner' },
    }) as never

  const cases: Array<[string, (req: never, res: never, next: never) => Promise<unknown>, Record<string, unknown>?]> = [
    ['deleteSearchById', controller.deleteSearchById],
    ['shareSearch', controller.shareSearch(appConfig), { userIds: [String(oid())] }],
    ['unshareSearch', controller.unshareSearch(appConfig), { userIds: [String(oid())] }],
    ['archiveSearch', controller.archiveSearch],
    ['unarchiveSearch', controller.unarchiveSearch],
    ['deleteSearchHistory', controller.deleteSearchHistory],
  ]

  for (const [name, handler, body] of cases) {
    it(`${name} only matches searches the caller owns, and keeps the query filters`, async () => {
      const next = sinon.stub()
      await handler(request(body), new FakeSSEResponse() as never, next as never)

      expect(queries).to.have.length(1)
      expect(queries[0]).to.include({ ...OWNED_ONLY, isShared: true })
      expect(next.firstCall.args[0]).to.have.property('statusCode', 404)
    })
  }

  it('getSearchById still shows a search shared with the caller', async () => {
    await controller.getSearchById(appConfig)(request(), new FakeSSEResponse() as never, sinon.stub() as never)

    expect(queries[0]).to.not.include(OWNED_ONLY)
  })
})
