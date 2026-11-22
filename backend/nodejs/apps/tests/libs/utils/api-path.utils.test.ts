import { expect } from 'chai'
import { accessLogLevel, isApiPath, isInternalApiPath } from '../../../src/libs/utils/api-path.utils'

describe('isApiPath', () => {
  for (const path of ['/api/v1/org', '/API/v1/org', '/Api/V1/Org', '/api/']) {
    it(`treats ${path} as API traffic`, () => {
      expect(isApiPath(path)).to.equal(true)
    })
  }

  for (const path of ['/', '/chat/', '/api', '/apiary/x', '/mcp', '/_next/static/a.js']) {
    it(`leaves ${path} out`, () => {
      expect(isApiPath(path)).to.equal(false)
    })
  }
})

describe('isInternalApiPath', () => {
  for (const path of [
    '/api/v1/document/internal/records/abc/documents',
    '/API/v1/Connectors/Internal/refreshIndividualConnectorToken',
    '/api/v1/internal/',
  ]) {
    it(`treats ${path} as service-to-service traffic`, () => {
      expect(isInternalApiPath(path)).to.equal(true)
    })
  }

  for (const path of [
    '/api/v1/document/records/abc',
    '/api/v1/internals/x',
    '/api/v1/document/internal-notes',
    '/internal/records/abc',
  ]) {
    it(`leaves ${path} out`, () => {
      expect(isInternalApiPath(path)).to.equal(false)
    })
  }
})

describe('accessLogLevel', () => {
  const internal = '/api/v1/document/internal/records/abc/documents'

  it('logs a successful internal call at debug', () => {
    expect(accessLogLevel(internal, 200)).to.equal('debug')
    expect(accessLogLevel(internal, 304)).to.equal('debug')
  })

  it('logs a failed internal call at info, and a 5xx at warn', () => {
    expect(accessLogLevel(internal, 400)).to.equal('info')
    expect(accessLogLevel(internal, 503)).to.equal('warn')
  })

  it('logs user API traffic at info, and a 5xx at warn: the error line is the one at error', () => {
    expect(accessLogLevel('/api/v1/org', 200)).to.equal('info')
    expect(accessLogLevel('/api/v1/org', 404)).to.equal('info')
    expect(accessLogLevel('/api/v1/org', 500)).to.equal('warn')
  })

  it('logs a successful health poll at debug, any case, and a failed one as usual', () => {
    expect(accessLogLevel('/api/v1/health', 200)).to.equal('debug')
    expect(accessLogLevel('/API/v1/Health/services', 200)).to.equal('debug')
    expect(accessLogLevel('/api/v1/health/services', 503)).to.equal('warn')
    expect(accessLogLevel('/api/v1/healthz', 200)).to.equal('info')
    expect(accessLogLevel('/api/v1/healthcheck-report', 200)).to.equal('info')
  })

  it('does not log non-API paths', () => {
    expect(accessLogLevel('/_next/static/a.js', 200)).to.equal(null)
    expect(accessLogLevel('/internal/records/abc', 200)).to.equal(null)
  })
})
