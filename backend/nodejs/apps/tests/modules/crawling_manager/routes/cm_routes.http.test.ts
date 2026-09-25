import 'reflect-metadata'
import { expect } from 'chai'
import http from 'http'
import { AddressInfo } from 'net'
import express from 'express'
import jwt from 'jsonwebtoken'
import sinon from 'sinon'
import { Container } from 'inversify'
import { createCrawlingManagerRouter } from '../../../../src/modules/crawling_manager/routes/cm_routes'
import { CrawlingSchedulerService } from '../../../../src/modules/crawling_manager/services/crawling_service'
import { CrawlingWorkerService } from '../../../../src/modules/crawling_manager/services/crawling_worker'
import { ConnectorsCrawlingService } from '../../../../src/modules/crawling_manager/services/connectors/connectors'
import { SyncEventProducer } from '../../../../src/modules/knowledge_base/services/sync_events.service'
import { AuthMiddleware } from '../../../../src/libs/middlewares/auth.middleware'
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service'
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware'
import { INVALID_PATH_SEGMENT_MESSAGE } from '../../../../src/libs/middlewares/safe-path-params.middleware'
import { Logger } from '../../../../src/libs/services/logger.service'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { UserActivities } from '../../../../src/modules/auth/schema/userActivities.schema'
import { OAuthTokenService } from '../../../../src/modules/oauth_provider/services/oauth_token.service'
import { AppConfig } from '../../../../src/modules/tokens_manager/config/config'
import {
  ApiResponse,
  FakeBackend,
  RecordedCall,
  buildConfig,
  errorMessage,
} from '../../tokens_manager/routes/connectors-http-harness'
import { FakeCrawlingQueue, FakeJob, FakeQueueStore, schedulerOver } from '../fake-crawling-queue'

const JWT_SECRET = 'connectors-http-test-jwt-secret'
const SCOPED_JWT_SECRET = 'connectors-http-test-scoped-secret'

const ORG_A = '64b000000000000000000a01'
const ORG_B = '64b000000000000000000b01'

interface FakeUser {
  _id: string
  orgId: string
  email: string
  fullName: string
  role: 'admin' | 'member'
}

const ADMIN_A: FakeUser = { _id: '64b0000000000000000000a1', orgId: ORG_A, email: 'ada@acme.test', fullName: 'Ada Admin', role: 'admin' }
const MEMBER_A: FakeUser = { _id: '64b0000000000000000000a2', orgId: ORG_A, email: 'max@acme.test', fullName: 'Max Member', role: 'member' }
const OTHER_A: FakeUser = { _id: '64b0000000000000000000a3', orgId: ORG_A, email: 'olga@acme.test', fullName: 'Olga Other', role: 'member' }
const ADMIN_B: FakeUser = { _id: '64b0000000000000000000b1', orgId: ORG_B, email: 'bea@globex.test', fullName: 'Bea Admin', role: 'admin' }
const USERS = [ADMIN_A, MEMBER_A, OTHER_A, ADMIN_B]

interface FakeConnector {
  orgId: string
  scope: 'team' | 'personal'
  createdBy: string
}

/** What the connector service holds; it only ever shows a caller the connectors of their own org. */
const CONNECTORS: Record<string, FakeConnector> = {
  'drive-team': { orgId: ORG_A, scope: 'team', createdBy: ADMIN_A._id },
  'drive-max': { orgId: ORG_A, scope: 'personal', createdBy: MEMBER_A._id },
  'drive-olga': { orgId: ORG_A, scope: 'personal', createdBy: OTHER_A._id },
}

const BASE = '/api/v1/crawlingManager'
const CONNECTOR = 'Google Drive'
const TYPE = encodeURIComponent(CONNECTOR)

const daily = (hour = 2, minute = 30) => ({
  scheduleConfig: { scheduleType: 'daily', isEnabled: true, timezone: 'Asia/Kolkata', hour, minute },
})
const custom = (cronExpression: string, timezone = 'UTC') => ({
  scheduleConfig: { scheduleType: 'custom', isEnabled: true, timezone, cronExpression },
})
const once = (at: Date) => ({
  scheduleConfig: { scheduleType: 'once', isEnabled: true, scheduledTime: at.toISOString() },
})
const interval = (intervalMinutes: number) => ({
  scheduleConfig: { scheduleType: 'interval', isEnabled: true, scheduleConfig: { intervalMinutes, timezone: 'UTC' } },
})

