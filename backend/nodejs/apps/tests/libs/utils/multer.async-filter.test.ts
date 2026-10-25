import 'reflect-metadata'
import { createRequire } from 'module'
import path from 'path'
import { expect } from 'chai'
import express from 'express'
import http from 'http'
import type { Server } from 'http'
import type { AddressInfo } from 'net'
import type { Multer } from 'multer'

const BOUNDARY = 'pipeshub-late-error-boundary'

// Guards GHSA-q7qp-rjg7-q26m (fixed in multer 2.5.0). With an asynchronous
// fileFilter the request can already be torn down when busboy reports an
// error; multer must keep an error listener on its busboy so that late error
// cannot become an uncaught exception. The race itself cannot be forced
// deterministically over HTTP, so the test captures multer's busboy instance
// and emits the late error directly.
describe('multer with an asynchronous fileFilter', () => {
  let server: Server
  let port: number
  let busboy: NodeJS.EventEmitter | undefined
  let restoreBusboy: () => void

  before((done) => {
    // multer requires busboy from its own directory; wrap that module to
    // capture each instance.
    const multerDir = path.dirname(require.resolve('multer/package.json'))
    const multerRequire = createRequire(path.join(multerDir, 'lib/make-middleware.js'))
    const busboyPath = multerRequire.resolve('busboy')
    const realBusboy = multerRequire('busboy')
    const cached = require.cache[busboyPath]!
    const original = cached.exports
    cached.exports = (...args: unknown[]) => {
      busboy = realBusboy(...args)
      return busboy
    }
    // multer binds busboy when it loads, so load a fresh copy under the wrap.
    const multerEntry = require.resolve('multer')
    const stale = [multerEntry, path.join(multerDir, 'lib/make-middleware.js')]
    const saved = stale.map((f) => require.cache[f])
    stale.forEach((f) => delete require.cache[f])
    const multer: typeof import('multer') = require('multer')
    restoreBusboy = () => {
      cached.exports = original
      stale.forEach((f, i) => {
        if (saved[i]) require.cache[f] = saved[i]
        else delete require.cache[f]
      })
    }

    const upload: Multer = multer({
      storage: multer.memoryStorage(),
      fileFilter: (_req, _file, cb) => {
        setTimeout(() => cb(new Error('type not allowed')), 5)
      },
    })
    const app = express()
    app.post('/upload', upload.array('files'), (_req, res) => {
      res.json({ ok: true })
    })
    app.use(
      (err: Error, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
        res.status(400).json({ error: err.message })
      },
    )
    server = app.listen(0, '127.0.0.1', () => {
      port = (server.address() as AddressInfo).port
      done()
    })
  })

  after((done) => {
    restoreBusboy()
    server.close(() => done())
  })

  it('swallows a busboy error that arrives after the request was torn down', async () => {
    const body =
      `--${BOUNDARY}\r\n` +
      'Content-Disposition: form-data; name="files"; filename="bad.txt"\r\n' +
      'Content-Type: text/plain\r\n\r\nx\r\n' +
      `--${BOUNDARY}--\r\n`

    const status = await new Promise<number>((resolve, reject) => {
      const req = http.request(
        {
          host: '127.0.0.1',
          port,
          path: '/upload',
          method: 'POST',
          headers: {
            'Content-Type': `multipart/form-data; boundary=${BOUNDARY}`,
            'Content-Length': Buffer.byteLength(body),
          },
        },
        (res) => {
          res.resume()
          res.on('end', () => resolve(res.statusCode ?? 0))
        },
      )
      req.on('error', reject)
      req.end(body)
    })
    expect(status).to.equal(400)

    await new Promise((r) => setTimeout(r, 300))
    expect(busboy, 'busboy instance captured').to.not.equal(undefined)
    expect(busboy!.listenerCount('error')).to.be.greaterThan(0)
    expect(() => busboy!.emit('error', new Error('late busboy error after teardown'))).to.not.throw()
  })
})
