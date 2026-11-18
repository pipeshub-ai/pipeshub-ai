import 'reflect-metadata'
import { expect } from 'chai'
import express from 'express'
import fs from 'fs'
import http from 'http'
import os from 'os'
import path from 'path'
import { serveFileFromLocalStorage, parseByteRange } from '../../../src/modules/storage/utils/utils'
import { PreconditionFailedError } from '../../../src/libs/errors/http.errors'

/**
 * Bound, Range-capable reads of a local stored file: the bytes of one range of the open file, a 416 with
 * the length for a range past the end, a 412 for a file that is not the one the caller verified, and a
 * streamed body whose memory does not grow with the file.
 */
describe('local stored file: range reads', () => {
  let dir: string
  let file: string
  const body = Buffer.from(Array.from({ length: 4096 }, (_v, i) => i % 251))

  before(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), 'range-read-'))
    file = path.join(dir, 'a.bin')
    fs.writeFileSync(file, body)
  })
  after(() => fs.rmSync(dir, { recursive: true, force: true }))

  const doc = (p = file): any => ({ documentName: 'a', extension: '.bin', local: { localPath: p } })

  async function get(headers: Record<string, string>, query = '', p = file) {
    const app = express()
    app.get('/d', (req, res, next) => {
      try {
        const ifMatch = typeof req.query.ifMatch === 'string' ? req.query.ifMatch : undefined
        serveFileFromLocalStorage(doc(p), res, undefined, ifMatch, req.headers.range)
      } catch (e) {
        next(e)
      }
    })
    // Express recognises an error handler by its four parameters.
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    app.use((err: any, _req: any, res: any, _next: any) => res.status(err instanceof PreconditionFailedError ? 412 : 500).end())
    const server = http.createServer((req, res) => void app(req, res))
    await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()))
    const port = (server.address() as any).port
    try {
      return await new Promise<{ status: number; headers: http.IncomingHttpHeaders; body: Buffer }>((resolve, reject) => {
        http.get({ host: '127.0.0.1', port, path: `/d${query}`, headers }, (res) => {
          const chunks: Buffer[] = []
          res.on('data', (c) => chunks.push(c))
          res.on('end', () => resolve({ status: res.statusCode!, headers: res.headers, body: Buffer.concat(chunks) }))
        }).on('error', reject)
      })
    } finally {
      server.close()
    }
  }

  it('a closed range answers 206 with exactly those bytes, the length and the file tag', async () => {
    const res = await get({ range: 'bytes=10-109' })
    expect(res.status).to.equal(206)
    expect(res.body.equals(body.subarray(10, 110))).to.equal(true)
    expect(res.headers['content-range']).to.equal('bytes 10-109/4096')
    expect(res.headers['content-length']).to.equal('100')
    expect(res.headers['etag']).to.match(/^"/)
  })

  it('an open-ended range reads to the end, and an end past the file is clamped', async () => {
    const open = await get({ range: 'bytes=4000-' })
    expect(open.status).to.equal(206)
    expect(open.body.equals(body.subarray(4000))).to.equal(true)
    const clamped = await get({ range: 'bytes=4000-999999' })
    expect(clamped.headers['content-range']).to.equal('bytes 4000-4095/4096')
  })

  it('a range past the end answers 416 with the length and no body', async () => {
    const res = await get({ range: 'bytes=4096-4200' })
    expect(res.status).to.equal(416)
    expect(res.headers['content-range']).to.equal('bytes */4096')
    expect(res.body.length).to.equal(0)
  })

  it('no range, a multi-range and a malformed range all serve the whole file', async () => {
    for (const range of [undefined, 'bytes=0-1,5-9', 'bytes=abc', 'bytes=9-3', 'items=0-5']) {
      const res = await get(range ? { range } : {})
      expect(res.status, String(range)).to.equal(200)
      expect(res.body.length).to.equal(4096)
    }
  })

  it('a read bound to another file identity answers 412 before any byte', async () => {
    const first = await get({})
    const tag = String(first.headers['etag'])
    const ok = await get({ range: 'bytes=0-9' }, `?ifMatch=${encodeURIComponent(tag)}`)
    expect(ok.status).to.equal(206)
    const stale = await get({ range: 'bytes=0-9' }, `?ifMatch=${encodeURIComponent('"other"')}`)
    expect(stale.status).to.equal(412)
    expect(stale.body.length).to.equal(0)
  })

  it('parseByteRange reads suffixes and refuses a zero-length suffix', () => {
    expect(parseByteRange('bytes=-100', 4096)).to.deep.equal({ start: 3996, end: 4095 })
    expect(parseByteRange('bytes=-100000', 4096)).to.deep.equal({ start: 0, end: 4095 })
    expect(parseByteRange('bytes=-0', 4096)).to.equal('unsatisfiable')
    expect(parseByteRange('bytes=0-0', 0)).to.equal('unsatisfiable')
  })

  it('a 512 MiB body is streamed: the server never holds more than a few chunks of it', async function () {
    this.timeout(60000)
    const big = path.join(dir, 'big.bin')
    const fd = fs.openSync(big, 'w')
    fs.ftruncateSync(fd, 512 * 1024 * 1024)
    fs.closeSync(fd)
    let serverRes: http.ServerResponse | undefined
    const app = express()
    app.get('/d', (_req, res) => {
      serverRes = res
      serveFileFromLocalStorage(doc(big), res)
    })
    const server = http.createServer((req, res) => void app(req, res))
    await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()))
    const port = (server.address() as any).port
    let buffered = 0
    let received = 0
    await new Promise<void>((resolve, reject) => {
      http.get({ host: '127.0.0.1', port, path: '/d' }, (res) => {
        res.on('data', (c: Buffer) => {
          received += c.length
          buffered = Math.max(buffered, serverRes?.writableLength ?? 0)
        })
        res.on('end', resolve)
      }).on('error', reject)
    })
    server.close()
    expect(received).to.equal(512 * 1024 * 1024)
    // Process size drifts with the other tests in a worker; what is held for the client does not.
    expect(buffered).to.be.lessThan(8 * 1024 * 1024)
  })
})
