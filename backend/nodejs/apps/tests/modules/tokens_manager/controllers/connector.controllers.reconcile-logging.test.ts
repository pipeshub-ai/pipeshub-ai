import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils'
import * as orchestrator from '../../../../src/modules/crawling_manager/services/connector_schedule_orchestrator'
import { RECONCILE_LOG_MESSAGE } from '../../../../src/modules/crawling_manager/services/connector_schedule_orchestrator'
import { updateConnectorInstanceFiltersSyncConfig } from '../../../../src/modules/tokens_manager/controllers/connector.controllers'
import { runWithRequestContext } from '../../../../src/libs/context/request-context'
import { HttpMethod } from '../../../../src/libs/enums/http-methods.enum'
import { captureLogRecords, newRequestId, recordsOf } from '../../../helpers/log-capture'

/**
 * The background schedule reconcile after a sync-settings save writes one line per reconcile, with the
 * logger's outcome and the why in `reason` (docs/logging.md, "Connector proxy (Node)").
 */

type Row = Record<string, any>

const CONNECTOR = 'conn-1'
const GOOD_SNAPSHOT = {
  statusCode: 200,
  data: { config: { type: 'Jira', isActive: true, createdBy: 'u1', config: { sync: { selectedStrategy: 'SCHEDULED' } } } },
}

const level = (r: Row): string => {
  const l = String(r.level).toLowerCase()
  return l === 'warning' ? 'warn' : l
}

