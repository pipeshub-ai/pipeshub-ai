import { expect } from 'chai'
import {
  REDACTED,
  redactSensitiveQueryParams,
} from '../../../src/libs/utils/log-redaction.utils'

describe('log-redaction.utils', () => {
  describe('redactSensitiveQueryParams', () => {
    it('redacts an OAuth authorization code', () => {
      // The concrete leak: /oauth/callback?code=... is a credential that can be
      // exchanged for tokens by whoever reads the access log first.
      const out = redactSensitiveQueryParams(
        '/api/v1/mcp-servers/oauth/callback?code=abc123&state=xyz',
      )
      expect(out).to.not.contain('abc123')
      expect(out).to.contain(`code=${encodeURIComponent(REDACTED)}`)
    })

    it('keeps the path and non-sensitive params intact', () => {
      const out = redactSensitiveQueryParams('/api/v1/docs?page=2&sort=name')
      expect(out).to.equal('/api/v1/docs?page=2&sort=name')
    })

    it('leaves a url without a query string untouched', () => {
      expect(redactSensitiveQueryParams('/api/v1/health')).to.equal(
        '/api/v1/health',
      )
    })

    it('redacts presigned-url signatures', () => {
      const out = redactSensitiveQueryParams(
        '/f.pdf?X-Amz-Signature=deadbeef&X-Amz-Credential=AKIA',
      )
      expect(out).to.not.contain('deadbeef')
      expect(out).to.not.contain('AKIA')
    })

    it('is case-insensitive on the parameter name', () => {
      const out = redactSensitiveQueryParams('/cb?CODE=secret')
      expect(out).to.not.contain('secret')
    })

    it('redacts an absolute referrer while keeping its origin', () => {
      // A Referer is absolute and can itself be an OAuth callback. Stripping the
      // origin would remove the only thing that makes a referrer worth logging.
      const out = redactSensitiveQueryParams(
        'https://app.example.com/cb?code=secret&page=2',
      )
      expect(out).to.not.contain('secret')
      expect(out).to.contain('https://app.example.com/cb')
      expect(out).to.contain('page=2')
    })

    it('drops the query entirely when the url cannot be parsed', () => {
      // Falling back to pass-through would be the one case where a secret
      // survives, so an unparseable url loses its query rather than its safety.
      const out = redactSensitiveQueryParams('http://[::bad::]/cb?code=secret')
      expect(out).to.not.contain('secret')
    })
  })
})
