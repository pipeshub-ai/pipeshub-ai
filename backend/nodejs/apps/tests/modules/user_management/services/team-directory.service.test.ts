import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import {
  ConnectorTeamDirectory,
  ITeamDirectory,
  RequestScopedTeamDirectory,
  teamDirectoryFor,
} from '../../../../src/modules/user_management/services/team-directory.service'
import { callerIdentityOf, CallerIdentity } from '../../../../src/libs/types/caller-identity'
import { createMockLogger } from '../../../helpers/mock-logger'
import { Logger } from '../../../../src/libs/services/logger.service'
import { resolveCallerTeamIds } from '../../../../src/modules/projects/utils/team-membership'

const appConfig = { connectorBackend: 'http://connector:8088' } as any

function identity(headers: Record<string, string> = { authorization: 'Bearer t' }): CallerIdentity {
  return callerIdentityOf({ headers, user: { userId: 'u1', orgId: 'o1' } } as any)
}

describe('callerIdentityOf', () => {
  it('uses the request itself as the memo key and forwards its headers', () => {
    const req: any = { headers: { authorization: 'Bearer t' }, user: { userId: 'u1', orgId: 'o1' } }
    const id = callerIdentityOf(req)
    expect(id.requestKey).to.equal(req)
    expect(id.userId).to.equal('u1')
    expect(id.orgId).to.equal('o1')
    expect(id.authHeaders).to.equal(req.headers)
  })
})

describe('ConnectorTeamDirectory', () => {
  afterEach(() => sinon.restore())

  it('is unresolved, without throwing, when the lookup fails', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').rejects(new Error('down'))
    const dir = new ConnectorTeamDirectory(appConfig, createMockLogger())
    expect(await dir.callerTeamIds(identity())).to.deep.equal({ status: 'unresolved' })
  })

  it('is unresolved on a non-200 and on a body without teamIds', async () => {
    const stub = sinon.stub(AIServiceCommand.prototype, 'execute')
    stub.onFirstCall().resolves({ statusCode: 503, data: null } as any)
    stub.onSecondCall().resolves({ statusCode: 200, data: {} } as any)
    const dir = new ConnectorTeamDirectory(appConfig, createMockLogger())
    expect(await dir.callerTeamIds(identity())).to.deep.equal({ status: 'unresolved' })
    expect(await dir.callerTeamIds(identity())).to.deep.equal({ status: 'unresolved' })
  })

  it('returns ok with the string ids, dropping empties and non-strings', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves({
      statusCode: 200,
      data: { teamIds: ['a', '', null, 3, 'b'] },
    } as any)
    const dir = new ConnectorTeamDirectory(appConfig, createMockLogger())
    expect(await dir.callerTeamIds(identity())).to.deep.equal({ status: 'ok', teamIds: ['a', 'b'] })
  })

  it('exists is true only on a 200 and false on a 404 or a throw', async () => {
    const stub = sinon.stub(AIServiceCommand.prototype, 'execute')
    stub.onCall(0).resolves({ statusCode: 200, data: {} } as any)
    stub.onCall(1).resolves({ statusCode: 404, data: null } as any)
    stub.onCall(2).rejects(new Error('x'))
    const dir = new ConnectorTeamDirectory(appConfig, createMockLogger())
    expect(await dir.exists('t1', identity())).to.equal(true)
    expect(await dir.exists('t1', identity())).to.equal(false)
    expect(await dir.exists('t1', identity())).to.equal(false)
  })
})

describe('ConnectorTeamDirectory.memberUserIds', () => {
  afterEach(() => sinon.restore())
  const members = (ids: string[], memberCount = ids.length) => ({ statusCode: 200, data: { team: { members: ids.map((userId) => ({ userId })), memberCount } } }) as any

  it('returns the member user ids as the caller, asking for one more than the cap', async () => {
    const stub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves(members(['u1', 'u2']))
    const dir = new ConnectorTeamDirectory(appConfig, createMockLogger())
    expect(await dir.memberUserIds('t 1', identity(), { limit: 50 })).to.deep.equal({ status: 'ok', userIds: ['u1', 'u2'] })
    const options = (stub.thisValues[0] as any).options ?? (stub.thisValues[0] as any)
    expect(JSON.stringify(options)).to.include('/api/v1/entity/team/t%201/users?page=1&limit=51')
    expect(JSON.stringify(options)).to.include('Bearer t')
  })

  it('a team over the cap is unresolved, not truncated', async () => {
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves(members(['u1'], 80))
    const dir = new ConnectorTeamDirectory(appConfig, createMockLogger())
    expect(await dir.memberUserIds('t', identity(), { limit: 50 })).to.deep.equal({ status: 'unresolved' })
  })

  it('exactly at the cap is fine', async () => {
    const ids = Array.from({ length: 50 }, (_, i) => `u${i}`)
    sinon.stub(AIServiceCommand.prototype, 'execute').resolves(members(ids))
    const dir = new ConnectorTeamDirectory(appConfig, createMockLogger())
    expect(await dir.memberUserIds('t', identity(), { limit: 50 })).to.deep.include({ status: 'ok' })
  })

  it('never asks for more than a page of 100', async () => {
    const stub = sinon.stub(AIServiceCommand.prototype, 'execute').resolves(members(['u1']))
    const dir = new ConnectorTeamDirectory(appConfig, createMockLogger())
    await dir.memberUserIds('t', identity(), { limit: 500 })
    expect(JSON.stringify(stub.thisValues[0])).to.include('limit=100')
  })

  it('is unresolved, without throwing, on a non-200, a malformed body or a failure; ids that are not strings are dropped', async () => {
    const stub = sinon.stub(AIServiceCommand.prototype, 'execute')
    stub.onCall(0).resolves({ statusCode: 404, data: null } as any)
    stub.onCall(1).resolves({ statusCode: 200, data: {} } as any)
    stub.onCall(2).rejects(new Error('down'))
    stub.onCall(3).resolves({ statusCode: 200, data: { team: { members: [{ userId: 'a' }, { userId: 5 }, {}] } } } as any)
    const dir = new ConnectorTeamDirectory(appConfig, createMockLogger())
    for (let i = 0; i < 3; i += 1) {
      expect(await dir.memberUserIds('t', identity(), { limit: 50 })).to.deep.equal({ status: 'unresolved' })
    }
    expect(await dir.memberUserIds('t', identity(), { limit: 50 })).to.deep.equal({ status: 'ok', userIds: ['a'] })
  })

  it('the request-scoped decorator passes it through unmemoised', async () => {
    const memberUserIds = sinon.stub().resolves({ status: 'ok', userIds: ['u'] })
    const dir = new RequestScopedTeamDirectory({ memberUserIds } as any)
    const id = identity()
    await dir.memberUserIds('t', id, { limit: 50 })
    await dir.memberUserIds('t', id, { limit: 50 })
    expect(memberUserIds.calledTwice).to.equal(true)
    expect(memberUserIds.firstCall.args).to.deep.equal(['t', id, { limit: 50 }])
  })
})

