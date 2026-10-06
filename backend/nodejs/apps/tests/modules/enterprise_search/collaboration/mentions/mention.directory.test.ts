import { expect } from 'chai'
import sinon from 'sinon'
import { HttpAgentDirectory } from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/agent.directory'
import { AIServiceCommand } from '../../../../../src/libs/commands/ai_service/ai.service.command'
import { FixedClock } from '../../../../../src/libs/types/clock'

const identity = { userId: 'u1', orgId: 'o1', authHeaders: { authorization: 'Bearer t', 'content-length': '99' }, requestKey: {} }

describe('HttpAgentDirectory (agent mention checks, 60 s per caller)', () => {
  afterEach(() => sinon.restore())

  const stub = (statusCode: number, data?: unknown) => sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode, data } as never)

  it('asks the query service as the caller, sending only the credential, and reads the service-account flag', async () => {
    const execute = stub(200, { agent: { isServiceAccount: true } })
    const dir = new HttpAgentDirectory(() => 'http://ai.test', { warn: sinon.stub() })
    expect(await dir.canExecute(identity, 'a b')).to.equal(true)
    expect(await dir.isServiceAccount(identity, 'a b')).to.equal(true)
    expect(execute.callCount).to.equal(1)
    const command = execute.thisValues[0] as unknown as { uri: string; headers: Record<string, string> }
    expect(command.uri).to.equal('http://ai.test/api/v1/agent/a%20b')
    expect(Object.keys(command.headers).map((k) => k.toLowerCase())).to.not.include('content-length')
    expect(JSON.stringify(command.headers)).to.contain('Bearer t')
  })

  it('404 is a denial; 403 and transport errors are unavailable and are not cached', async () => {
    const execute = stub(404)
    const dir = new HttpAgentDirectory(() => 'http://ai.test', { warn: sinon.stub() })
    expect(await dir.canExecute(identity, 'a')).to.equal(false)
    execute.resolves({ statusCode: 403 } as never)
    expect(await dir.canExecute(identity, 'b')).to.equal('unavailable')
    execute.rejects(new Error('down'))
    expect(await dir.canExecute(identity, 'b')).to.equal('unavailable')
    expect(execute.callCount).to.equal(3)
  })

  it('caches an answer per caller for 60 s', async () => {
    const execute = stub(200, { agent: {} })
    const clock = new FixedClock(1_000)
    const dir = new HttpAgentDirectory(() => 'http://ai.test', { warn: sinon.stub() }, clock)
    await dir.canExecute(identity, 'a')
    await dir.canExecute(identity, 'a')
    await dir.canExecute({ ...identity, userId: 'u2' }, 'a')
    expect(execute.callCount).to.equal(2)
    clock.advance(61_000)
    await dir.canExecute(identity, 'a')
    expect(execute.callCount).to.equal(3)
  })

  it('describes an agent from the same cached lookup, and nothing for a denied or nameless one', async () => {
    const execute = stub(200, { agent: { name: 'Offer drafter', handle: 'offer-drafter' } })
    const dir = new HttpAgentDirectory(() => 'http://ai.test', { warn: sinon.stub() })
    expect(await dir.canExecute(identity, 'a')).to.equal(true)
    expect(await dir.describe(identity, 'a')).to.deep.equal({ name: 'Offer drafter', handle: 'offer-drafter' })
    expect(execute.callCount).to.equal(1)
    execute.resolves({ statusCode: 200, data: { agent: {} } } as never)
    expect(await dir.describe(identity, 'nameless')).to.equal(undefined)
    execute.resolves({ statusCode: 404 } as never)
    expect(await dir.describe(identity, 'denied')).to.equal(undefined)
  })
})
