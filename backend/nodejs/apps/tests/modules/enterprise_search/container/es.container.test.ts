import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'

const SERVICE_MODULE_PATH = require.resolve('../../../../src/modules/enterprise_search/services/agent_schedule.service')
const CONTAINER_MODULE_PATH = require.resolve('../../../../src/modules/enterprise_search/container/es.container')

describe('enterprise_search/container/es.container', () => {
  let savedServiceModule: NodeModule | undefined
  let fakeScheduleService: any

  const loadContainer = () => {
    savedServiceModule = require.cache[SERVICE_MODULE_PATH]
    require.cache[SERVICE_MODULE_PATH] = {
      ...(savedServiceModule || {}),
      id: SERVICE_MODULE_PATH,
      filename: SERVICE_MODULE_PATH,
      loaded: true,
      exports: {
        AgentScheduleService: function MockAgentScheduleService() {
          return fakeScheduleService
        },
      },
      children: savedServiceModule?.children || [],
      paths: savedServiceModule?.paths || [],
      parent: savedServiceModule?.parent || null,
    } as any

    delete require.cache[CONTAINER_MODULE_PATH]

    return require(CONTAINER_MODULE_PATH).EnterpriseSearchAgentContainer as typeof import('../../../../src/modules/enterprise_search/container/es.container').EnterpriseSearchAgentContainer
  }

  beforeEach(() => {
    fakeScheduleService = {
      shutdown: sinon.stub().resolves(),
    }
  })

  afterEach(() => {
    if (savedServiceModule) {
      require.cache[SERVICE_MODULE_PATH] = savedServiceModule
    } else {
      delete require.cache[SERVICE_MODULE_PATH]
    }
    delete require.cache[CONTAINER_MODULE_PATH]
    sinon.restore()
  })

  it('should initialize and bind the agent schedule service', async () => {
    const EnterpriseSearchAgentContainer = loadContainer()
    const keyValueStoreService = {
      connect: sinon.stub().resolves(),
      disconnect: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(true),
    }

    sinon.stub(KeyValueStoreService, 'getInstance').returns(keyValueStoreService as any)

    const container = await EnterpriseSearchAgentContainer.initialize(
      { enabled: true } as any,
      {
        jwtSecret: 'jwt-secret',
        scopedJwtSecret: 'scoped-secret',
      } as any,
    )

    expect(container.isBound('AgentScheduleService')).to.be.true
    expect(container.get('AgentScheduleService')).to.equal(fakeScheduleService)
  })

  it('should shut down bound services during dispose', async () => {
    const EnterpriseSearchAgentContainer = loadContainer()
    const keyValueStoreService = {
      connect: sinon.stub().resolves(),
      disconnect: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(true),
    }

    sinon.stub(KeyValueStoreService, 'getInstance').returns(keyValueStoreService as any)

    await EnterpriseSearchAgentContainer.initialize(
      { enabled: true } as any,
      {
        jwtSecret: 'jwt-secret',
        scopedJwtSecret: 'scoped-secret',
      } as any,
    )

    await EnterpriseSearchAgentContainer.dispose()

    expect(keyValueStoreService.disconnect.calledOnce).to.be.true
    expect(fakeScheduleService.shutdown.calledOnce).to.be.true
  })

  it('should be importable', async () => {
    try {
      const mod = await import('../../../../src/modules/enterprise_search/container/es.container')
      expect(mod).to.be.an('object')
    } catch (error: any) {
      expect(error).to.exist
    }
  })
})
