import { expect } from 'chai'
import sinon from 'sinon'
import { CONVERSATION_STATUS } from '../../../../../src/modules/enterprise_search/constants/constants'
import {
  conversationExtensions,
  useConversationExtensions,
} from '../../../../../src/modules/enterprise_search/services/conversation/conversation-extensions'
import {
  CONVERSATION_ERROR_CODES,
  DuplicateMessageError,
  RunLostError,
} from '../../../../../src/modules/enterprise_search/services/conversation/domain/errors'
import type { LeaseHandle } from '../../../../../src/modules/enterprise_search/services/conversation/turn/run-lease'
import {
  outcomeForStatus,
  statusFor,
  TurnLifecycle,
  type TurnOutcome,
} from '../../../../../src/modules/enterprise_search/services/conversation/turn/turn-lifecycle'

/** A lease that records what the turn's exit did with it. */
const lease = (release: LeaseHandle['release'] = async () => undefined) => {
  const calls = { stopHeartbeat: 0, release: [] as Array<Parameters<LeaseHandle['release']>> }
  const handle: LeaseHandle = {
    runId: 'run-1',
    sessionId: 'session-1',
    orgId: 'org-1',
    userId: 'user-1',
    renew: async () => true,
    release: async (...args) => {
      calls.release.push(args)
      return release(...args)
    },
    ownershipFilter: () => ({}),
    startHeartbeat: () => undefined,
    stopHeartbeat: () => {
      calls.stopHeartbeat += 1
    },
  }
  return { handle, calls }
}

const flush = () => new Promise((resolve) => setImmediate(resolve))

describe('a turn settles once (TurnLifecycle)', () => {
  let turnCompleted: sinon.SinonStub

  beforeEach(() => {
    turnCompleted = sinon.stub().resolves()
    const base = conversationExtensions()
    useConversationExtensions({ events: { ...base.events, turnCompleted } })
  })
  afterEach(() => useConversationExtensions(undefined))

  it('gives each outcome the session status it leaves, and reads an outcome back from a status', () => {
    const cases: Array<[TurnOutcome, string]> = [
      ['completed', CONVERSATION_STATUS.COMPLETE],
      ['failed', CONVERSATION_STATUS.FAILED],
      ['stopped', CONVERSATION_STATUS.STOPPED],
      ['cancelled', CONVERSATION_STATUS.STOPPED],
      ['duplicate', CONVERSATION_STATUS.COMPLETE],
      ['unstarted', CONVERSATION_STATUS.COMPLETE],
    ]
    for (const [outcome, status] of cases) expect(statusFor(outcome), outcome).to.equal(status)
    expect(outcomeForStatus(CONVERSATION_STATUS.FAILED)).to.equal('failed')
    expect(outcomeForStatus(CONVERSATION_STATUS.STOPPED)).to.equal('stopped')
    expect(outcomeForStatus(undefined)).to.equal('completed')
  })

  it('stops the heartbeat and releases the lease once, however often it is settled', async () => {
    const { handle, calls } = lease()
    const turn = new TurnLifecycle(handle, { error: sinon.stub() })
    await Promise.all([turn.settle('failed'), turn.settle('completed'), turn.settle('stopped')])
    expect(calls.stopHeartbeat).to.equal(1)
    expect(calls.release).to.deep.equal([[CONVERSATION_STATUS.FAILED, { restorePrevious: false }]])
  })

  it('puts back the status the session had when no turn ran (a duplicate or one that never started)', async () => {
    for (const outcome of ['duplicate', 'unstarted'] as const) {
      const { handle, calls } = lease()
      await new TurnLifecycle(handle, { error: sinon.stub() }).settle(outcome)
      expect(calls.release, outcome).to.deep.equal([[CONVERSATION_STATUS.COMPLETE, { restorePrevious: true }]])
    }
  })

  it('announces a completed turn without waiting for it, and nothing else', async () => {
    const { handle } = lease()
    await new TurnLifecycle(handle, { error: sinon.stub() }).settle('completed')
    await flush()
    expect(turnCompleted.calledOnceWithExactly({ orgId: 'org-1', sessionId: 'session-1', senderUserId: 'user-1' })).to.equal(true)
    for (const outcome of ['failed', 'stopped', 'cancelled', 'duplicate', 'unstarted'] as const) {
      await new TurnLifecycle(lease().handle, { error: sinon.stub() }).settle(outcome)
    }
    await flush()
    expect(turnCompleted.callCount).to.equal(1)
  })

  it('logs a lease it could not release or an announcement that failed, and still settles', async () => {
    const error = sinon.stub()
    const { handle } = lease(async () => {
      throw new Error('mongo down')
    })
    turnCompleted.rejects(new Error('bus down'))
    await new TurnLifecycle(handle, { error }).settle('completed')
    await flush()
    expect(error.calledWithMatch('Failed to release the conversation lease', {
      sessionId: 'session-1',
      runId: 'run-1',
      outcome: 'completed',
      error: 'mongo down',
    })).to.equal(true)
    expect(error.calledWithMatch('Failed to publish chat activity', { sessionId: 'session-1', error: 'bus down' })).to.equal(true)
  })
})

describe('turn errors a client reads', () => {
  it('a lost run is a 409 with its own code and no details', () => {
    const e = new RunLostError()
    expect([e.code, e.statusCode, e.publicDetails]).to.deep.equal([CONVERSATION_ERROR_CODES.RUN_LOST, 409, undefined])
  })

  it('a repeated message is a 409 that says whether it was answered, and names the chat only for a first send', () => {
    const repeat = new DuplicateMessageError('m-1', true)
    expect([repeat.code, repeat.statusCode]).to.deep.equal([CONVERSATION_ERROR_CODES.DUPLICATE_MESSAGE, 409])
    expect(repeat.publicDetails).to.deep.equal({ messageId: 'm-1', answered: true })
    expect(new DuplicateMessageError(null, false, 'c-1').publicDetails).to.deep.equal({ conversationId: 'c-1', messageId: null, answered: false })
  })
})