interface Query<T> {
  select(): Query<T>
  lean(): Query<T>
  sort(): Query<T>
  exec(): Promise<T>
  then<R>(onFulfilled: (value: T) => R, onRejected?: (reason: unknown) => R): Promise<R>
}
const query = <T>(value: T): Query<T> => {
  const q: Query<T> = {
    select: () => q,
    lean: () => q,
    sort: () => q,
    exec: async () => value,
    then: (onFulfilled, onRejected) => Promise.resolve(value).then(onFulfilled, onRejected),
  }
  return q
}

describe('Crawling manager over HTTP', () => {
  const backend = new FakeBackend()
  const tokens = new AuthTokenService(JWT_SECRET, SCOPED_JWT_SECRET)
  const oauthGrants = new Map<string, { userId: string; orgId: string; scope: string }>()
  let config: AppConfig
  let server: http.Server
  let origin = ''
  let store: FakeQueueStore
  let scheduler: CrawlingSchedulerService
  let queue: FakeCrawlingQueue

  const orgOfCaller = (call: RecordedCall): string | undefined => {
    const token = String(call.headers.authorization ?? '').replace(/^Bearer /, '')
    const claims = jwt.decode(token) as { orgId?: string } | null
    return claims?.orgId ?? oauthGrants.get(token)?.orgId
  }

  const stubConnectorService = () => {
    for (const [id, connector] of Object.entries(CONNECTORS)) {
      backend.on('GET', `/api/v1/connectors/${id}`, (c) =>
        orgOfCaller(c) === connector.orgId
          ? { status: 200, body: { success: true, connector: { _key: id, ...connector } } }
          : { status: 404, body: { detail: 'Connector not found' } },
      )
    }
  }

  const session = (user: FakeUser): string =>
    tokens.generateToken({
      userId: user._id,
      orgId: user.orgId,
      email: user.email,
      fullName: user.fullName,
      accountType: 'business',
      role: user.role,
    })

  const oauth = (user: FakeUser, scope: string): string => {
    const token = jwt.sign({ tokenType: 'oauth', client_id: 'client-1', iss: 'pipeshub', jti: `${user._id}-${scope}` }, 'unused')
    oauthGrants.set(token, { userId: user._id, orgId: user.orgId, scope })
    return token
  }

  const send = async (method: string, path: string, token?: string, body?: unknown): Promise<ApiResponse> => {
    const headers: Record<string, string> = {}
    if (token) headers.authorization = `Bearer ${token}`
    if (body !== undefined) headers['content-type'] = 'application/json'
    const res = await fetch(`${origin}${BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const text = await res.text()
    return { status: res.status, body: text ? (JSON.parse(text) as Record<string, unknown>) : {} }
  }

  /** Sends the path byte for byte; fetch would resolve dot segments first. */
  const sendRaw = (method: string, rawPath: string, token: string): Promise<ApiResponse> =>
    new Promise((resolve, reject) => {
      const req = http.request(
        { host: '127.0.0.1', port: Number(new URL(origin).port), method, path: `${BASE}${rawPath}`, headers: { authorization: `Bearer ${token}` } },
        (res) => {
          const chunks: Buffer[] = []
          res.on('data', (c: Buffer) => chunks.push(c))
          res.on('end', () => resolve({ status: res.statusCode ?? 0, body: JSON.parse(Buffer.concat(chunks).toString('utf8')) }))
        },
      )
      req.on('error', reject)
      req.end()
    })

  const mountRouter = async (service: CrawlingSchedulerService) => {
    const oauthService = {
      verifyAccessToken: async (token: string) => {
        const grant = oauthGrants.get(token)
        if (!grant) throw new Error('unknown oauth token')
        return { ...grant, client_id: 'client-1', fullName: 'OAuth Caller', accountType: 'business' }
      },
    } as unknown as OAuthTokenService
    const container = new Container()
    container.bind(AuthMiddleware).toConstantValue(new AuthMiddleware(Logger.getInstance(), tokens, () => oauthService))
    container.bind<AppConfig>('AppConfig').toConstantValue(config)
    container.bind(CrawlingSchedulerService).toConstantValue(service)
    const app = express()
    app.use(express.json())
    app.use(BASE, createCrawlingManagerRouter(container))
    app.use(ErrorMiddleware.handleError())
    server = http.createServer(app)
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  }

  const repeatables = async () => queue.getRepeatableJobs()
  const pendingRuns = () => store.pending()

  before(async () => {
    await backend.start()
    config = buildConfig(backend.url)
  })

  after(async () => {
    await backend.stop()
  })

  // Stubbed per test: in a serial run another file's root-level
  // `afterEach(sinon.restore)` would undo a stub made once in `before`.
  beforeEach(async () => {
    sinon.stub(Users, 'findOne').callsFake(((filter: Record<string, unknown>) =>
      query(
        USERS.find(
          (u) =>
            (filter._id === undefined || String(filter._id) === u._id) &&
            (filter.orgId === undefined || String(filter.orgId) === u.orgId),
        ) ?? null,
      )) as unknown as typeof Users.findOne)
    sinon.stub(UserActivities, 'findOne').callsFake((() => query(null)) as unknown as typeof UserActivities.findOne)
    backend.reset()
    stubConnectorService()
    store = new FakeQueueStore()
    ;({ service: scheduler, queue } = schedulerOver(store))
    await mountRouter(scheduler)
  })

  afterEach(async () => {
    sinon.restore()
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
  })

  describe('who may manage a schedule', () => {
    it('turns away a request without a sign-in and queues nothing', async () => {
      const res = await send('POST', `/${TYPE}/drive-team/schedule`, undefined, daily())
      expect(res.status).to.equal(401)
      expect(await repeatables()).to.have.length(0)
    })

    it('lets an app with read access look but not schedule', async () => {
      const readOnly = oauth(ADMIN_A, 'crawl:read')
      const write = await send('POST', `/${TYPE}/drive-team/schedule`, readOnly, daily())
      expect(write.status).to.equal(403)
      expect(await repeatables()).to.have.length(0)

      const read = await send('GET', `/${TYPE}/drive-team/schedule`, readOnly)
      expect(read.status).to.equal(404)
      expect(read.body.message).to.equal('No scheduled job found for this connector')
    })

    it('lets an app with write access schedule for the org it was granted in', async () => {
      const res = await send('POST', `/${TYPE}/drive-team/schedule`, oauth(ADMIN_A, 'crawl:write'), daily())
      expect(res.status).to.equal(201)
      expect(pendingRuns()[0]?.data.orgId).to.equal(ORG_A)
    })

    it('stops a member from scheduling an org-wide connector and leaves the admin schedule alone', async () => {
      expect((await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())).status).to.equal(201)

      for (const [method, path, body] of [
        ['POST', 'schedule', daily(5, 0)],
        ['POST', 'pause', undefined],
        ['DELETE', 'remove', undefined],
      ] as const) {
        const res = await send(method, `/${TYPE}/drive-team/${path}`, session(MEMBER_A), body)
        expect(res.status, `${method} ${path}`).to.equal(403)
        expect(errorMessage(res)).to.equal('You are not authorized to schedule this connector')
      }

      const [schedule] = await repeatables()
      expect(schedule?.pattern).to.equal('30 2 * * *')
      expect(pendingRuns()).to.have.length(1)
    })

    it("stops a member from touching another member's personal connector", async () => {
      expect((await send('POST', `/${TYPE}/drive-olga/schedule`, session(OTHER_A), daily())).status).to.equal(201)
      for (const [method, path] of [['GET', 'schedule'], ['POST', 'pause'], ['POST', 'resume'], ['DELETE', 'remove']] as const) {
        const res = await send(method, `/${TYPE}/drive-olga/${path}`, session(MEMBER_A))
        expect(res.status, `${method} ${path}`).to.equal(403)
      }
      expect(await repeatables()).to.have.length(1)
    })

    it('lets a member schedule their own personal connector', async () => {
      const res = await send('POST', `/${TYPE}/drive-max/schedule`, session(MEMBER_A), daily())
      expect(res.status).to.equal(201)
      expect(res.body.message).to.equal('Crawling job scheduled successfully')
      expect(pendingRuns()[0]?.data.userId).to.equal(MEMBER_A._id)
    })

    it("does not let another org's admin reach a connector, even by its id", async () => {
      expect((await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())).status).to.equal(201)
      for (const [method, path] of [['POST', 'schedule'], ['GET', 'schedule'], ['POST', 'pause'], ['DELETE', 'remove']] as const) {
        const res = await send(method, `/${TYPE}/drive-team/${path}`, session(ADMIN_B), method === 'POST' && path === 'schedule' ? daily() : undefined)
        expect(res.status, `${method} ${path}`).to.equal(404)
      }
      expect(await repeatables()).to.have.length(1)
      expect(pendingRuns()[0]?.data.orgId).to.equal(ORG_A)
    })

    it('takes the org and user from the sign-in, not from the request body', async () => {
      const res = await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), {
        ...daily(),
        orgId: ORG_B,
        userId: ADMIN_B._id,
      })
      expect(res.status).to.equal(201)
      const run = pendingRuns()[0]
      expect(run?.data.orgId).to.equal(ORG_A)
      expect(run?.data.userId).to.equal(ADMIN_A._id)
      expect(run?.id).to.not.include(ORG_B)
    })

    it("lists only the caller's own org's schedules", async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())
      await scheduler.scheduleJob(CONNECTOR, 'globex-drive', daily().scheduleConfig as never, ORG_B, ADMIN_B._id)

      const mine = await send('GET', '/schedule/all', session(ADMIN_B))
      expect(mine.status).to.equal(200)
      const jobs = mine.body.data as Array<{ data: { orgId: string; connectorId: string } }>
      expect(jobs.map((j) => j.data.connectorId)).to.deep.equal(['globex-drive'])
    })

    it("clears only the caller's own org when removing every schedule", async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())
      await scheduler.scheduleJob(CONNECTOR, 'globex-drive', daily(4, 0).scheduleConfig as never, ORG_B, ADMIN_B._id)

      const res = await send('DELETE', '/schedule/all', session(ADMIN_B))
      expect(res.status).to.equal(200)
      expect(pendingRuns().map((j) => j.data.orgId)).to.deep.equal([ORG_A])
      expect((await repeatables()).map((r) => r.pattern)).to.deep.equal(['30 2 * * *'])
    })

    it('does not let a member remove every schedule in the org', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())
      await send('POST', `/${TYPE}/drive-olga/schedule`, session(OTHER_A), daily(6, 0))

      const res = await send('DELETE', '/schedule/all', session(MEMBER_A))
      expect(res.status).to.equal(400)
      expect(errorMessage(res)).to.equal('Admin access required')
      expect(await repeatables()).to.have.length(2)
      expect(pendingRuns()).to.have.length(2)
    })
  })

  describe('creating, changing, pausing and resuming', () => {
    it('creates one repeating schedule with the requested time and timezone', async () => {
      const res = await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())
      expect(res.status).to.equal(201)
      expect(res.body.data).to.include({ connector: CONNECTOR, connectorId: 'drive-team' })

      const [schedule, ...rest] = await repeatables()
      expect(rest).to.have.length(0)
      expect(schedule).to.include({ pattern: '30 2 * * *', tz: 'Asia/Kolkata' })

      const status = await send('GET', `/${TYPE}/drive-team/schedule`, session(ADMIN_A))
      expect(status.status).to.equal(200)
      const data = status.body.data as { state: string; data: { scheduleConfig: { hour: number } } }
      expect(data.state).to.equal('delayed')
      expect(data.data.scheduleConfig.hour).to.equal(2)
    })

    it('replaces the old schedule when it is changed, leaving one', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())
      const res = await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), custom('0 */6 * * *'))
      expect(res.status).to.equal(201)

      expect((await repeatables()).map((r) => r.pattern)).to.deep.equal(['0 */6 * * *'])
      expect(pendingRuns()).to.have.length(1)
    })

    it('does not add a second copy when a restarted service schedules the same connector again', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), interval(30))

      const { service: restarted } = schedulerOver(store)
      await restarted.scheduleJob(CONNECTOR, 'drive-team', interval(30).scheduleConfig as never, ORG_A, ADMIN_A._id)

      const all = await repeatables()
      expect(all).to.have.length(1)
      expect(all[0]?.every).to.equal(String(30 * 60 * 1000))
      expect(pendingRuns()).to.have.length(1)
    })

    it('pauses without anything left to fire, and resumes with the same schedule', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())

      const paused = await send('POST', `/${TYPE}/drive-team/pause`, session(ADMIN_A))
      expect(paused.status).to.equal(200)
      expect(await repeatables()).to.have.length(0)
      expect(pendingRuns()).to.have.length(0)
      const status = await send('GET', `/${TYPE}/drive-team/schedule`, session(ADMIN_A))
      expect((status.body.data as { state: string }).state).to.equal('paused')

      const again = await send('POST', `/${TYPE}/drive-team/pause`, session(ADMIN_A))
      expect(again.status).to.equal(400)
      expect(errorMessage(again)).to.equal('Job is already paused')

      const resumed = await send('POST', `/${TYPE}/drive-team/resume`, session(ADMIN_A))
      expect(resumed.status).to.equal(200)
      expect((await repeatables()).map((r) => r.pattern)).to.deep.equal(['30 2 * * *'])
      expect(pendingRuns()).to.have.length(1)

      const twice = await send('POST', `/${TYPE}/drive-team/resume`, session(ADMIN_A))
      expect(twice.status).to.equal(400)
      expect(errorMessage(twice)).to.equal('No paused job found to resume')
    })

    it('turns a schedule off when it is sent disabled, and says nothing new was created', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())
      const off = daily()
      off.scheduleConfig.isEnabled = false
      const res = await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), off)
      expect(res.status).to.equal(400)
      expect(errorMessage(res)).to.equal('Cannot schedule a disabled job')
      expect(await repeatables()).to.have.length(0)
      expect(pendingRuns()).to.have.length(0)
    })

    it('says there is nothing to pause when no schedule exists', async () => {
      const res = await send('POST', `/${TYPE}/drive-team/pause`, session(ADMIN_A))
      expect(res.status).to.equal(400)
      expect(errorMessage(res)).to.equal('No active job found to pause')
    })

    it('removes a schedule so nothing is left to fire', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())
      const res = await send('DELETE', `/${TYPE}/drive-team/remove`, session(ADMIN_A))
      expect(res.status).to.equal(200)
      expect(await repeatables()).to.have.length(0)
      expect(pendingRuns()).to.have.length(0)
      expect((await send('GET', `/${TYPE}/drive-team/schedule`, session(ADMIN_A))).status).to.equal(404)
    })
  })

  describe('checking a schedule before anything changes', () => {
    const expectDailyKept = async () => {
      expect((await repeatables()).map((r) => r.pattern)).to.deep.equal(['30 2 * * *'])
      expect(pendingRuns()).to.have.length(1)
    }

    beforeEach(async () => {
      expect((await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())).status).to.equal(201)
    })

    it('refuses a cron expression without five fields', async () => {
      const res = await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), custom('0 2 * *'))
      expect(res.status).to.equal(400)
      expect(errorMessage(res)).to.include('Invalid cron expression format')
      await expectDailyKept()
    })

    for (const [what, body] of [
      ['a cron minute out of range', custom('61 * * * *')],
      ['a cron field that is not a number', custom('0 banana * * *')],
      ['an unknown timezone', custom('0 2 * * *', 'Mars/Olympus_Mons')],
    ] as const) {
      it(`refuses ${what} and keeps the schedule already there`, async () => {
        const res = await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), body)
        expect(res.status).to.equal(400)
        expect(errorMessage(res)).to.match(/^This schedule can't be used/)
        expect(JSON.stringify(res.body)).to.not.match(/stack|cron-parser|at \w+ \(/)
        await expectDailyKept()
      })
    }

    it('refuses a one-time run in the past and keeps the schedule already there', async () => {
      const res = await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(new Date(Date.now() - 60_000)))
      expect(res.status).to.equal(400)
      expect(errorMessage(res)).to.equal('Scheduled time must be in the future')
      await expectDailyKept()
    })

    for (const minutes of [0, 1.5, 60 * 24 * 365 + 1]) {
      it(`refuses an interval of ${minutes} minutes`, async () => {
        const res = await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), interval(minutes))
        expect(res.status).to.equal(400)
        await expectDailyKept()
      })
    }

    it('refuses an hour of 24', async () => {
      const res = await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily(24, 0))
      expect(res.status).to.equal(400)
      expect(errorMessage(res)).to.not.include('ZodError')
      await expectDailyKept()
    })
  })

  describe('one-time runs', () => {
    it('schedules a single run at the requested time', async () => {
      const at = new Date(Date.now() + 60 * 60 * 1000)
      const res = await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(at))
      expect(res.status).to.equal(201)
      const [run, ...rest] = pendingRuns()
      expect(rest).to.have.length(0)
      expect(run?.state).to.equal('delayed')
      expect(Math.abs((run?.runAt ?? 0) - at.getTime())).to.be.lessThan(1000)
      expect(await repeatables()).to.have.length(0)
    })

    it('does not fire a one-time run after it is removed', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(new Date(Date.now() + 60_000)))
      expect((await send('DELETE', `/${TYPE}/drive-team/remove`, session(ADMIN_A))).status).to.equal(200)
      expect(pendingRuns()).to.have.length(0)
    })

    it('does not fire a one-time run while it is paused', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(new Date(Date.now() + 60_000)))
      expect((await send('POST', `/${TYPE}/drive-team/pause`, session(ADMIN_A))).status).to.equal(200)
      expect(pendingRuns()).to.have.length(0)
    })

    it('moves a one-time run when it is scheduled again for a different time', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(new Date(Date.now() + 60_000)))
      const later = new Date(Date.now() + 3 * 60 * 60 * 1000)
      expect((await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(later))).status).to.equal(201)
      const runs = pendingRuns()
      expect(runs).to.have.length(1)
      expect(Math.abs((runs[0]?.runAt ?? 0) - later.getTime())).to.be.lessThan(1000)
    })

    it('queues a new one-time run after the previous one has finished', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(new Date(Date.now() + 60_000)))
      const [first] = pendingRuns()
      await queue.runDue(async () => {}, first!.runAt)
      expect(first?.state).to.equal('completed')

      const next = new Date(Date.now() + 2 * 60 * 60 * 1000)
      expect((await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(next))).status).to.equal(201)
      const runs = pendingRuns()
      expect(runs).to.have.length(1)
      expect(runs[0]?.state).to.equal('delayed')
      expect(Math.abs((runs[0]?.runAt ?? 0) - next.getTime())).to.be.lessThan(1000)
    })

    it('moves a one-time run that is due but not yet picked up', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(new Date(Date.now() + 60_000)))
      const [first] = pendingRuns()
      queue.promoteDue(first!.runAt)
      expect(first?.state).to.equal('prioritized')

      const next = new Date(Date.now() + 2 * 60 * 60 * 1000)
      expect((await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(next))).status).to.equal(201)
      const runs = pendingRuns()
      expect(runs).to.have.length(1)
      expect(runs[0]?.state).to.equal('delayed')
      expect(Math.abs((runs[0]?.runAt ?? 0) - next.getTime())).to.be.lessThan(1000)
    })

    it('pauses a one-time run that is due but not yet picked up', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(new Date(Date.now() + 60_000)))
      const [first] = pendingRuns()
      queue.promoteDue(first!.runAt)

      expect((await send('POST', `/${TYPE}/drive-team/pause`, session(ADMIN_A))).status).to.equal(200)
      expect(pendingRuns()).to.have.length(0)
    })
  })

  describe('when a run is due', () => {
    const published: Array<{ eventType: string; payload: Record<string, unknown> }> = []
    let publishFails: Error | null = null
    let worker: CrawlingWorkerService

    beforeEach(() => {
      published.length = 0
      publishFails = null
      const producer = {
        publishEvent: async (event: { eventType: string; payload: Record<string, unknown> }) => {
          if (publishFails) throw publishFails
          published.push(event)
        },
      } as unknown as SyncEventProducer
      // Built without its constructor, which would open a BullMQ worker on Redis.
      worker = Object.assign(Object.create(CrawlingWorkerService.prototype) as CrawlingWorkerService, {
        logger: Logger.getInstance({ service: 'CrawlingWorkerService' }),
        taskService: new ConnectorsCrawlingService(producer),
      })
    })

    const process = (job: FakeJob) => (worker as unknown as { processJob(j: FakeJob): Promise<void> }).processJob(job)

    it('asks for a sync of that connector in that org, and queues the next run', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), interval(15))
      const [first] = pendingRuns()
      await queue.runDue(process, first!.runAt)

      expect(published).to.have.length(1)
      expect(published[0]?.payload).to.include({ orgId: ORG_A, connectorId: 'drive-team' })
      expect(first?.state).to.equal('completed')
      const next = pendingRuns()
      expect(next).to.have.length(1)
      expect(next[0]!.runAt - first!.runAt).to.equal(15 * 60 * 1000)
    })

    it('records a run that could not start the sync as failed, after its retries', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), {
        ...once(new Date(Date.now() + 60_000)),
        maxRetries: 2,
      })
      publishFails = new Error('broker unreachable')
      const [run] = pendingRuns()
      await queue.runDue(process, run!.runAt)

      expect(run?.state).to.equal('failed')
      expect(run?.attemptsMade).to.equal(2)
      const status = await send('GET', `/${TYPE}/drive-team/schedule`, session(ADMIN_A))
      expect((status.body.data as { state: string }).state).to.equal('failed')
    })
  })

  describe('every schedule in the org', () => {
    // A due run with a priority waits in `prioritized` until a worker takes it.
    const scheduleBothAndPromote = async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())
      await scheduler.scheduleJob(CONNECTOR, 'drive-max', once(new Date(Date.now() + 60_000)).scheduleConfig as never, ORG_A, MEMBER_A._id)
      queue.promoteDue(Date.now() + 2 * 24 * 60 * 60 * 1000)
      expect(pendingRuns().map((j) => j.state)).to.deep.equal(['prioritized', 'prioritized'])
    }

    it('lists a run that is due but not yet picked up', async () => {
      await scheduleBothAndPromote()
      const res = await send('GET', '/schedule/all', session(ADMIN_A))
      expect(res.status).to.equal(200)
      const jobs = res.body.data as Array<{ data: { connectorId: string } }>
      expect(jobs.map((j) => j.data.connectorId).sort()).to.deep.equal(['drive-max', 'drive-team'])
    })

    it('removes schedules and runs that are due but not yet picked up', async () => {
      await scheduleBothAndPromote()
      const res = await send('DELETE', '/schedule/all', session(ADMIN_A))
      expect(res.status).to.equal(200)
      expect(await repeatables()).to.have.length(0)
      expect(pendingRuns()).to.have.length(0)
    })

    it('says so when a queued run cannot be cancelled', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(new Date(Date.now() + 60_000)))
      const [run] = pendingRuns()
      sinon.stub(run!, 'remove').rejects(new Error('connect ETIMEDOUT 10.0.3.7:6379'))

      const res = await send('DELETE', '/schedule/all', session(ADMIN_A))
      expect(res.status).to.equal(500)
      expect(errorMessage(res)).to.include('could not be removed')
      expect(errorMessage(res)).to.not.include('10.0.3.7')
      expect(pendingRuns()).to.deep.equal([run])
    })

    it('says so when a repeating schedule cannot be removed', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())
      sinon.stub(queue, 'removeRepeatable').rejects(new Error('CLUSTERDOWN The cluster is down'))

      const res = await send('DELETE', '/schedule/all', session(ADMIN_A))
      expect(res.status).to.equal(500)
      expect(errorMessage(res)).to.include('could not be removed')
      expect(errorMessage(res)).to.not.include('CLUSTERDOWN')
      expect(await repeatables()).to.have.length(1)
      expect(pendingRuns()).to.have.length(1)
    })

    it('still succeeds when a run a worker is processing cannot be removed', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(new Date(Date.now() + 60_000)))
      const [run] = pendingRuns()
      run!.state = 'active'

      const res = await send('DELETE', '/schedule/all', session(ADMIN_A))
      expect(res.status).to.equal(200)
    })
  })

  describe('when something goes wrong', () => {
    // The state reads fine until a cancel has failed, as when Redis drops mid-request.
    const stateUnreadableAfterFailedRemove = (job: FakeJob) => {
      let removeFailed = false
      const readState = job.getState.bind(job)
      sinon.stub(job, 'remove').callsFake(async () => {
        removeFailed = true
        throw new Error('connect ETIMEDOUT 10.0.3.7:6379')
      })
      sinon.stub(job, 'getState').callsFake(async () => {
        if (removeFailed) throw new Error('connect ETIMEDOUT 10.0.3.7:6379')
        return readState()
      })
      return { reset: () => { removeFailed = false } }
    }

    it('reports a removal that could not reach the queue instead of claiming success', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())
      store.failWith = new Error('connect ECONNREFUSED 10.0.3.7:6379')

      const res = await send('DELETE', `/${TYPE}/drive-team/remove`, session(ADMIN_A))
      expect(res.status).to.equal(500)
      expect(errorMessage(res)).to.not.include('ECONNREFUSED')
      expect(errorMessage(res)).to.not.include('10.0.3.7')
      expect(JSON.stringify(res.body)).to.not.include('stack')
    })

    it('keeps a schedule running when pausing it could not reach the queue', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())
      const realGetRepeatable = queue.getRepeatableJobs.bind(queue)
      sinon.stub(queue, 'getRepeatableJobs').onFirstCall().rejects(new Error('READONLY replica')).callsFake(realGetRepeatable)

      const res = await send('POST', `/${TYPE}/drive-team/pause`, session(ADMIN_A))
      expect(res.status).to.equal(500)
      expect(errorMessage(res)).to.not.include('READONLY')
      expect(scheduler.getPausedJobs().size).to.equal(0)
      const status = await send('GET', `/${TYPE}/drive-team/schedule`, session(ADMIN_A))
      expect((status.body.data as { state: string }).state).to.equal('delayed')
    })

    it('says so when a queued run cannot be cancelled, and leaves it queued', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(new Date(Date.now() + 60_000)))
      const [run] = pendingRuns()
      sinon.stub(run!, 'remove').rejects(new Error("READONLY You can't write against a read only replica."))

      for (const [method, path] of [['POST', 'pause'], ['DELETE', 'remove']] as const) {
        const res = await send(method, `/${TYPE}/drive-team/${path}`, session(ADMIN_A))
        expect(res.status, `${method} ${path}`).to.equal(500)
        expect(errorMessage(res)).to.include('could not be cancelled')
        expect(errorMessage(res)).to.not.include('READONLY')
      }
      expect(scheduler.getPausedJobs().size).to.equal(0)
      expect(pendingRuns()).to.deep.equal([run])
      expect(run?.state).to.equal('delayed')
    })

    it('treats a run whose state cannot be read after a failed cancel as still queued', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(new Date(Date.now() + 60_000)))
      const [run] = pendingRuns()
      const cannotReadState = stateUnreadableAfterFailedRemove(run!)

      for (const [method, path] of [['POST', 'pause'], ['DELETE', 'remove']] as const) {
        cannotReadState.reset()
        const res = await send(method, `/${TYPE}/drive-team/${path}`, session(ADMIN_A))
        expect(res.status, `${method} ${path}`).to.equal(500)
        expect(errorMessage(res)).to.include('could not be cancelled')
      }
      expect(scheduler.getPausedJobs().size).to.equal(0)
      expect(pendingRuns()).to.deep.equal([run])
    })

    it('keeps a repeating schedule and says so when it cannot be removed', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())
      sinon.stub(queue, 'removeRepeatable').rejects(new Error('CLUSTERDOWN The cluster is down'))

      for (const [method, path] of [['POST', 'pause'], ['DELETE', 'remove']] as const) {
        const res = await send(method, `/${TYPE}/drive-team/${path}`, session(ADMIN_A))
        expect(res.status, `${method} ${path}`).to.equal(500)
        expect(errorMessage(res)).to.include('schedule could not be removed')
        expect(errorMessage(res)).to.not.include('CLUSTERDOWN')
      }
      expect(scheduler.getPausedJobs().size).to.equal(0)
      expect(await repeatables()).to.have.length(1)
      expect(pendingRuns()).to.have.length(1)
    })

    it('pauses when the queued run was picked up before it could be cancelled', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(new Date(Date.now() + 60_000)))
      const [run] = pendingRuns()
      sinon.stub(run!, 'remove').callsFake(async () => {
        run!.state = 'active'
        throw new Error(`Job ${run!.id} could not be removed because it is locked by another worker`)
      })

      expect((await send('POST', `/${TYPE}/drive-team/pause`, session(ADMIN_A))).status).to.equal(200)
      expect(scheduler.getPausedJobs().size).to.equal(1)
    })

    it('says so when the finished one-time run cannot be cleared, and queues nothing', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(new Date(Date.now() + 60_000)))
      const [first] = pendingRuns()
      await queue.runDue(async () => {}, first!.runAt)
      sinon.stub(first!, 'remove').rejects(new Error('connect ETIMEDOUT 10.0.3.7:6379'))

      const res = await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(new Date(Date.now() + 2 * 60 * 60 * 1000)))
      expect(res.status).to.equal(500)
      expect(errorMessage(res)).to.include("a new one can't be scheduled yet")
      expect(errorMessage(res)).to.not.include('10.0.3.7')
      expect(pendingRuns()).to.have.length(0)
    })

    it('does not treat a finished one-time run as cleared when its state cannot be read', async () => {
      await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(new Date(Date.now() + 60_000)))
      const [first] = pendingRuns()
      await queue.runDue(async () => {}, first!.runAt)
      stateUnreadableAfterFailedRemove(first!)

      const res = await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), once(new Date(Date.now() + 2 * 60 * 60 * 1000)))
      expect(res.status).to.equal(500)
      expect(errorMessage(res)).to.include("a new one can't be scheduled yet")
    })

    it('hides what the connector service said when it failed', async () => {
      backend.on('GET', '/api/v1/connectors/drive-team', {
        status: 500,
        body: { detail: 'arangodb: connection refused at arango-0.internal:8529' },
      })
      const res = await send('POST', `/${TYPE}/drive-team/schedule`, session(ADMIN_A), daily())
      expect(res.status).to.equal(500)
      expect(errorMessage(res)).to.not.include('arango')
      expect(await repeatables()).to.have.length(0)
    })
  })

  describe('path parameters', () => {
    const unsafe = ['..', '.', '%2E%2E', 'a%2Fb', 'a%5Cb', 'a%3Fb', 'a%23b', 'a%25b', '%20..%20']

    for (const segment of unsafe) {
      it(`refuses "${segment}" as a connector id before calling anything`, async () => {
        for (const [method, path] of [['POST', 'pause'], ['GET', 'schedule'], ['DELETE', 'remove'], ['POST', 'resume']] as const) {
          const res = await sendRaw(method, `/${TYPE}/${segment}/${path}`, session(ADMIN_A))
          expect(res.status, `${method} ${path}`).to.equal(400)
          expect(errorMessage(res)).to.equal(INVALID_PATH_SEGMENT_MESSAGE)
        }
        expect(backend.calls).to.have.length(0)
      })

      it(`refuses "${segment}" as a connector type before calling anything`, async () => {
        const res = await sendRaw('GET', `/${segment}/drive-team/schedule`, session(ADMIN_A))
        expect(res.status).to.equal(400)
        expect(errorMessage(res)).to.equal(INVALID_PATH_SEGMENT_MESSAGE)
        expect(backend.calls).to.have.length(0)
      })
    }

    it('still accepts a connector type with a space in it', async () => {
      const res = await sendRaw('GET', `/SHAREPOINT%20ONLINE/drive-team/schedule`, session(ADMIN_A))
      expect(res.status).to.equal(404)
      expect(backend.callsTo('GET', '/api/v1/connectors/drive-team')).to.have.length(1)
    })
  })
})
