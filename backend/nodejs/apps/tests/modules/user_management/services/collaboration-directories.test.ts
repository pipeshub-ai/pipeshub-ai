import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { ConnectorTeamLookup } from '../../../../src/modules/user_management/services/team-lookup.service'
import { MongoOrgDirectory } from '../../../../src/modules/user_management/services/org-directory.service'
import { Org } from '../../../../src/modules/user_management/schema/org.schema'

const identity = { userId: 'u', orgId: 'o', authHeaders: { authorization: 'Bearer t' }, requestKey: {} }
const lookup = () => new ConnectorTeamLookup({ connectorBackend: 'http://connectors' } as never, { warn: sinon.stub() })

describe('ConnectorTeamLookup', () => {
  afterEach(() => sinon.restore())

  it('reads each team once as the caller and tells present, deleted and unreachable teams apart', async () => {
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute')
    execute.callsFake(async function (this: unknown) {
      const { uri } = this as { uri: string }
      if (uri.endsWith('/ok')) return { statusCode: 200, data: { team: { name: 'Sales' } } } as never
      if (uri.endsWith('/gone')) return { statusCode: 404, data: {} } as never
      if (uri.endsWith('/odd')) return { statusCode: 200, data: {} } as never
      throw new Error('connector down')
    })
    const out = await lookup().describeMany(['ok', 'gone', 'odd', 'down', 'ok'], identity)
    expect(out.get('ok')).to.deep.equal({ status: 'ok', name: 'Sales' })
    expect(out.get('gone')).to.deep.equal({ status: 'missing' })
    expect(out.get('odd')).to.deep.equal({ status: 'unavailable' })
    expect(out.get('down')).to.deep.equal({ status: 'unavailable' })
    expect(execute.callCount).to.equal(4)
  })

  it('reads many teams in bounded batches', async () => {
    let running = 0
    let peak = 0
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async () => {
      running += 1
      peak = Math.max(peak, running)
      await new Promise((resolve) => setTimeout(resolve, 2))
      running -= 1
      return { statusCode: 200, data: { team: { name: 'T' } } } as never
    })
    const ids = Array.from({ length: 30 }, (_, i) => `t${i}`)
    expect((await lookup().describeMany(ids, identity)).size).to.equal(30)
    expect(peak).to.be.at.most(8)
  })
})

describe('MongoOrgDirectory', () => {
  afterEach(() => sinon.restore())
  const orgQuery = (org: unknown) => ({ select: () => ({ lean: () => ({ exec: () => Promise.resolve(org) }) }) })

  it('prefers the short name, then the registered name, then a generic label', async () => {
    const find = sinon.stub(Org, 'findById')
    const id = 'a'.repeat(24)
    find.onCall(0).returns(orgQuery({ shortName: ' Acme ', registeredName: 'Acme Corp' }) as never)
    find.onCall(1).returns(orgQuery({ shortName: '', registeredName: 'Acme Corp' }) as never)
    find.onCall(2).returns(orgQuery(null) as never)
    const dir = new MongoOrgDirectory()
    expect(await dir.displayName(id)).to.equal('Acme')
    expect(await dir.displayName(id)).to.equal('Acme Corp')
    expect(await dir.displayName(id)).to.equal('your organization')
    expect(await dir.displayName('not-an-id')).to.equal('your organization')
  })
})
