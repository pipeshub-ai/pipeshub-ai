import { expect } from 'chai'
import { refId } from '../../../../src/modules/authz/validators/authz.validators'

describe('authz validators: refId', () => {
  it('returns the id part of a <type>:<id> reference', () => {
    expect(refId('chat:65a1b2c3d4e5f60718293a4b')).to.equal('65a1b2c3d4e5f60718293a4b')
  })

  it('refuses a repeated query parameter that arrives as an array', () => {
    expect(() => refId(['chat:a', 'chat:b'] as unknown as string)).to.throw(TypeError)
  })
})
