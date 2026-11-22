/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import net from 'node:net'
import { ListenHub, PgListener } from '../../../src/libs/db/listen'
import { ensurePgRuntime } from '../../../src/libs/db/runtime-boot'
import { configurePg } from '../../../src/libs/db'

/** A LISTEN connection that goes silent is noticed, replaced and re-read. PG_RUNTIME_TEST_URL: the node login of a migrated database. */
const URL = process.env.PG_RUNTIME_TEST_URL ?? ''
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
async function until(cond: () => boolean, ms = 15000): Promise<void> {
  const end = Date.now() + ms
  while (!cond()) {
    if (Date.now() > end) throw new Error('condition not reached in time')
    await sleep(25)
  }
}

(URL ? describe : describe.skip)('LISTEN keepalive', function () {
  this.timeout(60000)
  before(() => { process.env.PG_NODE_URL = URL; process.env.PG_POOLER = 'none'; process.env.PG_LISTEN_PING_S = '0.4'; process.env.PG_LISTEN_PING_TIMEOUT_S = '0.6' })
  after(() => { configurePg(undefined); delete process.env.PG_LISTEN_PING_S; delete process.env.PG_LISTEN_PING_TIMEOUT_S })

  it('holds no connection when the last subscriber leaves while it is being made', async () => {
    const hub = ListenHub.forUrl(URL)
    const internals = hub as unknown as { subs: Set<unknown>; doConnect(): Promise<void> }
    internals.subs.add({ channel: 'pipeshub_kv_changed', onNotify: () => undefined })
    const connecting = internals.doConnect()
    internals.subs.clear()
    await connecting
    expect(hub.connections).to.equal(0)
  })

  it('a pending retry does not replace a connection that an on-demand connect already made', async () => {
    const hub = ListenHub.forUrl(URL)
    const internals = hub as unknown as { client: unknown; subs: Set<unknown>; doConnect(): Promise<void> }
    const live = { sentinel: true }
    internals.client = live
    internals.subs.add({ channel: 'pipeshub_kv_changed', onNotify: () => undefined })
    try {
      await internals.doConnect()
      expect(internals.client).to.equal(live)
    } finally {
      internals.client = undefined
      internals.subs.clear()
    }
  })

  it('replaces a connection that stops answering, tells subscribers to re-read, and listens again', async () => {
    const u = new globalThis.URL(URL)
    let frozenBefore = 0
    const sockets: net.Socket[] = []
    const relay = net.createServer((c) => {
      const up = net.connect(Number(u.port), u.hostname)
      const born = Date.now()
      sockets.push(c, up)
      const pipe = (a: net.Socket, b: net.Socket) => a.on('data', (d) => { if (born >= frozenBefore) b.write(d) }).on('error', () => undefined)
      pipe(c, up); pipe(up, c)
    })
    await new Promise<void>((r) => relay.listen(0, '127.0.0.1', r))
    const port = (relay.address() as net.AddressInfo).port
    // everything but the address stays: the password and the query (sslmode ...) of the real URL
    const relayed = new globalThis.URL(URL)
    relayed.hostname = '127.0.0.1'
    relayed.port = String(port)
    const relayUrl = relayed.toString()
    const got: string[] = []
    let reconnects = 0
    const l = new PgListener(relayUrl, 'pipeshub_kv_changed', (p) => got.push(p), () => { reconnects++ })
    try {
      await l.start()
      await until(() => reconnects >= 1)
      const hub = ListenHub.forUrl(relayUrl)
      expect(hub.healthy()).to.equal(true)
      const notify = (text: string) => ensurePgRuntime().withUow({ preOrg: true, write: true }, (tx) => tx.query("/* pipeshub:test.k */ SELECT pg_notify('pipeshub_kv_changed', $1)", [text]))
      await notify('a')
      await until(() => got.includes('a'))
      const before = reconnects
      frozenBefore = Date.now() + 1                                    // the open connection is now a black hole; new ones work
      await until(() => reconnects > before, 20000)
      await notify('b')
      await until(() => got.includes('b'))
      expect(hub.healthy()).to.equal(true)
    } finally {
      await l.stop()
      sockets.forEach((s) => s.destroy())
      relay.close()
    }
  })
})
