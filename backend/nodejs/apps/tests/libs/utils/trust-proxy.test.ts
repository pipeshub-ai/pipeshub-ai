import { expect } from 'chai'
import { parseTrustProxy } from '../../../src/libs/utils/trust-proxy'

describe('parseTrustProxy', () => {
  it('should trust no proxy when unset, empty, "false" or "0"', () => {
    for (const raw of [undefined, '', '  ', 'false', '0']) {
      expect(parseTrustProxy(raw)).to.deep.equal({ value: false })
    }
  })

  it('should parse a hop count', () => {
    expect(parseTrustProxy('2')).to.deep.equal({ value: 2 })
  })

  it('should parse a comma-separated list of addresses/CIDRs', () => {
    expect(parseTrustProxy('loopback, 10.0.0.0/8,')).to.deep.equal({
      value: ['loopback', '10.0.0.0/8'],
    })
  })

  it('should reject "true" and return a warning', () => {
    const parsed = parseTrustProxy('true')
    expect(parsed.value).to.equal(false)
    expect(parsed.warning).to.include('TRUST_PROXY=true')
  })
})
