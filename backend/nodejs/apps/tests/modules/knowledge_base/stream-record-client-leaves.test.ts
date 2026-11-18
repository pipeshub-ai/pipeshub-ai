import 'reflect-metadata'
import { expect } from 'chai'
import express from 'express'
import http from 'http'
import sinon from 'sinon'
import { getRecordBuffer } from '../../../src/modules/knowledge_base/controllers/kb_controllers'
import { Logger } from '../../../src/libs/services/logger.service'

/**
 * the record stream proxy when the client leaves at every point,
 * with what the gateway logs. The upstream counts its own requests and when each closed.
 *  - before the upstream answers: the upstream request must close when the client leaves, not when the upstream answers
 *  - at the headers / mid-body: closed, and a client leaving on purpose is not logged at ERROR
 *  - after the end: nothing aborted, nothing logged
 *  - before the answer, the upstream then answers 403 / 304: no crash, no write after the client left
 *  - 100 clients leave before the answer at once: every upstream request closes
 */
const SIZE = 32 * 1024 * 1024
const tick = (ms: number) => new Promise((r) => setTimeout(r, ms))

function listen(handler: http.RequestListener) {
  const server = http.createServer(handler)
  return new Promise<{ server: http.Server; port: number }>((r) =>
    server.listen(0, '127.0.0.1', () => r({ server, port: (server.address() as any).port })),
  )
}
function close(...servers: http.Server[]) {
  for (const s of servers) {
    s.closeAllConnections?.()
    s.close()
  }
}

type Leave = 'before-headers' | 'at-headers' | 'mid-body' | 'after-end'
function client(port: number, leave: Leave, leaveAfterMs = 150) {
  return new Promise<{ status?: number; bytes: number; ended: boolean; leftAt: number }>((resolve) => {
    let bytes = 0
    let status: number | undefined
    const req = http.get({ host: '127.0.0.1', port, path: '/stream/r1', headers: { authorization: 'Bearer t' } }, (res) => {
      status = res.statusCode
      if (leave === 'at-headers') {
        req.destroy()
        return resolve({ status, bytes, ended: false, leftAt: Date.now() })
      }
      res.on('data', (c: Buffer) => {
        bytes += c.length
        if (leave === 'mid-body' && bytes >= 1024 * 1024) {
          req.destroy()
          resolve({ status, bytes, ended: false, leftAt: Date.now() })
        }
      })
      res.on('end', () => resolve({ status, bytes, ended: true, leftAt: Date.now() }))
    })
    req.on('error', () => undefined)
    if (leave === 'before-headers') {
      setTimeout(() => {
        req.destroy()
        resolve({ status, bytes, ended: false, leftAt: Date.now() })
      }, leaveAfterMs)
    }
  })
}

function gateway(connectorUrl: string) {
  const a = express()
  a.get(
    '/stream/:recordId',
    (req: any, _res, next) => {
      req.user = { userId: 'u1', orgId: 'o1' }
      req.context = { requestId: 'q' }
      next()
    },
    getRecordBuffer(connectorUrl) as any,
  )
  const errors: unknown[] = []
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  a.use((err: unknown, _req: any, res: any, _next: any) => {
    errors.push(err)
    if (!res.headersSent) res.status(500).end()
  })
  return listen((req, res) => void a(req, res)).then((s) => ({ ...s, errors }))
}

/** An upstream that answers `status` after `headerDelayMs` and records when each request closed. */
function upstream(status: number, headerDelayMs = 0, size = SIZE) {
  const st = { requests: 0, closedAt: [] as number[], answeredAt: [] as number[], sent: 0, finished: 0 }
  const up = listen((req, res) => {
    st.requests += 1
    req.on('close', () => undefined)
    res.on('close', () => st.closedAt.push(Date.now()))
    setTimeout(() => {
      if (res.destroyed) return
      st.answeredAt.push(Date.now())
      if (status === 304) {
        res.writeHead(304, { etag: '"t1"' })
        return res.end()
      }
      if (status >= 400) {
        res.writeHead(status, { 'content-type': 'application/json' })
        return res.end(JSON.stringify({ detail: 'refused' }))
      }
      res.writeHead(status, { 'content-type': 'application/pdf', 'content-length': String(size), etag: '"t1"' })
      const chunk = Buffer.alloc(64 * 1024, 1)
      let sent = 0
      const pump = () => {
        while (sent < size) {
          sent += chunk.length
          st.sent += chunk.length
          if (!res.write(chunk)) {
            res.once('drain', pump)
            return
          }
        }
        res.end(() => {
          st.finished += 1
        })
      }
      pump()
    }, headerDelayMs)
  })
  return { st, up }
}

