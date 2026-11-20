import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { readableRecordIds } from '../../../../src/modules/enterprise_search/utils/viewer-redaction'
import { accessCheckBreaker } from '../../../../src/modules/enterprise_search/utils/access-check-breaker'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'

/**
 * The access request of one read says which ids are deleted, apart from the ones the reader may not open, and how
 * long its answers hold. A body that says either in a form it can't be read in fails the read closed.
 */

const appConfig: any = {
  aiBackend: 'http://localhost:8000',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
}
const VIEWER = { userId: 'cccccccccccccccccccccccc', orgId: 'bbbbbbbbbbbbbbbbbbbbbbbb' }

const answering = (data: Record<string, unknown>) =>
  sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data } as any)

describe('the access request of one read', () => {
  beforeEach(() => accessCheckBreaker.reset())
  afterEach(() => {
    sinon.restore()
    accessCheckBreaker.reset()
  })

  it('keeps the deleted ids apart from the forbidden ones, and the soonest time its answers may change', async () => {
    const until = Date.now() + 12_000
    answering({ accessible: ['r1'], accessibleUrls: [], unmatchedUrls: [], names: {}, deleted: ['r3'], validUntil: until })

    const readable = await readableRecordIds(appConfig, VIEWER, ['r1', 'r2', 'r3'])

    expect(readable.failed).to.equal(false)
    expect([...readable.ids]).to.deep.equal(['r1'])
    expect([...(readable.deleted ?? [])]).to.deep.equal(['r3'])
    expect(readable.validUntil).to.equal(until)
  })

  it('an older service that says nothing about deleted ids excuses none of them', async () => {
    answering({ accessible: [], accessibleUrls: [], unmatchedUrls: [], names: {} })

    const readable = await readableRecordIds(appConfig, VIEWER, ['r3'])

    expect(readable.failed).to.equal(false)
    expect(readable.deleted?.size ?? 0).to.equal(0)
    expect(readable.validUntil).to.be.a('number')
  })

  for (const [what, data] of [
    ['deleted ids that are not a list of strings', { deleted: 'r3' }],
    ['a validUntil that is not a time', { validUntil: 'soon' }],
  ] as const) {
    it(`a body with ${what} fails the read closed`, async () => {
      answering({ accessible: ['r1'], accessibleUrls: [], unmatchedUrls: [], names: {}, ...data })

      const readable = await readableRecordIds(appConfig, VIEWER, ['r1'])

      expect(readable.failed).to.equal(true)
      expect(readable.ids.size).to.equal(0)
    })
  }

  it('a refused reader (an account that is not active) fails the read closed', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 403, data: { detail: 'inactive' } } as any)

    const readable = await readableRecordIds(appConfig, VIEWER, ['r1'])

    expect(readable.failed).to.equal(true)
  })
})
