import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { readableRecordIds } from '../../../../src/modules/enterprise_search/utils/viewer-redaction'
import { accessCheckBreaker } from '../../../../src/modules/enterprise_search/utils/access-check-breaker'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import {
  runWithRequestContext,
  setContextIdentity,
  setContextKnowledgeLimit,
} from '../../../../src/libs/context/request-context'

/**
 * A read made with a token limited to some sources (a connected app's or a personal access token's knowledge limit)
 * is held to them: the access request of the read carries the limit, so the query service counts what lies outside
 * it as unreadable, as it does for every other read by that token.
 */

const appConfig: any = {
  aiBackend: 'http://localhost:8000',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
}
const CAROL = { userId: 'cccccccccccccccccccccccc', orgId: 'bbbbbbbbbbbbbbbbbbbbbbbb' }
const BOB = { userId: 'dddddddddddddddddddddddd', orgId: 'bbbbbbbbbbbbbbbbbbbbbbbb' }
const LIMIT = { apps: [], recordGroups: [], records: ['q'] }

const asked = () =>
  sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
    statusCode: 200,
    data: { accessible: [], accessibleUrls: [], unmatchedUrls: [], names: {}, deleted: [] },
  } as any)

const bodiesOf = (execute: sinon.SinonStub) =>
  execute.thisValues.map((command) => {
    const body = (command as unknown as { body: unknown }).body
    return typeof body === 'string' ? JSON.parse(body) : body
  })

const asCaller = <T>(limit: unknown, fn: () => Promise<T>): Promise<T> =>
  runWithRequestContext({ rootId: 'r' }, () => {
    setContextIdentity({ userId: CAROL.userId, orgId: CAROL.orgId })
    setContextKnowledgeLimit(limit)
    return fn()
  })

describe("a read held to its token's knowledge limit", () => {
  beforeEach(() => accessCheckBreaker.reset())
  afterEach(() => {
    sinon.restore()
    accessCheckBreaker.reset()
  })

  it('sends the limit with the access request of the caller’s own read', async () => {
    const execute = asked()

    await asCaller(LIMIT, () => readableRecordIds(appConfig, CAROL, ['r', 'q']))

    expect(bodiesOf(execute)).to.deep.equal([{ recordIds: ['r', 'q'], webUrls: [], knowledgeLimit: LIMIT }])
  })

  it('a check made in that request for someone else is not bound by the caller’s token', async () => {
    const execute = asked()

    await asCaller(LIMIT, () => readableRecordIds(appConfig, BOB, ['r']))

    expect(bodiesOf(execute)).to.deep.equal([{ recordIds: ['r'], webUrls: [] }])
  })

  it('a token with no limit, or no request at all, sends none', async () => {
    const execute = asked()

    await asCaller(undefined, () => readableRecordIds(appConfig, CAROL, ['r']))
    await readableRecordIds(appConfig, CAROL, ['r2'])

    expect(bodiesOf(execute)).to.deep.equal([
      { recordIds: ['r'], webUrls: [] },
      { recordIds: ['r2'], webUrls: [] },
    ])
  })

  it('a limit that names nothing reaches nothing, never everything', async () => {
    const execute = asked()

    await asCaller({}, () => readableRecordIds(appConfig, CAROL, ['r']))

    expect(bodiesOf(execute)[0]).to.deep.equal({ recordIds: ['r'], webUrls: [], knowledgeLimit: { apps: [], recordGroups: [], records: [] } })
  })
})
