import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import axios from 'axios'

import { AuthTokenService } from '../../../../src/libs/services/authtoken.service'
import { Logger } from '../../../../src/libs/services/logger.service'

const SERVICE_MODULE_PATH = '../../../../src/modules/enterprise_search/services/agent_schedule.service'
const BULLMQ_MODULE_PATH = require.resolve('bullmq')

describe('enterprise_search/services/agent_schedule.service', () => {
  const createAppConfig = () => ({
    jwtSecret: 'jwt-secret',
    scopedJwtSecret: 'scoped-secret',
    esBackend: 'http://localhost:8000',
    redis: {
      host: 'localhost',
      port: 6379,
      username: 'default',
      password: 'secret',
      db: 3,
    },
  })

  let queueMock: any
  let workerMock: any
  let loggerMock: any
  let workerHandlers: Record<string, (...args: any[]) => void>
  let savedBullmq: NodeModule | undefined

  const loadService = () => {
    const mockedBullmq = {
      Queue: function MockQueue() {
        return queueMock
      },
      Worker: function MockWorker() {
        return workerMock
      },
    }

    savedBullmq = require.cache[BULLMQ_MODULE_PATH]
    require.cache[BULLMQ_MODULE_PATH] = {
      ...(savedBullmq || {}),
      id: BULLMQ_MODULE_PATH,
      filename: BULLMQ_MODULE_PATH,
      loaded: true,
      exports: mockedBullmq,
      children: savedBullmq?.children || [],
      paths: savedBullmq?.paths || [],
      parent: savedBullmq?.parent || null,
    } as any

    const resolvedPath = require.resolve(SERVICE_MODULE_PATH)
    delete require.cache[resolvedPath]

    return require(SERVICE_MODULE_PATH).AgentScheduleService as typeof import('../../../../src/modules/enterprise_search/services/agent_schedule.service').AgentScheduleService
  }

  beforeEach(() => {
    workerHandlers = {}
    queueMock = {
      add: sinon.stub().resolves(),
      getRepeatableJobs: sinon.stub().resolves([]),
      removeRepeatableByKey: sinon.stub().resolves(),
      getJobs: sinon.stub().resolves([]),
      close: sinon.stub().resolves(),
    }
    workerMock = {
      on: sinon.stub().callsFake((event: string, handler: (...args: any[]) => void) => {
        workerHandlers[event] = handler
        return workerMock
      }),
      close: sinon.stub().resolves(),
    }
    loggerMock = {
      info: sinon.stub(),
      error: sinon.stub(),
    }

    sinon.stub(Logger, 'getInstance').returns(loggerMock as any)
  })

  afterEach(() => {
    if (savedBullmq) {
      require.cache[BULLMQ_MODULE_PATH] = savedBullmq
    } else {
      delete require.cache[BULLMQ_MODULE_PATH]
    }
    delete require.cache[require.resolve(SERVICE_MODULE_PATH)]
    sinon.restore()
  })

  it('should post a scheduled trigger and wait for the stream to finish', async () => {
    const AgentScheduleService = loadService()
    sinon.stub(AuthTokenService.prototype, 'generateScopedToken').returns('scoped-token')
    const streamHandlers: Record<string, (...args: any[]) => void> = {}
    const stream = {
      on: sinon.stub().callsFake((event: string, handler: (...args: any[]) => void) => {
        streamHandlers[event] = handler
        return stream
      }),
      resume: sinon.stub(),
    }
    const axiosPost = sinon.stub(axios, 'post').resolves({ data: stream } as any)

    const service = new AgentScheduleService(createAppConfig() as any)

    const pendingTrigger = (service as any).runScheduledTrigger({
      agentKey: 'agent-1',
      triggerId: 'trigger-1',
      orgId: 'org-1',
      userId: 'user-1',
      email: 'user@test.com',
      input: 'Run the workflow',
      timezone: 'UTC',
    })

    await Promise.resolve()
    streamHandlers.end!()

    await pendingTrigger

    expect(axiosPost.calledOnce).to.be.true
    expect(stream.resume.calledOnce).to.be.true
    expect(axiosPost.firstCall.args[0]).to.include('/api/v1/agents/agent-1/conversations/internal/stream')
    expect(axiosPost.firstCall.args[1]).to.deep.equal({
      query: 'Run the workflow',
      timezone: 'UTC',
      chatMode: 'auto',
    })
    expect(axiosPost.firstCall.args[2]!.headers.Authorization).to.equal('Bearer scoped-token')
  })

  it('should reject when the scheduled trigger stream emits an error', async () => {
    const AgentScheduleService = loadService()
    sinon.stub(AuthTokenService.prototype, 'generateScopedToken').returns('scoped-token')
    const streamHandlers: Record<string, (...args: any[]) => void> = {}
    const stream = {
      on: sinon.stub().callsFake((event: string, handler: (...args: any[]) => void) => {
        streamHandlers[event] = handler
        return stream
      }),
      resume: sinon.stub(),
    }
    sinon.stub(axios, 'post').resolves({ data: stream } as any)

    const service = new AgentScheduleService(createAppConfig() as any)

    const pendingTrigger = (service as any).runScheduledTrigger({
      agentKey: 'agent-1',
      triggerId: 'trigger-1',
      orgId: 'org-1',
      userId: 'user-1',
      email: 'user@test.com',
      input: 'Run the workflow',
      timezone: 'UTC',
    })

    await Promise.resolve()
    streamHandlers.error!(new Error('stream failed'))

    let caughtError: Error | null = null
    try {
      await pendingTrigger
    } catch (error: any) {
      caughtError = error
    }

    expect(caughtError?.message).to.equal('stream failed')
  })

  it('should sync repeatable jobs extracted from the flow', async () => {
    const AgentScheduleService = loadService()
    const service = new AgentScheduleService(createAppConfig() as any)

    await service.syncAgentScheduleFromFlow(
      'agent-1',
      {
        nodes: [
          {
            id: 'sched-1',
            data: {
              type: 'scheduled-input',
              config: {
                enabled: true,
                cronExpression: '0 8 * * 1-5',
                timezone: 'Europe/Berlin',
                input: 'Daily summary',
              },
            },
          },
          { id: 'agent-1', data: { type: 'agent-core', config: {} } },
        ],
        edges: [
          {
            source: 'sched-1',
            target: 'agent-1',
            sourceHandle: 'message',
            targetHandle: 'input',
          },
        ],
      },
      { orgId: 'org-1', userId: 'user-1', email: 'user@test.com' },
    )

    expect(queueMock.add.calledOnce).to.be.true
    expect(queueMock.add.firstCall.args[0]).to.equal('agent-schedule:agent-1:sched-1')
    expect(queueMock.add.firstCall.args[1]).to.deep.equal({
      agentKey: 'agent-1',
      triggerId: 'sched-1',
      orgId: 'org-1',
      userId: 'user-1',
      email: 'user@test.com',
      input: 'Daily summary',
      timezone: 'Europe/Berlin',
    })
    expect(queueMock.add.firstCall.args[2]).to.deep.equal({
      jobId: 'agent-schedule-agent-1-sched-1-org-1',
      repeat: {
        pattern: '0 8 * * 1-5',
        tz: 'Europe/Berlin',
      },
    })
    expect(loggerMock.info.calledWith('Synchronized agent schedules', sinon.match({
      agentKey: 'agent-1',
      triggerCount: 1,
      ownerOrgId: 'org-1',
    }))).to.be.true
  })

  it('should remove repeatable and queued jobs for an agent', async () => {
    const AgentScheduleService = loadService()
    const removableJob = {
      data: { agentKey: 'agent-1' },
      remove: sinon.stub().resolves(),
    }
    const otherJob = {
      data: { agentKey: 'agent-2' },
      remove: sinon.stub().resolves(),
    }
    queueMock.getRepeatableJobs.resolves([
      { key: 'repeat-1', name: 'agent-schedule:agent-1:sched-1' },
      { key: 'repeat-2', name: 'agent-schedule:agent-2:sched-2' },
    ])
    queueMock.getJobs.resolves([removableJob, otherJob])

    const service = new AgentScheduleService(createAppConfig() as any)

    await service.removeSchedulesForAgent('agent-1')

    expect(queueMock.removeRepeatableByKey.calledOnceWithExactly('repeat-1')).to.be.true
    expect(removableJob.remove.calledOnce).to.be.true
    expect(otherJob.remove.called).to.be.false
  })

  it('should skip syncing and removing schedules when agent key is missing', async () => {
    const AgentScheduleService = loadService()
    const service = new AgentScheduleService(createAppConfig() as any)

    await service.syncAgentScheduleFromFlow('', { nodes: [], edges: [] }, {
      orgId: 'org-1',
      userId: 'user-1',
      email: 'user@test.com',
    })
    await service.removeSchedulesForAgent('')

    expect(queueMock.add.called).to.be.false
    expect(queueMock.getRepeatableJobs.called).to.be.false
  })

  it('should log worker completion and failure events and shut down cleanly', async () => {
    const AgentScheduleService = loadService()
    const service = new AgentScheduleService(createAppConfig() as any)

    workerHandlers.failed?.({
      id: 'job-1',
      data: { agentKey: 'agent-1', triggerId: 'sched-1' },
    } as any, new Error('job failed'))
    workerHandlers.completed?.({
      id: 'job-2',
      data: { agentKey: 'agent-1', triggerId: 'sched-1' },
    } as any)

    await service.shutdown()

    expect(loggerMock.error.calledWith('Scheduled agent trigger failed', sinon.match({
      jobId: 'job-1',
      agentKey: 'agent-1',
      triggerId: 'sched-1',
      error: 'job failed',
    }))).to.be.true
    expect(loggerMock.info.calledWith('Scheduled agent trigger completed', sinon.match({
      jobId: 'job-2',
      agentKey: 'agent-1',
      triggerId: 'sched-1',
    }))).to.be.true
    expect(workerMock.close.calledOnce).to.be.true
    expect(queueMock.close.calledOnce).to.be.true
  })
})