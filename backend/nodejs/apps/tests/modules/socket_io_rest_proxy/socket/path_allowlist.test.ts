import { expect } from 'chai'
import {
  DEFAULT_CLI_RPC_ALLOWED_REST_PREFIXES,
  normalizeAndAssertCliRpcProxyPath,
} from '../../../../src/modules/socket_io_rest_proxy/socket/path_allowlist'

describe('normalizeAndAssertCliRpcProxyPath', () => {
  it('accepts exact prefix and nested allowed path', () => {
    const exact = normalizeAndAssertCliRpcProxyPath(
      '/api/v1/connectors',
      DEFAULT_CLI_RPC_ALLOWED_REST_PREFIXES,
    )
    const nested = normalizeAndAssertCliRpcProxyPath(
      '/api/v1/connectors/my-connector',
      DEFAULT_CLI_RPC_ALLOWED_REST_PREFIXES,
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
    const result = normalizeAndAssertCliRpcProxyPath(
      '  /api/v1/knowledgeBase%2Fdocs//abc  ',
      DEFAULT_CLI_RPC_ALLOWED_REST_PREFIXES,
    )

    expect(result).to.deep.equal({
      ok: true,
      normalizedPath: '/api/v1/knowledgeBase/docs/abc',
    })
  })

  it('rejects path traversal and non-segment prefix bypass', () => {
    const traversal = normalizeAndAssertCliRpcProxyPath(
      '/api/v1/connectors/../secrets',
      DEFAULT_CLI_RPC_ALLOWED_REST_PREFIXES,
    )
    const prefixBypass = normalizeAndAssertCliRpcProxyPath(
      '/api/v1/connectorsEvil',
      DEFAULT_CLI_RPC_ALLOWED_REST_PREFIXES,
    )

    expect(traversal).to.deep.equal({
      ok: false,
      reason: 'Path is not allowed for CLI RPC',
    })
    expect(prefixBypass).to.deep.equal({
      ok: false,
      reason: 'Path is not allowed for CLI RPC',
    })
  })

  it('rejects malformed and invalid paths', () => {
    const missingSlash = normalizeAndAssertCliRpcProxyPath(
      'api/v1/connectors',
      DEFAULT_CLI_RPC_ALLOWED_REST_PREFIXES,
    )
    const badEncoding = normalizeAndAssertCliRpcProxyPath(
      '/api/v1/connectors/%E0%A4%A',
      DEFAULT_CLI_RPC_ALLOWED_REST_PREFIXES,
    )
    const nullByte = normalizeAndAssertCliRpcProxyPath(
      '/api/v1/connectors/%00',
      DEFAULT_CLI_RPC_ALLOWED_REST_PREFIXES,
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
