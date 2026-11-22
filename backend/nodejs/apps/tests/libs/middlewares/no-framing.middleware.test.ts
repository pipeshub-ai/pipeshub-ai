import { expect } from 'chai'
import express from 'express'
import { AddressInfo } from 'net'
import { noFraming, UNFRAMEABLE_PAGE_PATHS } from '../../../src/libs/middlewares/no-framing.middleware'

describe('noFraming', () => {
  async function headersFor(path: string): Promise<Headers> {
    const app = express()
    // Stands in for a policy set earlier (helmet's, in strict mode), which must survive.
    app.use((_req, res, next) => {
      res.setHeader('Content-Security-Policy', "default-src 'self'")
      next()
    })
    app.use(UNFRAMEABLE_PAGE_PATHS, noFraming)
    app.get(/.*/, (_req, res) => { res.send('page') })
    const server = app.listen(0)
    try {
      const { port } = server.address() as AddressInfo
      return (await fetch(`http://127.0.0.1:${port}${path}`)).headers
    } finally {
      server.close()
    }
  }

  for (const path of ['/login', '/login/', '/sign-up/', '/oauth/authorize/', '/oauth/device/']) {
    it(`forbids framing ${path} and keeps the existing policy`, async () => {
      const h = await headersFor(path)
      expect(h.get('x-frame-options')).to.equal('DENY')
      expect(h.get('content-security-policy')).to.equal("default-src 'self', frame-ancestors 'none'")
    })
  }

  it('leaves other pages alone', async () => {
    const h = await headersFor('/chat/')
    expect(h.get('x-frame-options')).to.equal(null)
    expect(h.get('content-security-policy')).to.equal("default-src 'self'")
  })
})
