import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  ACCESS_CHECK_BATCH,
  ANSWER_UNCONFIRMED,
  Readable,
  newReadable,
  readableRecordIds,
  redactMessagesFor,
} from '../../../../src/modules/enterprise_search/utils/viewer-redaction'
import { accessCheckBreaker } from '../../../../src/modules/enterprise_search/utils/access-check-breaker'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'

/**
 * One read of a shared chat checks the viewer's access within one deadline, in one request for up to
 * `ACCESS_CHECK_BATCH` ids (more go four chunks at a time), and a breaker stops reads asking while the
 * service keeps failing. Every way it can't answer fails closed: nothing unconfirmed is shown.
 */

const appConfig: any = {
  aiBackend: 'http://localhost:8000',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
}
const VIEWER = { userId: 'cccccccccccccccccccccccc', orgId: 'bbbbbbbbbbbbbbbbbbbbbbbb' }
const OWNER = 'aaaaaaaaaaaaaaaaaaaaaaaa'

const ids = (n: number): string[] => Array.from({ length: n }, (_, i) => `r${i}`)
const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))
const allReadable = (body: { recordIds: string[] }) => ({
  statusCode: 200,
  data: { accessible: body.recordIds, accessibleUrls: [], unmatchedUrls: [], names: {} },
})

/** A query service that never answers: each call fails when its own budget runs out, as fetch does. */
const timesOut = async function (this: { timeoutMs: number }): Promise<never> {
  await sleep(this.timeoutMs + 5)
  throw new DOMException('The operation was aborted due to timeout', 'TimeoutError')
}

/** Five reads that time out open the breaker: reads that gave the service time to answer, on a free event loop. */
const openTheBreaker = async (): Promise<void> => {
  const reads = await Promise.all(
    Array.from({ length: 5 }, () => readableRecordIds(appConfig, VIEWER, ['r1'], [], undefined, 300)),
  )
  expect(reads.map((read) => read.failed)).to.deep.equal([true, true, true, true, true])
}

