import { expect } from 'chai'
import { deriveUserActionSecret } from '../../../src/libs/utils/jwtKeys'

describe('deriveUserActionSecret', () => {
  const secret = 'test-scoped-secret'

  it('should be deterministic', () => {
    expect(deriveUserActionSecret(secret)).to.equal(deriveUserActionSecret(secret))
  })

  // Pinned so a change to the derivation context is caught: the Python
  // services' tests recompute this value to prove they reject such tokens.
  it('should match the known HMAC-SHA256 vector', () => {
    expect(deriveUserActionSecret(secret)).to.equal(
      '3ce2811b305d7de9ca1ed0da56b525ac98f18e7fd67d4e4cdfdad7d2e7ad028c',
    )
  })

  it('should differ from the input secret', () => {
    expect(deriveUserActionSecret(secret)).to.not.equal(secret)
  })

  it('should differ for different secrets', () => {
    expect(deriveUserActionSecret(secret)).to.not.equal(
      deriveUserActionSecret(`${secret}-other`),
    )
  })

  it('should throw on an empty secret', () => {
    expect(() => deriveUserActionSecret('')).to.throw(
      /Scoped JWT secret is not configured/,
    )
  })
})
