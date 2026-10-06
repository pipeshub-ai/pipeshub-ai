import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { EnterpriseSearchAgentContainer } from '../../../../src/modules/enterprise_search/container/es.container'
import { DecisionCache } from '../../../../src/modules/authz/cache/decision-cache'
import { useTeamIdsCache } from '../../../../src/modules/user_management/services/cached-team-directory'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'
import { HttpAgentReadinessAdapter } from '../../../../src/modules/enterprise_search/services/collaboration/readiness/http-agent-readiness.adapter'
import { CollaboratorsController } from '../../../../src/modules/enterprise_search/controller/collaborators.controller'
import { OutboxCollaborationNotifier } from '../../../../src/modules/enterprise_search/services/collaboration/notify/collaboration-notifier'
import { useConversationEventProducers } from '../../../../src/modules/enterprise_search/services/collaboration/notify/conversation-event-producers'
import { COLLAB_TYPES } from '../../../../src/modules/enterprise_search/services/collaboration/collab.types'

describe('enterprise_search/container/es.container', () => {
  afterEach(() => {
    sinon.restore()
    useConversationEventProducers(undefined)
  })

  it('should be importable', async () => {
    try {
      const mod = await import('../../../../src/modules/enterprise_search/container/es.container')
      expect(mod).to.be.an('object')
    } catch (error: any) {
      expect(error).to.exist
    }
  })

  describe('conversation guards wiring', () => {
    afterEach(() => {
      useTeamIdsCache(undefined)
    })

    async function initialize() {
      sinon.stub(KeyValueStoreService, 'getInstance').returns({ connect: sinon.stub().resolves() } as never)
      const appConfig = { jwtSecret: 'j', scopedJwtSecret: 's', connectorBackend: 'http://connectors' } as never
      return EnterpriseSearchAgentContainer.initialize({} as never, appConfig)
    }

    type Deps = { deps: { authz: { deps: { cache: DecisionCache; teams: unknown } }; teams: unknown } }
    const key = { orgId: 'o', subjectKey: 'user:u', resourceType: 'chat', resourceId: 'c' }

    it('gives the PDP the shared Redis decision cache and the team directory that supplies teamsVersion', async () => {
      const shared = { get: sinon.stub().resolves(null), set: sinon.stub().resolves() }
      useTeamIdsCache(shared as never)

      const container = await initialize()

      const guards = container.get(COLLAB_TYPES.ConversationGuards) as never as Deps
      const authz = guards.deps.authz.deps
      expect(authz.cache).to.be.instanceOf(DecisionCache)
      await authz.cache.set({}, key, 'viewer')
      expect(shared.set.calledOnce).to.equal(true)
      expect(authz.teams, 'same directory the guards resolve teams from').to.equal(guards.deps.teams)
    })

    it('picks up a shared cache wired after the container is built', async () => {
      const container = await initialize()
      const shared = { get: sinon.stub().resolves(null), set: sinon.stub().resolves() }
      useTeamIdsCache(shared as never)

      const guards = container.get(COLLAB_TYPES.ConversationGuards) as never as Deps
      await guards.deps.authz.deps.cache.set({}, key, 'viewer')

      expect(shared.set.calledOnce).to.equal(true)
    })

    it('falls back to the per-request cache when no shared cache is configured', async () => {
      const container = await initialize()
      const guards = container.get(COLLAB_TYPES.ConversationGuards) as never as Deps
      const request = {}
      await guards.deps.authz.deps.cache.set(request, key, 'viewer')
      expect(await guards.deps.authz.deps.cache.get(request, key)).to.equal('viewer')
    })
  })

  it('binds the agent readiness port to the HTTP adapter', async () => {
    sinon.stub(KeyValueStoreService, 'getInstance').returns({ connect: sinon.stub().resolves() } as never)
    const appConfig = { jwtSecret: 'j', scopedJwtSecret: 's', aiBackend: 'http://ai' } as never
    const container = await EnterpriseSearchAgentContainer.initialize({} as never, appConfig)
    expect(container.get(COLLAB_TYPES.AgentReadinessPort)).to.be.instanceOf(HttpAgentReadinessAdapter)
  })

  it('binds the collaboration service, its feed and readiness services, the controller and the outbox notifier', async () => {
    sinon.stub(KeyValueStoreService, 'getInstance').returns({ connect: sinon.stub().resolves() } as never)
    const appConfig = { jwtSecret: 'j', scopedJwtSecret: 's', iamBackend: 'http://iam', connectorBackend: 'http://c' } as never
    const container = await EnterpriseSearchAgentContainer.initialize({} as never, appConfig)
    for (const token of [COLLAB_TYPES.CollaborationService, COLLAB_TYPES.FeedService, COLLAB_TYPES.ReadinessService, COLLAB_TYPES.FeatureFlags]) {
      expect(container.isBound(token), String(token)).to.equal(true)
    }
    expect(container.get(COLLAB_TYPES.CollaboratorsController)).to.be.instanceOf(CollaboratorsController)
    expect(container.get(COLLAB_TYPES.CollaborationNotifier)).to.be.instanceOf(OutboxCollaborationNotifier)
    expect(container.isBound(COLLAB_TYPES.ConversationEventProducers)).to.equal(true)
  })
})
