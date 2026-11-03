import { expect } from 'chai'
import { getCurrentOrgId, makeOrgContext, runWithOrgContext } from '../../../src/libs/context/orgContext'
import { setIdentityPort } from '../../../src/libs/repositories/identity'
import { inInstallOrg } from '../../../src/libs/repositories/identity/install-org'

describe('inInstallOrg', () => {
  const saved = process.env.DOC_STORE
  const asked: string[] = []
  const port = (id: string | null) => ({ oldestOrgId: async () => (asked.push('oldest'), id) }) as never

  afterEach(() => {
    if (saved === undefined) delete process.env.DOC_STORE
    else process.env.DOC_STORE = saved
    setIdentityPort()
    asked.length = 0
  })

  it('on MongoDB runs the lookup as it is, without asking for the organisation', async () => {
    delete process.env.DOC_STORE
    setIdentityPort(port('a'.repeat(24)))
    expect(await inInstallOrg('t', async () => getCurrentOrgId(), [] as never)).to.equal(undefined)
    expect(asked).to.deep.equal([])
  })

  it('on PostgreSQL binds the install organisation, answers `none` without one, and keeps a bound organisation', async () => {
    process.env.DOC_STORE = 'postgres'
    const org = 'a'.repeat(24)
    setIdentityPort(port(org))
    expect(await inInstallOrg('t', async () => getCurrentOrgId(), 'none')).to.equal(org)
    setIdentityPort(port(null))
    expect(await inInstallOrg('t', async () => 'ran', 'none')).to.equal('none')
    asked.length = 0
    const bound = 'b'.repeat(24)
    const seen = await runWithOrgContext(makeOrgContext({ orgId: bound, userId: null, kind: 'service', source: 'test' }), () =>
      inInstallOrg('t', async () => getCurrentOrgId(), 'none'),
    )
    expect(seen).to.equal(bound)
    expect(asked).to.deep.equal([])
  })
})
