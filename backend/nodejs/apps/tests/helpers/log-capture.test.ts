import { expect } from 'chai'
import sinon from 'sinon'
import { Logger } from '../../src/libs/services/logger.service'
import { captureLogRecords, inRequestContext, newRequestId, recordsOf } from './log-capture'

describe('log-capture: a test counts only its own request’s lines', () => {
  afterEach(() => sinon.restore())

  it('keeps the request’s lines, its background work included, and drops other requests’ and context-free lines', async () => {
    const records = captureLogRecords()
    const logger = Logger.getInstance({ service: 'log-capture-test' })
    const ours = newRequestId('log-capture')
    const theirs = newRequestId('log-capture')

    // Like a chat run's heartbeat another test left running: fires during this test, in its own context or in none.
    const timers = [
      inRequestContext(theirs, () => setInterval(() => logger.warn('Failed to record run activity'), 0)),
      setInterval(() => logger.warn('Failed to record run activity'), 0),
    ]
    try {
      await inRequestContext(ours, async () => {
        logger.info('ours.start')
        await new Promise<void>((resolve) =>
          setImmediate(() => {
            logger.debug('ours.background')
            resolve()
          }),
        )
        logger.warn('ours.end')
      })
      await new Promise((resolve) => setTimeout(resolve, 5))
    } finally {
      timers.forEach(clearInterval)
    }

    expect(records.some((r) => r.message === 'Failed to record run activity' && r.request_id === theirs)).to.equal(true)
    expect(records.some((r) => r.message === 'Failed to record run activity' && r.request_id === undefined)).to.equal(true)
    expect(recordsOf(records, ours).map((r) => r.message)).to.deep.equal(['ours.start', 'ours.background', 'ours.end'])
  })

  it('records every level, whichever logger wrote it', () => {
    const records = captureLogRecords()
    const id = newRequestId('log-capture')
    inRequestContext(id, () => {
      Logger.getInstance({ service: 'one' }).debug('a')
      Logger.getInstance({ service: 'two' }).error('b')
    })

    expect(recordsOf(records, id).map((r) => [r.message, r.service, String(r.level).toLowerCase()])).to.deep.equal([
      ['a', 'one', 'debug'],
      ['b', 'two', 'error'],
    ])
  })

  it('gives each call a new request id', () => {
    expect(newRequestId('x')).to.not.equal(newRequestId('x'))
  })
})
