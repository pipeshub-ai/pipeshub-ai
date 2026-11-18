import 'reflect-metadata'
import { expect } from 'chai'
import express from 'express'
import fs from 'fs'
import http from 'http'
import os from 'os'
import path from 'path'
import { getRecordBuffer } from '../../../src/modules/knowledge_base/controllers/kb_controllers'
import { serveFileFromLocalStorage } from '../../../src/modules/storage/utils/utils'
import { StorageController } from '../../../src/modules/storage/controllers/storage.controller'

/**
 * release-on-close on every relay path (record stream proxy, local file read, opened-handle
 * read), with the client leaving before the upstream headers, at the headers, mid-body and after the end.
 */
const SIZE = 64 * 1024 * 1024
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

/** Requests `path` on `port` and leaves as told. Resolves with what the client saw. */
function client(port: number, p: string, leave: Leave, headers: Record<string, string> = {}, leaveAfterMs = 150) {
  return new Promise<{ status?: number; bytes: number; ended: boolean }>((resolve) => {
    let bytes = 0
    let status: number | undefined
    const req = http.get({ host: '127.0.0.1', port, path: p, headers: { authorization: 'Bearer t', ...headers } }, (res) => {
      status = res.statusCode
      if (leave === 'at-headers') {
        req.destroy()
        return resolve({ status, bytes, ended: false })
      }
      res.on('data', (c: Buffer) => {
        bytes += c.length
        if (leave === 'mid-body' && bytes >= 1024 * 1024) {
          req.destroy()
          resolve({ status, bytes, ended: false })
        }
      })
      res.on('end', () => resolve({ status, bytes, ended: true }))
    })
    req.on('error', () => undefined)
    if (leave === 'before-headers') {
      setTimeout(() => {
        req.destroy()
        resolve({ status, bytes, ended: false })
      }, leaveAfterMs)
    }
  })
}

describe('getRecordBuffer releases the upstream read on every way out', function () {
  this.timeout(30000)

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
    return listen((req, res) => void a(req, res))
  }

  function bigUpstream(status: 200 | 206, headerDelayMs = 0, size = SIZE) {
    const st = { closed: false, sent: 0, finished: false }
    const up = listen((_req, res) => {
      res.on('close', () => {
        st.closed = true
      })
      setTimeout(() => {
        res.writeHead(status, {
          'content-type': 'application/pdf',
          'content-length': String(size),
          ...(status === 206 ? { 'content-range': `bytes 0-${size - 1}/${size * 2}` } : {}),
          etag: '"t1"',
        })
        const chunk = Buffer.alloc(64 * 1024, 1)
        const pump = () => {
          while (st.sent < size) {
            st.sent += chunk.length
            if (!res.write(chunk)) {
              res.once('drain', pump)
              return
            }
          }
          res.end(() => {
            st.finished = true
          })
        }
        pump()
      }, headerDelayMs)
    })
    return { st, up }
  }

  const leaves: Array<[Leave, 200 | 206]> = [
    ['at-headers', 200],
    ['mid-body', 200],
    ['mid-body', 206],
  ]
  for (const [leave, status] of leaves) {
    it(`a client that leaves ${leave} of a ${status} releases the upstream read within 3 s`, async () => {
      const { st, up } = bigUpstream(status)
      const u = await up
      const gw = await gateway(`http://127.0.0.1:${u.port}`)
      try {
        const seen = await client(gw.port, '/stream/r1', leave)
        expect(seen.status).to.equal(status)
        const t0 = Date.now()
        while (!st.closed && Date.now() - t0 < 3000) await tick(25)
        expect(st.closed, `upstream open after the client left (${st.sent} of ${SIZE} sent)`).to.equal(true)
        expect(st.sent).to.be.lessThan(SIZE)
      } finally {
        close(gw.server, u.server)
      }
    })
  }

  it('a client that leaves BEFORE the upstream answers (a slow conversion) releases the upstream read within 3 s of its answer', async () => {
    const { st, up } = bigUpstream(200, 600)
    const u = await up
    const gw = await gateway(`http://127.0.0.1:${u.port}`)
    try {
      const seen = await client(gw.port, '/stream/r1', 'before-headers', {}, 150)
      expect(seen.status).to.equal(undefined)
      const t0 = Date.now()
      while (!st.closed && Date.now() - t0 < 3600) await tick(25)
      expect(st.closed, `upstream open ${Date.now() - t0} ms after the client left (${st.sent} of ${SIZE} sent)`).to.equal(true)
    } finally {
      close(gw.server, u.server)
    }
  })

  it('a client that reads to the end gets every byte and the upstream ends normally', async () => {
    const small = 3 * 1024 * 1024
    const { st, up } = bigUpstream(200, 0, small)
    const u = await up
    const gw = await gateway(`http://127.0.0.1:${u.port}`)
    try {
      const seen = await client(gw.port, '/stream/r1', 'after-end')
      expect(seen).to.deep.equal({ status: 200, bytes: small, ended: true })
      await tick(100)
      expect(st.finished).to.equal(true)
    } finally {
      close(gw.server, u.server)
    }
  })
})

describe('local file reads close the descriptor on every way out', function () {
  this.timeout(30000)
  let dir: string
  let file: string
  before(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), 'qa-r3-'))
    file = path.join(dir, 'big.pdf')
    fs.writeFileSync(file, '')
    fs.truncateSync(file, SIZE)
  })
  after(() => fs.rmSync(dir, { recursive: true, force: true }))

  // Descriptor accounting reads /proc, a Linux interface. Where it is absent the
  // count is reported as 0 so the status and byte assertions below still run.
  const fdDir = process.env.STREAM_TEST_FD_DIR ?? '/proc/self/fd'
  const haveFdDir = fs.existsSync(fdDir)
  const openFds = () => {
    if (!haveFdDir) return 0
    return fs.readdirSync(fdDir).filter((fd) => {
      try {
        return fs.readlinkSync(`${fdDir}/${fd}`) === file
      } catch {
        return false
      }
    }).length
  }

  const routes: Record<string, (req: express.Request, res: express.Response, next: express.NextFunction) => void> = {
    serveFileFromLocalStorage: (req, res, next) => {
      try {
        serveFileFromLocalStorage({ documentName: 'big', extension: '.pdf', local: { localPath: file } } as any, res, undefined, undefined, req.headers.range)
      } catch (e) {
        next(e)
      }
    },
    sendOpenedFile: (req, res, next) => {
      const c = Object.create(StorageController.prototype)
      void fs.promises.open(file, 'r').then((handle) => c.sendOpenedFile(req, res, next, { handle, size: SIZE }, file))
    },
  }

  for (const [name, route] of Object.entries(routes)) {
    for (const leave of ['at-headers', 'mid-body', 'after-end'] as Leave[]) {
      for (const range of [undefined, 'bytes=1000-40000000']) {
        it(`${name}: a client that leaves ${leave}${range ? ' of a range' : ''} leaves no descriptor open`, async () => {
          const app = express().get('/f', route)
          const s = await listen((req, res) => void app(req, res))
          try {
            expect(openFds()).to.equal(0)
            const seen = await client(s.port, '/f', leave, range ? { range } : {})
            expect(seen.status).to.equal(range ? 206 : 200)
            if (leave === 'after-end') expect(seen.bytes).to.equal(range ? 40000000 - 1000 + 1 : SIZE)
            const t0 = Date.now()
            while (openFds() > 0 && Date.now() - t0 < 3000) await tick(25)
            expect(openFds(), 'descriptors still open on the file').to.equal(0)
          } finally {
            close(s.server)
          }
        })
      }
    }
  }
})
