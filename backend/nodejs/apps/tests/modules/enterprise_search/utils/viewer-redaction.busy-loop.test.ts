import 'reflect-metadata'
import { expect } from 'chai'
import http from 'http'
import type { AddressInfo } from 'net'
import {
  newReadable,
  readableRecordIds,
} from '../../../../src/modules/enterprise_search/utils/viewer-redaction'
import { accessCheckBreaker } from '../../../../src/modules/enterprise_search/utils/access-check-breaker'
import { registerUpstreams } from '../../../../src/libs/http/upstream-call'
import { registerAppUpstreams } from '../../../../src/modules/tokens_manager/config/config'

/**
 * The breaker around the viewer access check opens for a query service that is slow, never for a process too busy
 * to read the answers: a timeout counts against the service only when its request had time to be answered and the
 * event loop was not the delay. A read that times out either way still fails closed for its own rows.
 */

const VIEWER = { userId: 'cccccccccccccccccccccccc', orgId: 'bbbbbbbbbbbbbbbbbbbbbbbb' }
const DEADLINE_MS = 300

/** Keeps the event loop busy, as a burst of other reads' work does. */
const busyFor = (ms: number): void => {
  const until = Date.now() + ms
  while (Date.now() < until) {
    // The process's one thread is taken.
  }
}

interface QueryService {
  appConfig: any
  asked: () => number
  close: () => Promise<void>
}

/** A query service on a local port that answers each access check, every id readable, after `delayMs`. */
const queryService = async (delayMs: number): Promise<QueryService> => {
  let asked = 0
  const held = new Set<NodeJS.Timeout>()
  const server = http.createServer((req, res) => {
    let body = ''
    req.on('data', (chunk) => {
      body += String(chunk)
    })
    req.on('end', () => {
      asked += 1
      const { recordIds = [] } = JSON.parse(body || '{}') as { recordIds?: string[] }
      const timer = setTimeout(() => {
        held.delete(timer)
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ accessible: recordIds, accessibleUrls: [], unmatchedUrls: [], names: {}, deleted: [] }))
      }, delayMs)
      held.add(timer)
    })
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address() as AddressInfo
  registerAppUpstreams({ aiBackend: `http://127.0.0.1:${port}` })
  return {
    appConfig: {
      aiBackend: `http://127.0.0.1:${port}`,
      jwtSecret: 'test-jwt-secret',
      scopedJwtSecret: 'test-scoped-secret',
    },
    asked: () => asked,
    close: () =>
      new Promise<void>((resolve) => {
        for (const timer of held) clearTimeout(timer)
        server.closeAllConnections()
        server.close(() => resolve())
      }),
  }
}

describe('the access check breaker, a slow service and a busy process', () => {
  afterEach(() => registerUpstreams({}))
  let service: QueryService | undefined

  beforeEach(() => accessCheckBreaker.reset())
  afterEach(async () => {
    await service?.close()
    service = undefined
    accessCheckBreaker.reset()
  })

  it('opens for a slow service: five reads that had time, on a free event loop, then the next asks nothing', async () => {
    service = await queryService(2_000)
    for (let i = 0; i < 5; i++) {
      const read = await readableRecordIds(service.appConfig, VIEWER, [`r${i}`], [], undefined, DEADLINE_MS)
      expect(read.failed, 'each read fails closed').to.equal(true)
      expect(read.ids.size).to.equal(0)
    }
    const asked = service.asked()

    const next = await readableRecordIds(service.appConfig, VIEWER, ['r9'], [], undefined, DEADLINE_MS)

    expect(next.failed).to.equal(true)
    expect(service.asked(), 'an open breaker asks nothing').to.equal(asked)
  })

  it('stays shut when a busy event loop, not a healthy service, kept a burst of reads past their deadline', async () => {
    const healthy = await queryService(0)
    service = healthy
    const burst = Array.from({ length: 8 }, (_, i) =>
      readableRecordIds(healthy.appConfig, VIEWER, [`r${i}`], [], undefined, DEADLINE_MS),
    )
    busyFor(DEADLINE_MS + 500)
    const reads = await Promise.all(burst)
    expect(
      reads.map((read) => read.failed),
      'each read past its deadline fails closed for its own rows',
    ).to.deep.equal(Array<boolean>(8).fill(true))
    expect(reads.every((read) => read.ids.size === 0)).to.equal(true)

    const next = await readableRecordIds(healthy.appConfig, VIEWER, ['r9'], [], undefined, DEADLINE_MS)

    expect(next.failed, 'the next read asks, and the service answers').to.equal(false)
    expect(next.ids.has('r9')).to.equal(true)
  })

  it('stays shut when reads reached their check with too little of their deadline left', async () => {
    service = await queryService(2_000)
    for (let i = 0; i < 6; i++) {
      const known = newReadable()
      known.deadline = Date.now() + 200
      const read = await readableRecordIds(service.appConfig, VIEWER, [`r${i}`], [], known, DEADLINE_MS)
      expect(read.failed, 'each read fails closed').to.equal(true)
    }

    expect(accessCheckBreaker.allow()?.probe, 'shut: the next check goes out as usual').to.equal(false)
  })
})
