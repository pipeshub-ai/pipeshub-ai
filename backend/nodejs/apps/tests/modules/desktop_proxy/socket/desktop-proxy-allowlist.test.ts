import { expect } from 'chai'
import {
  DEFAULT_REST_PROXY_ALLOWED_PREFIXES,
  normalizeAndAssertRestProxyPath,
} from '../../../../src/modules/desktop_proxy/socket/desktop-proxy-allowlist'

describe('normalizeAndAssertRestProxyPath', () => {
  it('accepts exact prefix and nested allowed path', () => {
    const exact = normalizeAndAssertRestProxyPath(
      '/api/v1/connectors',
      DEFAULT_REST_PROXY_ALLOWED_PREFIXES,
    )
    const nested = normalizeAndAssertRestProxyPath(
      '/api/v1/connectors/my-connector',
      DEFAULT_REST_PROXY_ALLOWED_PREFIXES,
    )

    expect(exact).to.deep.equal({
      ok: true,
      normalizedPath: '/api/v1/connectors',
    })
    expect(nested).to.deep.equal({
      ok: true,
      normalizedPath: '/api/v1/connectors/my-connector',
    })
  })

  it('normalizes encoded and duplicate-slash paths', () => {
    const result = normalizeAndAssertRestProxyPath(
      '  /api/v1/knowledgeBase%2Fdocs//abc  ',
      DEFAULT_REST_PROXY_ALLOWED_PREFIXES,
    )

    expect(result).to.deep.equal({
      ok: true,
      normalizedPath: '/api/v1/knowledgeBase/docs/abc',
    })
  })

  it('rejects path traversal and non-segment prefix bypass', () => {
    const traversal = normalizeAndAssertRestProxyPath(
      '/api/v1/connectors/../secrets',
      DEFAULT_REST_PROXY_ALLOWED_PREFIXES,
    )
    const prefixBypass = normalizeAndAssertRestProxyPath(
      '/api/v1/connectorsEvil',
      DEFAULT_REST_PROXY_ALLOWED_PREFIXES,
    )

    expect(traversal).to.deep.equal({
      ok: false,
      reason: 'Path is not allowed for REST proxy',
    })
    expect(prefixBypass).to.deep.equal({
      ok: false,
      reason: 'Path is not allowed for REST proxy',
    })
  })

  it('rejects malformed and invalid paths', () => {
    const missingSlash = normalizeAndAssertRestProxyPath(
      'api/v1/connectors',
      DEFAULT_REST_PROXY_ALLOWED_PREFIXES,
    )
    const badEncoding = normalizeAndAssertRestProxyPath(
      '/api/v1/connectors/%E0%A4%A',
      DEFAULT_REST_PROXY_ALLOWED_PREFIXES,
    )
    const nullByte = normalizeAndAssertRestProxyPath(
      '/api/v1/connectors/%00',
      DEFAULT_REST_PROXY_ALLOWED_PREFIXES,
    )

    expect(missingSlash).to.deep.equal({
      ok: false,
      reason: 'Path must start with /',
    })
    expect(badEncoding).to.deep.equal({
      ok: false,
      reason: 'Invalid path encoding',
    })
    expect(nullByte).to.deep.equal({
      ok: false,
      reason: 'Invalid path',
    })
  })
})
