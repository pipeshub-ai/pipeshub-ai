import { expect } from 'chai'
import sinon from 'sinon'
import { requireFlag } from '../../../src/libs/middlewares/require-flag.middleware'

const run = (flags: { isEnabled: sinon.SinonStub }) => {
  const next = sinon.stub()
  return requireFlag(flags, 'SOME_FLAG')({} as never, {} as never, next).then(() => next)
}

describe('requireFlag', () => {
  it('lets the request through while the flag is on', async () => {
    const isEnabled = sinon.stub().resolves(true)
    const next = await run({ isEnabled })
    expect(isEnabled.calledOnceWithExactly('SOME_FLAG')).to.equal(true)
    expect(next.calledOnceWithExactly()).to.equal(true)
  })

  it('answers 404 while it is off', async () => {
    const next = await run({ isEnabled: sinon.stub().resolves(false) })
    expect(next.firstCall.args[0]).to.include({ statusCode: 404 })
  })

  it('forwards a flag-service failure', async () => {
    const next = await run({ isEnabled: sinon.stub().rejects(new Error('kv down')) })
    expect(next.firstCall.args[0].message).to.equal('kv down')
  })
})
