import 'reflect-metadata'
import { expect } from 'chai'
import express from 'express'
import http from 'http'
import { getRecordBuffer } from '../../../src/modules/knowledge_base/controllers/kb_controllers'

/**
 * The stream proxy between a REAL Express app and a REAL upstream HTTP server.
 * (1) 206 and 416 are relayed byte-exact with their headers; (2) a client that leaves after the headers
 * (the cache's abort at headers, the range transport's abort on a 200) releases the upstream read.
 */
const SIZE = 64 * 1024 * 1024

function upstream(handler: http.RequestListener) {
  const server = http.createServer(handler)
  return new Promise<{ server: http.Server; port: number }>((r) =>
    server.listen(0, '127.0.0.1', () => r({ server, port: (server.address() as any).port })),
  )
}

function gateway(connectorUrl: string) {
  const a = express()
  a.get('/stream/:recordId', (req: any, _res, next) => {
    req.user = { userId: 'u1', orgId: 'o1' }
    req.context = { requestId: 'q' }
    next()
  }, getRecordBuffer(connectorUrl) as any)
  const server = http.createServer((req, res) => void a(req, res))
  return new Promise<{ server: http.Server; port: number }>((r) =>
    server.listen(0, '127.0.0.1', () => r({ server, port: (server.address() as any).port })),
  )
}

describe('stream proxy against a real upstream', function () {
  this.timeout(20000)

  it('relays a 206 byte-exact with Content-Range, Content-Length, ETag and Accept-Ranges', async () => {
    let seenRange: string | undefined
    let seenIfRange: string | undefined
    const up = await upstream((req, res) => {
      seenRange = req.headers['range'] as string
      seenIfRange = req.headers['if-range'] as string
      const body = Buffer.alloc(1024, 7)
      res.writeHead(206, {
        'content-type': 'application/pdf',
        'content-range': `bytes 100-1123/${SIZE}`,
        'content-length': '1024',
        etag: '"t1"',
        'accept-ranges': 'bytes',
        'cache-control': 'private, no-store',
      })
      res.end(body)
    })
    const gw = await gateway(`http://127.0.0.1:${up.port}`)
    try {
      const out = await new Promise<{ status: number; headers: http.IncomingHttpHeaders; bytes: number }>((resolve, reject) => {
        http.get({ host: '127.0.0.1', port: gw.port, path: '/stream/r1', headers: { authorization: 'Bearer t', range: 'bytes=100-1123', 'if-range': '"t1"', 'x-record-content-open': '1' } }, (res) => {
          let n = 0
          res.on('data', (c) => (n += (c as Buffer).length))
          res.on('end', () => resolve({ status: res.statusCode!, headers: res.headers, bytes: n }))
        }).on('error', reject)
      })
      expect({ seenRange, seenIfRange }).to.deep.equal({ seenRange: 'bytes=100-1123', seenIfRange: '"t1"' })
      expect(out.status).to.equal(206)
      expect(out.bytes).to.equal(1024)
      expect(out.headers['content-range']).to.equal(`bytes 100-1123/${SIZE}`)
      expect(out.headers['content-length']).to.equal('1024')
      expect(out.headers['etag']).to.equal('"t1"')
      expect(out.headers['accept-ranges']).to.equal('bytes')
    } finally {
      gw.server.close(); up.server.close()
    }
  })

  it('relays a 416 with Content-Range, no ETag, no-store', async () => {
    const up = await upstream((_req, res) => {
      res.writeHead(416, { 'content-type': 'application/json', 'content-range': `bytes */${SIZE}`, 'cache-control': 'no-store' })
      res.end(JSON.stringify({ detail: 'Requested range not satisfiable' }))
    })
    const gw = await gateway(`http://127.0.0.1:${up.port}`)
    try {
      const out = await new Promise<{ status: number; headers: http.IncomingHttpHeaders }>((resolve, reject) => {
        http.get({ host: '127.0.0.1', port: gw.port, path: '/stream/r1', headers: { authorization: 'Bearer t', range: `bytes=${SIZE}-` } }, (res) => {
          res.resume(); res.on('end', () => resolve({ status: res.statusCode!, headers: res.headers }))
        }).on('error', reject)
      })
      expect(out.status).to.equal(416)
      expect(out.headers['content-range']).to.equal(`bytes */${SIZE}`)
      expect(out.headers['etag']).to.equal(undefined)
      expect(out.headers['cache-control']).to.equal('no-store')
    } finally {
      gw.server.close(); up.server.close()
    }
  })

  it('a client that leaves after the headers releases the upstream read within 3 s', async () => {
    let upstreamClosed = false
    let upstreamSent = 0
    const up = await upstream((_req, res) => {
      res.writeHead(200, { 'content-type': 'application/pdf', 'content-length': String(SIZE), etag: '"t1"' })
      const chunk = Buffer.alloc(64 * 1024, 1)
      const pump = () => {
        while (upstreamSent < SIZE) {
          upstreamSent += chunk.length
          if (!res.write(chunk)) { res.once('drain', pump); return }
        }
        res.end()
      }
      res.on('close', () => { upstreamClosed = true })
      pump()
    })
    const gw = await gateway(`http://127.0.0.1:${up.port}`)
    try {
      await new Promise<void>((resolve, reject) => {
        const req = http.get({ host: '127.0.0.1', port: gw.port, path: '/stream/r1', headers: { authorization: 'Bearer t' } }, (res) => {
          expect(res.statusCode).to.equal(200)
          req.destroy() // the client's abort at headers
          resolve()
        })
        req.on('error', () => {})
        setTimeout(() => reject(new Error('no headers')), 5000)
      })
      const t0 = Date.now()
      while (!upstreamClosed && Date.now() - t0 < 3000) await new Promise((r) => setTimeout(r, 50))
      expect(upstreamClosed, 'upstream read still open 3 s after the client left').to.equal(true)
    } finally {
      gw.server.closeAllConnections?.(); up.server.closeAllConnections?.()
      gw.server.close(); up.server.close()
    }
  })
})
