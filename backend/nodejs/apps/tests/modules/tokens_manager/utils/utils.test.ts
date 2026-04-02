import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { normalizeUrl } from '../../../../src/modules/tokens_manager/utils/utils'

describe('tokens_manager/utils/utils', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('normalizeUrl', () => {
    it('should remove trailing slash from URL', () => {
      expect(normalizeUrl('http://example.com/')).to.equal('http://example.com')
    })

    it('should return URL unchanged if no trailing slash', () => {
      expect(normalizeUrl('http://example.com')).to.equal('http://example.com')
    })

    it('should trim whitespace from URL', () => {
      expect(normalizeUrl('  http://example.com/  ')).to.equal('http://example.com')
    })

    it('should return empty string for empty input', () => {
      expect(normalizeUrl('')).to.equal('')
    })

    it('should return empty string for null/undefined input', () => {
      expect(normalizeUrl(null as any)).to.equal('')
      expect(normalizeUrl(undefined as any)).to.equal('')
    })

    it('should return empty string for non-string input', () => {
      expect(normalizeUrl(123 as any)).to.equal('')
    })

    it('should handle URLs with multiple trailing slashes by removing only the last one', () => {
      const result = normalizeUrl('http://example.com/path/')
      expect(result).to.equal('http://example.com/path')
    })

    it('should handle URLs with paths correctly', () => {
      expect(normalizeUrl('http://example.com/api/v1/')).to.equal('http://example.com/api/v1')
      expect(normalizeUrl('http://example.com/api/v1')).to.equal('http://example.com/api/v1')
    })
  })
})
