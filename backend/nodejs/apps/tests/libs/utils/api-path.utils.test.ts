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

  it('logs a failed internal call at info', () => {
    expect(accessLogLevel(internal, 400)).to.equal('info')
    expect(accessLogLevel(internal, 503)).to.equal('info')
  })

  it('logs user API traffic at info whatever the status', () => {
    expect(accessLogLevel('/api/v1/org', 200)).to.equal('info')
    expect(accessLogLevel('/api/v1/org', 500)).to.equal('info')
  })

  it('does not log non-API paths', () => {
    expect(accessLogLevel('/_next/static/a.js', 200)).to.equal(null)
    expect(accessLogLevel('/internal/records/abc', 200)).to.equal(null)
  })
})