describe('RequestScopedTeamDirectory', () => {
  function fakeInner(): { inner: ITeamDirectory; calls: sinon.SinonStub; exists: sinon.SinonStub } {
    const calls = sinon.stub().resolves({ status: 'ok', teamIds: ['a'] })
    const exists = sinon.stub().resolves(true)
    return { inner: { callerTeamIds: calls, exists, teamsVersion: sinon.stub().resolves(0) }, calls, exists }
  }

  it('shares one lookup between concurrent and repeated calls for the same request', async () => {
    const { inner, calls } = fakeInner()
    const dir = new RequestScopedTeamDirectory(inner)
    const id = identity()

    await Promise.all([dir.callerTeamIds(id), dir.callerTeamIds(id), dir.callerTeamIds(id)])
    await dir.callerTeamIds(id)

    expect(calls.calledOnce).to.equal(true)
  })

  it('does not share results across requests', async () => {
    const { inner, calls } = fakeInner()
    const dir = new RequestScopedTeamDirectory(inner)

    await dir.callerTeamIds(identity())
    await dir.callerTeamIds(identity())

    expect(calls.calledTwice).to.equal(true)
  })

  it('memoizes an unresolved outcome for the request', async () => {
    const { inner, calls } = fakeInner()
    calls.resolves({ status: 'unresolved' })
    const dir = new RequestScopedTeamDirectory(inner)
    const id = identity()

    expect(await dir.callerTeamIds(id)).to.deep.equal({ status: 'unresolved' })
    await dir.callerTeamIds(id)

    expect(calls.calledOnce).to.equal(true)
  })

  it('does not memoize exists', async () => {
    const { inner, exists } = fakeInner()
    const dir = new RequestScopedTeamDirectory(inner)
    const id = identity()

    await dir.exists('t', id)
    await dir.exists('t', id)

    expect(exists.calledTwice).to.equal(true)
  })
})

describe('teamDirectoryFor', () => {
  afterEach(() => sinon.restore())

  it('returns one request-scoped instance per AppConfig', () => {
    const cfg = { connectorBackend: 'http://a' } as any
    expect(teamDirectoryFor(cfg)).to.equal(teamDirectoryFor(cfg))
    expect(teamDirectoryFor(cfg)).to.be.instanceOf(RequestScopedTeamDirectory)
    expect(teamDirectoryFor({ connectorBackend: 'http://b' } as any)).to.not.equal(teamDirectoryFor(cfg))
  })

  it('does not touch the process-wide Logger per call (getInstance rewrites its service meta)', () => {
    const getInstance = sinon.spy(Logger, 'getInstance')
    teamDirectoryFor({ connectorBackend: 'http://d' } as any)
    teamDirectoryFor({ connectorBackend: 'http://e' } as any)
    expect(getInstance.called).to.equal(false)
  })

  it('is shared with the projects membership resolver (callerTeamIds memoised, exists not)', async () => {
    const cfg = { connectorBackend: 'http://c' } as any
    const ids = sinon.stub(ConnectorTeamDirectory.prototype, 'callerTeamIds').resolves({ status: 'ok', teamIds: ['t1'] })
    const exists = sinon.stub(ConnectorTeamDirectory.prototype, 'exists').resolves(true)
    const req = { headers: { authorization: 'Bearer t' }, user: { userId: 'u1', orgId: 'o1' } } as any

    expect(await resolveCallerTeamIds(req, cfg)).to.deep.equal(['t1'])
    await teamDirectoryFor(cfg).callerTeamIds(callerIdentityOf(req))
    expect(ids.calledOnce).to.equal(true)

    await teamDirectoryFor(cfg).exists('t', callerIdentityOf(req))
    await teamDirectoryFor(cfg).exists('t', callerIdentityOf(req))
    expect(exists.calledTwice).to.equal(true)
  })
})