describe('Connector schedule reconcile: one line per reconcile, the cause in reason', () => {
  let records: Row[]
  // The save under test; the reconcile runs in its context (a timer from elsewhere does not).
  let requestId: string
  let clock: sinon.SinonFakeTimers
  let scheduler: { markReconcilePending: sinon.SinonStub; clearReconcilePending: sinon.SinonStub }

  beforeEach(() => {
    records = captureLogRecords()
    requestId = newRequestId('reconcile-logging')
    scheduler = { markReconcilePending: sinon.stub().resolves(), clearReconcilePending: sinon.stub().resolves() }
    // Only timers: the reconcile itself starts on a real setImmediate.
    clock = sinon.useFakeTimers({ toFake: ['setTimeout'] })
  })

  afterEach(() => {
    clock.restore()
    sinon.restore()
  })

  /** Saves sync settings (a 200 from the service), then lets the background reconcile run to its end. */
  const saveSync = async (read: (attempt: number) => Promise<unknown>): Promise<void> => {
    let reads = 0
    sinon.stub(connectorUtils, 'executeConnectorCommand').callsFake((async (_uri: string, method: HttpMethod) => {
      if (method === HttpMethod.PUT) return { statusCode: 200, data: { success: true } }
      reads += 1
      return read(reads)
    }) as any)
    const handler = updateConnectorInstanceFiltersSyncConfig({ connectorBackend: 'http://connector' } as any, scheduler as any)
    const req: any = {
      params: { connectorId: CONNECTOR },
      body: { sync: { selectedStrategy: 'SCHEDULED' } },
      headers: {},
      user: { userId: 'u1', orgId: 'org-1' },
    }
    const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub().returnsThis() }
    await runWithRequestContext({ rootId: requestId, logFields: { connector_id: CONNECTOR } }, async () => {
      await handler(req, res, sinon.stub())
    })
    await new Promise<void>((resolve) => setImmediate(resolve))
    for (let i = 0; i < 4; i += 1) await clock.tickAsync(30_000)
    for (let i = 0; i < 5; i += 1) await new Promise<void>((resolve) => setImmediate(resolve))
  }

  const mine = (): Row[] => recordsOf(records, requestId)
  const reconcileLines = (): Row[] => mine().filter((r) => r.message === RECONCILE_LOG_MESSAGE)

  it('three refused reads are three DEBUG lines and one WARN snapshot_failed, all with the connector, no ERROR', async () => {
    await saveSync(async () => ({ statusCode: 503, data: {} }))

    expect(reconcileLines().map((r) => [level(r), r.outcome, r.reason, r.status, r.attempt, r.connector_id])).to.deep.equal([
      ['debug', 'failure', 'non_2xx', 503, undefined, CONNECTOR],
      ['debug', 'failure', 'non_2xx', 503, undefined, CONNECTOR],
      ['debug', 'failure', 'non_2xx', 503, undefined, CONNECTOR],
      ['warn', 'failure', 'snapshot_failed', undefined, 3, CONNECTOR],
    ])
    expect(mine().filter((r) => level(r) === 'error')).to.deep.equal([])
    expect(scheduler.markReconcilePending.calledOnce).to.equal(true)
  })

  it('a 2xx without the settings is ERROR (no call line reports it), then the next read is used', async () => {
    const reconcile = sinon.stub(orchestrator, 'reconcileConnectorSchedule').resolves('scheduled')

    await saveSync(async (attempt) => (attempt === 1 ? { statusCode: 200, data: {} } : GOOD_SNAPSHOT))

    expect(reconcileLines().map((r) => [level(r), r.reason])).to.deep.equal([['error', 'no_envelope']])
    expect(reconcile.calledOnce).to.equal(true)
    expect(scheduler.clearReconcilePending.calledOnce).to.equal(true)
  })

  it('a 2xx without a connector type is ERROR no_type', async () => {
    sinon.stub(orchestrator, 'reconcileConnectorSchedule').resolves('scheduled')

    await saveSync(async (attempt) => (attempt === 1 ? { statusCode: 200, data: { config: { isActive: true } } } : GOOD_SNAPSHOT))

    expect(reconcileLines().map((r) => [level(r), r.reason])).to.deep.equal([['error', 'no_type']])
  })

  const timeout = Object.assign(new Error('The operation was aborted due to timeout'), { name: 'TimeoutError' })
  const abort = Object.assign(new Error('This operation was aborted'), { name: 'AbortError' })
  const refused = new TypeError('fetch failed', { cause: Object.assign(new Error('connect ECONNREFUSED'), { code: 'ECONNREFUSED' }) })
  for (const [name, error, expected] of [
    ['a timed-out read', timeout, 'debug'],
    ['an aborted read', abort, 'debug'],
    ['a refused connection', refused, 'debug'],
    ['our own bug (a TypeError with no cause)', new TypeError("Cannot read properties of undefined (reading 'config')"), 'error'],
  ] as const) {
    it(`${name}: request_failed at ${expected.toUpperCase()}`, async () => {
      await saveSync(async () => {
        throw error
      })

      const perRead = reconcileLines().filter((r) => r.reason === 'request_failed')
      expect(perRead.map(level)).to.deep.equal([expected, expected, expected])
      expect(perRead[0].metadata?.error, 'the error is kept, as metadata').to.exist
      expect(reconcileLines().filter((r) => r.reason === 'snapshot_failed').map(level)).to.deep.equal(['warn'])
    })
  }

  it('the orchestrator already reported its failure: the catch line is WARN, not a second ERROR', async () => {
    sinon.stub(orchestrator, 'reconcileConnectorSchedule').callsFake(async (_s, logger) => {
      logger.error(RECONCILE_LOG_MESSAGE, { outcome: 'failure', reason: 'queue_failed' })
      throw new Error('queue down')
    })

    await saveSync(async () => GOOD_SNAPSHOT)

    const background = mine().filter((r) => String(r.message).startsWith('Background schedule reconcile failed'))
    expect(background.map(level)).to.deep.equal(['warn'])
    expect(mine().filter((r) => level(r) === 'error').map((r) => r.reason)).to.deep.equal(['queue_failed'])
    expect(scheduler.markReconcilePending.calledOnce).to.equal(true)
  })

  it('a failure nothing else reported (clearing the pending mark) keeps its own level: ERROR', async () => {
    sinon.stub(orchestrator, 'reconcileConnectorSchedule').resolves('scheduled')
    scheduler.clearReconcilePending.rejects(new Error('cache write failed'))

    await saveSync(async () => GOOD_SNAPSHOT)

    const background = mine().filter((r) => String(r.message).startsWith('Background schedule reconcile failed'))
    expect(background.map(level)).to.deep.equal(['error'])
  })

  it('recording the change for the hourly repair failing is one ERROR with the error', async () => {
    scheduler.markReconcilePending.rejects(new Error('cache write failed'))

    await saveSync(async () => ({ statusCode: 503, data: {} }))

    const lines = mine().filter((r) => r.message === 'Could not record a schedule change for the hourly repair')
    expect(lines.map(level)).to.deep.equal(['error'])
    expect(lines[0].connector_id).to.equal(CONNECTOR)
  })
})