describe('the viewer access check of one read', () => {
  beforeEach(() => accessCheckBreaker.reset())
  afterEach(() => {
    sinon.restore()
    accessCheckBreaker.reset()
  })

  it('asks for every id of a read in one request', async () => {
    const asked: number[] = []
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async function (this: any) {
      const body = JSON.parse(this.body)
      asked.push(body.recordIds.length)
      return allReadable(body) as any
    })

    const readable = await readableRecordIds(appConfig, VIEWER, ids(ACCESS_CHECK_BATCH))

    expect(asked).to.deep.equal([ACCESS_CHECK_BATCH])
    expect(readable.ids.size).to.equal(ACCESS_CHECK_BATCH)
  })

  it('past one request, asks four chunks at a time, and settles only once every chunk answered', async () => {
    let inFlight = 0
    let most = 0
    const asked: number[] = []
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async function (this: any) {
      const body = JSON.parse(this.body)
      inFlight += 1
      most = Math.max(most, inFlight)
      await sleep(20)
      inFlight -= 1
      asked.push(body.recordIds.length)
      return allReadable(body) as any
    })

    const readable = await readableRecordIds(appConfig, VIEWER, ids(8 * ACCESS_CHECK_BATCH + 100))

    expect(most).to.equal(4)
    expect(asked.sort((a, b) => a - b)).to.deep.equal([100, ...Array<number>(8).fill(ACCESS_CHECK_BATCH)])
    expect(readable.failed).to.equal(false)
    expect(readable.ids.size).to.equal(8 * ACCESS_CHECK_BATCH + 100)
  })

  it('a chunk that fails leaves every id of the read unknown', async () => {
    let call = 0
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async function (this: any) {
      call += 1
      if (call === 3) return { statusCode: 503, data: { detail: 'Access check unavailable' } } as any
      return allReadable(JSON.parse(this.body)) as any
    })

    const readable = await readableRecordIds(appConfig, VIEWER, ids(2 * ACCESS_CHECK_BATCH + 600))

    expect(readable.failed).to.equal(true)
    expect(readable.ids.size).to.equal(0)
    expect(readable.asked.size).to.equal(0)
  })

  it('fails closed once the read runs past its one deadline, even when no call gives up', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(() => new Promise<never>(() => undefined))
    const started = Date.now()

    const outcome = await Promise.race([
      readableRecordIds(appConfig, VIEWER, ids(10), [], undefined, 100),
      sleep(3000).then(() => 'still waiting' as const),
    ])

    expect(outcome).to.not.equal('still waiting')
    expect((outcome as Readable).failed).to.equal(true)
    expect((outcome as Readable).ids.size).to.equal(0)
    expect(Date.now() - started).to.be.below(1000)
  })

  it('gives no call longer than what is left of the read’s deadline', async () => {
    const given: number[] = []
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async function (this: any) {
      given.push(this.timeoutMs)
      await sleep(30)
      return allReadable(JSON.parse(this.body)) as any
    })

    await readableRecordIds(appConfig, VIEWER, ids(5 * ACCESS_CHECK_BATCH + 600), [], undefined, 5000)

    expect(given).to.have.length(6)
    expect(Math.max(...given)).to.be.at.most(5000)
    // The last two chunks start after the first four answered, with less of the deadline left.
    expect(Math.max(...given.slice(4))).to.be.below(4990)
  })

  it('after five reads that time out, the next fails closed without asking the query service', async () => {
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(timesOut)
    await openTheBreaker()
    const calls = execute.callCount

    const refused = await readableRecordIds(appConfig, VIEWER, ['r1'])

    expect(refused.failed).to.equal(true)
    expect(refused.ids.size).to.equal(0)
    expect(execute.callCount).to.equal(calls)
  })

  it('once fifteen seconds have passed one read asks again, and its answer lets the others through', async () => {
    const clock = sinon.useFakeTimers({ toFake: ['Date'], now: Date.now() })
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(timesOut)
    await openTheBreaker()
    execute.callsFake(async function (this: any) {
      return allReadable(JSON.parse(this.body)) as any
    })

    expect((await readableRecordIds(appConfig, VIEWER, ['r1'])).failed, 'still open').to.equal(true)
    clock.tick(15_000)
    const probe = await readableRecordIds(appConfig, VIEWER, ['r1'])
    const after = await readableRecordIds(appConfig, VIEWER, ['r2'])

    expect(probe.failed).to.equal(false)
    expect(after.failed).to.equal(false)
    expect(after.ids.has('r2')).to.equal(true)
  })

  it('while it is open, another user’s answer that names a record is hidden as unconfirmed', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(timesOut)
    await openTheBreaker()

    const [view] = await redactMessagesFor(
      appConfig,
      VIEWER,
      [
        {
          messageType: 'bot_response',
          content: 'The figures are 42',
          citations: [{ citationData: { metadata: { recordId: 'r1' } } }],
        } as any,
      ],
      () => OWNER,
    )

    expect((view as any).answerHidden).to.deep.equal({ ...ANSWER_UNCONFIRMED })
    expect(JSON.stringify(view)).to.not.contain('42')
  })

  it('a read asks within its own deadline, whatever a call would allow, and asks nothing once it has passed', async () => {
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(timesOut)
    const known = newReadable()
    known.deadline = Date.now() + 40
    const started = Date.now()
    expect((await readableRecordIds(appConfig, VIEWER, ['r1'], [], known, 15_000)).failed).to.equal(true)
    expect(Date.now() - started).to.be.lessThan(1_000)
    expect((await readableRecordIds(appConfig, VIEWER, ['r2'], [], known, 15_000)).failed).to.equal(true)
    expect(execute.callCount).to.equal(1)
  })

  it('a read that asks no one asks nothing, whatever its deadline, and what it holds stays unknown', async () => {
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async function (this: any) {
      return allReadable(JSON.parse(this.body)) as any
    })
    const known: Readable = { ...newReadable(), askNoOne: true, deadline: Date.now() + 60_000 }
    const read = await readableRecordIds(appConfig, VIEWER, ['r1'], ['https://example.com/a'], known)
    expect(read.failed).to.equal(true)
    expect(read.ids.has('r1')).to.equal(false)
    expect(execute.callCount).to.equal(0)
    const shown = await redactMessagesFor(
      appConfig,
      VIEWER,
      [{ _id: 'm1', messageType: 'bot_response', content: 'R says 42.', referenceData: [{ recordId: 'r1', name: 'R' }] } as any],
      () => OWNER,
      { known },
    )
    expect((shown[0] as any).answerHidden, 'an answer that names a record').to.not.equal(undefined)
    expect(execute.callCount).to.equal(0)
  })

  it('an id a read could not settle is not asked again in that read, and stays unknown', async () => {
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(timesOut)
    const known = newReadable()
    expect((await readableRecordIds(appConfig, VIEWER, ['r1'], [], known, 20)).failed).to.equal(true)
    const again = await readableRecordIds(appConfig, VIEWER, ['r1'], [], known, 15_000)
    expect(again.failed).to.equal(true)
    expect(again.ids.has('r1')).to.equal(false)
    expect(execute.callCount).to.equal(1)
  })
})
