import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { ServiceUnavailableError } from '../../../../src/libs/errors/http.errors'
import {
  ATTACHMENTS_UNVERIFIED_MESSAGE,
  filterOwnedAttachments,
} from '../../../../src/modules/enterprise_search/utils/attachment-validation'

const appConfig = {
  aiBackend: 'http://ai.test',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
} as never

const caller = { userId: 'u1', orgId: 'o1' }
const refs = [{ recordId: 'r1', recordName: 'a' }, { recordId: 'r2', recordName: 'b' }] as never[]
const ok = (recordIds: unknown) => ({ statusCode: 200, data: { recordIds } }) as never

describe('filterOwnedAttachments', () => {
  afterEach(() => sinon.restore())

  it('keeps only the ids the AI backend confirms', async () => {
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute').resolves(ok(['r2']))
    expect(await filterOwnedAttachments(appConfig, caller, refs)).to.deep.equal([refs[1]])
    expect(execute.callCount).to.equal(1)
  })

  it('drops every attachment on a 4xx without retrying', async () => {
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 403, data: {} } as never)
    expect(await filterOwnedAttachments(appConfig, caller, refs)).to.deep.equal([])
    expect(execute.callCount).to.equal(1)
  })

  it('retries a timeout once and uses the second answer', async () => {
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute')
    execute.onFirstCall().rejects(new Error('The operation was aborted due to timeout'))
    execute.onSecondCall().resolves(ok(['r1', 'r2']))
    expect(await filterOwnedAttachments(appConfig, caller, refs)).to.deep.equal(refs)
    expect(execute.callCount).to.equal(2)
  })

  for (const [label, fail] of [
    ['a transport error', (s: sinon.SinonStub) => s.rejects(new Error('fetch failed'))],
    ['a 503', (s: sinon.SinonStub) => s.resolves({ statusCode: 503, data: {} } as never)],
  ] as const) {
    it(`fails the turn with a retryable 503 after ${label} on both attempts`, async () => {
      const execute = sinon.stub(AIServiceCommand.prototype, 'execute')
      fail(execute)
      let caught: unknown
      try {
        await filterOwnedAttachments(appConfig, caller, refs)
      } catch (error) {
        caught = error
      }
      expect(caught).to.be.instanceOf(ServiceUnavailableError)
      expect((caught as ServiceUnavailableError).message).to.equal(ATTACHMENTS_UNVERIFIED_MESSAGE)
      expect(execute.callCount).to.equal(2)
    })
  }

  it('makes no call when there is nothing to check', async () => {
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute')
    expect(await filterOwnedAttachments(appConfig, caller, [])).to.deep.equal([])
    expect(await filterOwnedAttachments(appConfig, caller, undefined)).to.equal(undefined)
    expect(execute.called).to.equal(false)
  })
})
