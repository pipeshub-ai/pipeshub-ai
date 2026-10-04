import { expect } from 'chai'
import sinon from 'sinon'

// Fails only in a serial (--no-parallel) run, when some file declares a top-level
// afterEach(() => sinon.restore()): mocha makes that a root hook that also undoes
// stubs other files set up once in before(). Scope such hooks inside a describe.
describe('root hooks', () => {
  const target = { read: () => 'real' }

  before(() => {
    sinon.stub(target, 'read').returns('stubbed')
  })

  after(() => {
    sinon.restore()
  })

  it('leave a stub made in before() in place for the first test', () => {
    expect(target.read()).to.equal('stubbed')
  })

  it('leave a stub made in before() in place for later tests too', () => {
    expect(target.read()).to.equal('stubbed')
  })
})