describe('the record stream proxy when the client leaves', function () {
  this.timeout(30000)
  let errorLog: sinon.SinonStub
  let unhandled: unknown[]
  const onUnhandled = (e: unknown) => unhandled.push(e)
  beforeEach(() => {
    errorLog = sinon.stub(Logger.prototype, 'error')
    unhandled = []
    process.on('unhandledRejection', onUnhandled)
  })
  afterEach(() => {
    errorLog.restore()
    process.off('unhandledRejection', onUnhandled)
  })
  // Only the proxy's own logger counts: background workers started elsewhere in the process log on their own.
  const STREAM_LOGGER = 'Knowledge Base Controller'
  const errorLines = () =>
    errorLog
      .getCalls()
      .filter((c) => (c.thisValue as { defaultMeta?: { service?: string } })?.defaultMeta?.service === STREAM_LOGGER)
      .map((c) => `${c.args[0]} ${JSON.stringify(c.args[1] ?? {})}`)

  it('before the upstream answers: the upstream request closes when the client leaves, not when the upstream answers', async () => {
    const { st, up } = upstream(200, 1500)
    const u = await up
    const gw = await gateway(`http://127.0.0.1:${u.port}`)
    try {
      const seen = await client(gw.port, 'before-headers', 150)
      const t0 = Date.now()
      while (st.closedAt.length === 0 && Date.now() - t0 < 3000) await tick(10)
      const lag = (st.closedAt[0] ?? Infinity) - seen.leftAt
      console.log('before-headers: upstream closed', lag, 'ms after the client left; answered', st.answeredAt.length, 'errors', errorLines())
      expect(lag, 'upstream request closed long after the client left').to.be.lessThan(500)
      expect(st.answeredAt, 'the upstream answered a request nobody waits for').to.deep.equal([])
      expect(errorLines(), 'ERROR lines for a client that left').to.deep.equal([])
      expect(gw.errors).to.deep.equal([])
    } finally {
      close(gw.server, u.server)
    }
  })

  for (const leave of ['at-headers', 'mid-body'] as Leave[]) {
    it(`${leave}: the upstream closes and a client leaving on purpose logs no ERROR`, async () => {
      const { st, up } = upstream(200)
      const u = await up
      const gw = await gateway(`http://127.0.0.1:${u.port}`)
      try {
        const seen = await client(gw.port, leave)
        expect(seen.status).to.equal(200)
        const t0 = Date.now()
        while (st.closedAt.length === 0 && Date.now() - t0 < 3000) await tick(10)
        await tick(200)
        console.log(`${leave}: upstream closed`, (st.closedAt[0] ?? Infinity) - seen.leftAt, 'ms after; errors', errorLines())
        expect(st.closedAt.length).to.equal(1)
        expect(st.finished).to.equal(0)
        expect(errorLines(), 'ERROR lines for a client that left').to.deep.equal([])
        expect(unhandled).to.deep.equal([])
      } finally {
        close(gw.server, u.server)
      }
    })
  }

  it('after the end: every byte, the upstream finishes, nothing aborted or logged', async () => {
    const { st, up } = upstream(200, 0, 2 * 1024 * 1024)
    const u = await up
    const gw = await gateway(`http://127.0.0.1:${u.port}`)
    try {
      const seen = await client(gw.port, 'after-end')
      expect(seen).to.include({ status: 200, bytes: 2 * 1024 * 1024, ended: true })
      await tick(200)
      expect(st.finished).to.equal(1)
      expect(errorLines()).to.deep.equal([])
    } finally {
      close(gw.server, u.server)
    }
  })

  for (const status of [403, 304, 206]) {
    it(`before the answer, then the upstream answers ${status}: no crash, no error handler, no unhandled rejection`, async () => {
      const { st, up } = upstream(status, 400)
      const u = await up
      const gw = await gateway(`http://127.0.0.1:${u.port}`)
      try {
        await client(gw.port, 'before-headers', 100)
        await tick(900)
        console.log(`leave-then-${status}: answered`, st.answeredAt.length, 'closed', st.closedAt.length, 'errors', errorLines())
        expect(gw.errors).to.deep.equal([])
        expect(unhandled).to.deep.equal([])
        expect(st.closedAt.length).to.equal(1)
      } finally {
        close(gw.server, u.server)
      }
    })
  }

  it('the client leaves while an earlier async middleware (auth) still runs: the upstream is never asked or closes at once', async () => {
    const { st, up } = upstream(200, 1500)
    const u = await up
    const a = express()
    a.get(
      '/stream/:recordId',
      (req: any, _res, next) => {
        req.user = { userId: 'u1', orgId: 'o1' }
        req.context = { requestId: 'q' }
        setTimeout(next, 400)
      },
      getRecordBuffer(`http://127.0.0.1:${u.port}`) as any,
    )
    const gw = await listen((req, res) => void a(req, res))
    try {
      const seen = await client(gw.port, 'before-headers', 100)
      await tick(800)
      const open = st.requests - st.closedAt.length
      console.log('leave-in-middleware: upstream requests', st.requests, 'still open', open, 'left at +', Date.now() - seen.leftAt)
      expect(open, 'an upstream request is open for a client that left before the handler ran').to.equal(0)
    } finally {
      close(gw.server, u.server)
    }
  })

  it('100 clients leave before the answer at once: every upstream request closes before it is answered', async () => {
    const { st, up } = upstream(200, 2000, 1024 * 1024)
    const u = await up
    const gw = await gateway(`http://127.0.0.1:${u.port}`)
    try {
      await Promise.all(Array.from({ length: 100 }, () => client(gw.port, 'before-headers', 300)))
      const t0 = Date.now()
      while (st.closedAt.length < st.requests && Date.now() - t0 < 1500) await tick(20)
      console.log('100 leave: requests', st.requests, 'closed', st.closedAt.length, 'answered', st.answeredAt.length)
      expect(st.requests).to.equal(100)
      expect(st.closedAt.length).to.equal(100)
      expect(st.answeredAt.length).to.equal(0)
    } finally {
      close(gw.server, u.server)
    }
  })
})
