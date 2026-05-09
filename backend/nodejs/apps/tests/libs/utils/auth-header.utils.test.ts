import { expect } from 'chai'
import { parseBearerToken } from '../../../src/libs/utils/auth-header.utils'

describe('libs/utils/auth-header.utils', () => {
  describe('parseBearerToken', () => {
    it('should return null for undefined or null header', () => {
      expect(parseBearerToken(undefined)).to.be.null
      expect(parseBearerToken(null)).to.be.null
    })

    it('should return null for empty header', () => {
      expect(parseBearerToken('')).to.be.null
    })

    it('should return null when scheme is not Bearer', () => {
      expect(parseBearerToken('Basic token123')).to.be.null
      expect(parseBearerToken('bearer token123')).to.be.null
    })

    it('should return null when token is missing', () => {
      expect(parseBearerToken('Bearer')).to.be.null
      expect(parseBearerToken('Bearer ')).to.be.null
    })

    it('should return token for valid Bearer header', () => {
      expect(parseBearerToken('Bearer token123')).to.equal('token123')
    })

    it('should parse first token segment when extra parts exist', () => {
      expect(parseBearerToken('Bearer token123 extra-data')).to.equal('token123')
    })
  })
})
