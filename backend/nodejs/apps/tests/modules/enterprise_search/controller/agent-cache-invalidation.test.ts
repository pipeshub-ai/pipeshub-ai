import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createAgent, updateAgent, deleteAgent } from '../../../../src/modules/enterprise_search/controller/es_controller'
import { AGENT_LIST_TTL_MS, HttpAgentDirectory, useAgentCacheInvalidator } from '../../../../src/modules/enterprise_search/services/collaboration/mentions/agent.directory'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { FixedClock } from '../../../../src/libs/types/clock'

const identity = { userId: 'u1', orgId: 'o1', authHeaders: { authorization: 'Bearer t' }, requestKey: {} }
const appConfig: any = { aiBackend: 'http://ai:8000', jwtSecret: 'j', scopedJwtSecret: 's' }
const listOf = (...keys: string[]) => ({ statusCode: 200, data: { agents: keys.map((k) => ({ _key: k, name: k })), pagination: {} } })

describe('agent list and access caches', () => {
  afterEach(() => {
    sinon.restore()
    useAgentCacheInvalidator(undefined)
  })

  const world = () => {
    const clock = new FixedClock(1_000)
    const dir = new HttpAgentDirectory(() => 'http://ai.test', { warn: sinon.stub() }, clock)
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute')
    return { clock, dir, execute }
  }

  it('the list is cached for 10 s, not 60', async () => {
    const { clock, dir, execute } = world()
    execute.resolves(listOf('a') as never)
    await dir.listExecutable(identity)
    await dir.listExecutable(identity)
    expect(execute.callCount).to.equal(1)
    clock.advance(AGENT_LIST_TTL_MS + 1)
    execute.resolves(listOf('a', 'b') as never)
    expect(((await dir.listExecutable(identity)) as any[]).map((a) => a.agentKey)).to.deep.equal(['a', 'b'])
  })

  it('invalidate drops the caller’s list at once, and only theirs', async () => {
    const { dir, execute } = world()
    execute.resolves(listOf('a') as never)
    await dir.listExecutable(identity)
    await dir.listExecutable({ ...identity, userId: 'u2' })
    execute.resolves(listOf('a', 'new') as never)
    dir.invalidate(identity)
    expect(((await dir.listExecutable(identity)) as any[]).map((a) => a.agentKey)).to.deep.equal(['a', 'new'])
    expect(((await dir.listExecutable({ ...identity, userId: 'u2' })) as any[]).map((a) => a.agentKey)).to.deep.equal(['a'])
  })

  it('a cached denial of a key does not outlive a create of that key, for anyone in the org', async () => {
    const { dir, execute } = world()
    execute.resolves({ statusCode: 404 } as never)
    const other = { ...identity, userId: 'u2' }
    expect(await dir.canExecute(identity, 'fresh')).to.equal(false)
    expect(await dir.canExecute(other, 'fresh')).to.equal(false)
    expect(await dir.canExecute(identity, 'unrelated')).to.equal(false)
    dir.invalidate(identity, 'fresh')
    execute.resolves({ statusCode: 200, data: { agent: { name: 'Fresh' } } } as never)
    expect(await dir.canExecute(identity, 'fresh')).to.equal(true)
    expect(await dir.canExecute(other, 'fresh')).to.equal(true)
    expect(execute.callCount).to.equal(5)
    expect(await dir.canExecute(identity, 'unrelated')).to.equal(false)
    expect(execute.callCount).to.equal(5)
  })

  it('another org’s cached answers are left alone', async () => {
    const { dir, execute } = world()
    execute.resolves({ statusCode: 404 } as never)
    const foreign = { ...identity, orgId: 'o2' }
    await dir.canExecute(foreign, 'k')
    dir.invalidate(identity, 'k')
    await dir.canExecute(foreign, 'k')
    expect(execute.callCount).to.equal(1)
  })

  describe('the routes that proxy agent changes invalidate', () => {
    const request = (params: Record<string, string> = {}) =>
      ({ headers: { authorization: 'Bearer t' }, body: { name: 'Joke Buddy' }, params, query: {}, user: { userId: 'u1', orgId: 'o1' }, context: { requestId: 'r' } }) as any
    const response = () => {
      const res: any = { status: sinon.stub(), json: sinon.stub() }
      res.status.returns(res)
      return res
    }
    const run = async (handler: any, req: any) => {
      const next = sinon.stub()
      await handler(req, response(), next)
      expect(next.called, String(next.firstCall?.args[0]?.message)).to.equal(false)
    }

    it('create (with the new key), update and delete, each for the caller', async () => {
      const invalidate = sinon.stub()
      useAgentCacheInvalidator({ invalidate })
      const execute = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { agent: { _key: 'agent-9' } } } as never)
      await run(createAgent(appConfig), request())
      await run(updateAgent(appConfig), request({ agentKey: 'agent-9' }))
      await run(deleteAgent(appConfig), request({ agentKey: 'agent-9' }))
      expect(invalidate.args.map(([who, key]) => [who.userId, who.orgId, key])).to.deep.equal([
        ['u1', 'o1', 'agent-9'],
        ['u1', 'o1', 'agent-9'],
        ['u1', 'o1', 'agent-9'],
      ])
      expect(execute.callCount).to.equal(3)
    })

    it('a refused change invalidates nothing', async () => {
      const invalidate = sinon.stub()
      useAgentCacheInvalidator({ invalidate })
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 403, data: {} } as never)
      await updateAgent(appConfig)(request({ agentKey: 'a' }), response(), sinon.stub())
      expect(invalidate.called).to.equal(false)
    })

    it('after a create the picker lists the new agent at once instead of up to a minute later', async () => {
      const { dir, execute } = world()
      useAgentCacheInvalidator(dir)
      execute.callsFake(async function (this: any) {
        return (/\/agent\/create$/.test(this.uri) ? { statusCode: 200, data: { agent: { _key: 'joke' } } } : listOf(...(created ? ['joke'] : []))) as never
      })
      let created = false
      expect(await dir.listExecutable(identity)).to.deep.equal([])
      created = true
      await run(createAgent(appConfig), request())
      expect(((await dir.listExecutable(identity)) as any[]).map((a) => a.agentKey)).to.deep.equal(['joke'])
    })
  })
})
