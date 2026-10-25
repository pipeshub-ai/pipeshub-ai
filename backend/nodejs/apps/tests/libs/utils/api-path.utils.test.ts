import { expect } from 'chai'
import { isApiPath } from '../../../src/libs/utils/api-path.utils'

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
