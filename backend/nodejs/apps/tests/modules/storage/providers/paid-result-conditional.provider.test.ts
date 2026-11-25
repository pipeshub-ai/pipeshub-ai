import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import fs from 'fs'
import fsp from 'fs/promises'
import os from 'os'
import path from 'path'
import { createHash } from 'crypto'
import AmazonS3Adapter from '../../../../src/modules/storage/providers/s3.provider'
import AzureBlobStorageAdapter from '../../../../src/modules/storage/providers/azure.provider'
import LocalStorageAdapter from '../../../../src/modules/storage/providers/local-storage.provider'
import { StorageUploadError } from '../../../../src/libs/errors/storage.errors'

const ROOT = 'org1/PipesHub/paid-results/rec-1'
/** This process's boot and PID namespace, as a lock names its owner's: a pid is judged only inside the same ones. */
const identity = (): { boot?: string; pidNs?: string } => {
  try {
    return { boot: fs.readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim(), pidNs: fs.readlinkSync('/proc/self/ns/pid') }
  } catch {
    return {}
  }
}
const key = (n: number): string => `${ROOT}/ocr_page/${n.toString(16).padStart(64, '0')}.json`

describe('Conditional delete, version bump and capability probe of the paid-result stores', () => {
  afterEach(() => sinon.restore())

  describe('local storage', () => {
    let home: string
    beforeEach(() => {
      home = fs.mkdtempSync(path.join(os.tmpdir(), 'paid-result-conditional-'))
      sinon.stub(os, 'homedir').returns(home)
    })
    afterEach(() => fs.rmSync(home, { recursive: true, force: true }))
    const adapter = () => new LocalStorageAdapter({ mountName: 'PipesHub', baseUrl: 'http://localhost:3000' } as any)
    const etagOf = async (a: LocalStorageAdapter, k: string): Promise<string> =>
      (await a.listObjects(`${ROOT}/`, { limit: 100 })).objects.find((o) => o.key === k)!.etag!
    const locks = (a: LocalStorageAdapter): string[] =>
      (fs.readdirSync(path.join((a as any).mountPath, ROOT), { recursive: true }) as string[]).filter((f) => f.endsWith('.lock'))

    it('lists an etag per file, which moves when the file is replaced even with equal size and the same bytes', async () => {
      const a = adapter()
      await a.writeObject(key(1), Buffer.from('aaaa'))
      const e1 = await etagOf(a, key(1))
      await a.writeObject(key(1), Buffer.from('aaaa'))
      expect(await etagOf(a, key(1))).to.not.equal(e1)
    })

    it('deleteObjectsIfMatch removes a file at the listed etag, keeps one that changed, and counts a missing one as done', async () => {
      const a = adapter()
      for (const n of [1, 2]) await a.writeObject(key(n), Buffer.from(`v${n}`))
      const [e1, e2] = [await etagOf(a, key(1)), await etagOf(a, key(2))]
      await a.writeObject(key(2), Buffer.from('v2 again'))

      const r = await a.deleteObjectsIfMatch([
        { key: key(1), etag: e1 },
        { key: key(2), etag: e2 },
        { key: key(3), etag: 'x' },
      ])

      expect(r.changed).to.deep.equal([key(2)])
      const left = (await a.listObjects(`${ROOT}/`, { limit: 10 })).objects.map((o) => o.key)
      expect(left).to.deep.equal([key(2)])
    })

    it('never lists a lock file, and leaves none behind after a write, an if-absent write, a bump or a delete', async () => {
      const a = adapter()
      await a.writeObject(key(1), Buffer.from('a'))
      await a.writeObjectIfAbsent(key(2), Buffer.from('b'))
      await a.writeObjectIfMatch(key(1), Buffer.from('a2'), await etagOf(a, key(1)))
      await a.deleteObjectsIfMatch([{ key: key(2), etag: await etagOf(a, key(2)) }])
      expect(locks(a)).to.deep.equal([])
      fs.writeFileSync(path.join((a as any).mountPath, path.dirname(key(1)), '.0123.lock'), 'x')
      expect((await a.listObjects(`${ROOT}/`, { limit: 10 })).objects.map((o) => o.key)).to.deep.equal([key(1)])
    })

    it('writeObjectIfMatch answers written, gone or changed, and a bump gives the file a new etag', async () => {
      const a = adapter()
      await a.writeObject(key(1), Buffer.from('one'))
      const e = await etagOf(a, key(1))
      expect(await a.writeObjectIfMatch(key(1), Buffer.from('one, touched'), 'not-it')).to.equal('changed')
      expect(await a.writeObjectIfMatch(key(1), Buffer.from('one, touched'), e)).to.equal('written')
      expect(await etagOf(a, key(1))).to.not.equal(e)
      expect(await a.writeObjectIfMatch(key(2), Buffer.from('x'), e)).to.equal('gone')
    })

    it('a write that fails part way leaves the original file whole and releases the lock', async () => {
      const a = adapter()
      await a.writeObject(key(1), Buffer.from('original'))
      const e = await etagOf(a, key(1))
      sinon.stub(a as any, 'writeFileAtomically').rejects(new Error('disk full'))
      let thrown: unknown
      try { await a.writeObjectIfMatch(key(1), Buffer.from('new'), e) } catch (err) { thrown = err }
      expect(thrown).to.be.instanceOf(StorageUploadError)
      sinon.restore()
      sinon.stub(os, 'homedir').returns(home)
      const opened = await a.openObject(key(1))
      expect((await opened!.handle.readFile()).toString()).to.equal('original')
      await opened!.handle.close()
      expect(locks(a)).to.deep.equal([])
    })

    it('a lock older than its lease is taken over, and a fresh one is waited for', async () => {
      const a = adapter()
      await a.writeObject(key(1), Buffer.from('one'))
      const dir = path.join((a as any).mountPath, path.dirname(key(1)))
      const name = `.${createHash('sha1').update(path.basename(key(1))).digest('hex')}.lock`
      fs.writeFileSync(path.join(dir, name), 'crashed holder')
      const old = new Date(Date.now() - 6 * 60 * 1000)
      fs.utimesSync(path.join(dir, name), old, old)
      await a.writeObject(key(1), Buffer.from('after the takeover'))
      expect(locks(a)).to.deep.equal([])

      fs.writeFileSync(path.join(dir, name), 'live holder')
      setTimeout(() => fs.rmSync(path.join(dir, name), { force: true }), 80)
      const started = Date.now()
      await a.writeObject(key(1), Buffer.from('after the wait'))
      expect(Date.now() - started).to.be.greaterThan(60)
    })

    it('two writers that both find a dead owner\'s lock enter one at a time, and the second never moves the first\'s fresh lock', async () => {
      const a = adapter()
      await a.writeObject(key(1), Buffer.from('one'))
      const dir = path.join((a as any).mountPath, path.dirname(key(1)))
      const name = `.${createHash('sha1').update(path.basename(key(1))).digest('hex')}.lock`
      fs.writeFileSync(path.join(dir, name), JSON.stringify({ token: 'x', pid: 2147483646, host: os.hostname(), ...identity() }))
      let inside = 0
      let most = 0
      const real = (a as any).writeFileAtomically.bind(a)
      sinon.stub(a as any, 'writeFileAtomically').callsFake(async (...args: unknown[]) => {
        inside += 1
        most = Math.max(most, inside)
        await new Promise((resolve) => setTimeout(resolve, 15))
        try { return await real(...args) } finally { inside -= 1 }
      })
      await Promise.all(Array.from({ length: 8 }, (_, i) => a.writeObject(key(1), Buffer.from(`w${i}`))))
      expect(most, 'two writers held the key at once').to.equal(1)
    })

    it('a lock released between its read and its stat is not abandoned, and is not an error', async () => {
      const a = adapter()
      await a.writeObject(key(1), Buffer.from('one'))
      const dir = path.join((a as any).mountPath, path.dirname(key(1)))
      const lock = path.join(dir, '.race.lock')
      fs.writeFileSync(lock, JSON.stringify({ token: 'x', pid: process.pid, host: os.hostname(), ...identity() }))
      const real = fsp.stat.bind(fsp)
      sinon.stub(fsp, 'stat').callsFake((async (p: string, ...rest: unknown[]) => {
        if (p === lock) throw Object.assign(new Error('ENOENT'), { code: 'ENOENT' })
        return (real as any)(p, ...rest)
      }) as never)
      expect(await (a as any).lockIsAbandoned(lock)).to.equal(false)
    })

    it('an I/O failure while reclaiming a lock is a failure of the call, not a wait that never ends', async () => {
      const a = adapter()
      await a.writeObject(key(1), Buffer.from('one'))
      const dir = path.join((a as any).mountPath, path.dirname(key(1)))
      const name = `.${createHash('sha1').update(path.basename(key(1))).digest('hex')}.lock`
      fs.writeFileSync(path.join(dir, name), JSON.stringify({ token: 'x', pid: 2147483646, host: os.hostname(), ...identity() }))
      const real = fsp.rename.bind(fsp)
      sinon.stub(fsp, 'rename').callsFake((async (from: string, to: string) => {
        if (from.endsWith('.lock')) throw Object.assign(new Error('EIO'), { code: 'EIO' })
        return real(from, to)
      }) as never)
      const started = Date.now()
      let thrown: unknown
      try { await a.writeObject(key(1), Buffer.from('x')) } catch (e) { thrown = e }
      expect(thrown).to.be.instanceOf(StorageUploadError)
      // wall-clock-ok: the failure it guards is waiting out the 15 s key-lock wait; 3 s is 5x below that
      expect(Date.now() - started).to.be.lessThan(3000)
    })

    it('a locked section that never finishes fails by its deadline and leaves its lock for the lease', async () => {
      const a = adapter()
      a.callDeadlineMs = 40
      await a.writeObject(key(1), Buffer.from('one'))
      const dir = path.join((a as any).mountPath, path.dirname(key(1)))
      sinon.stub(a as any, 'etagAtPath').callsFake(() => new Promise(() => undefined))
      let outcome: unknown = 'resolved'
      try { await a.deleteObjectsIfMatch([{ key: key(1), etag: 'x' }]) } catch (e) { outcome = e }
      expect(outcome, 'a hung section reported success').to.be.instanceOf(Error)
      expect(fs.readdirSync(dir).some((f) => f.endsWith('.lock')), 'the lock was released under work that may still run').to.equal(true)
      expect(fs.existsSync(path.join((a as any).mountPath, key(1))), 'the object was deleted').to.equal(true)
    })

    it('a locked section that ends just after its deadline releases its lock then, not at the end of the lease', async () => {
      const a = adapter()
      a.callDeadlineMs = 40
      await a.writeObject(key(1), Buffer.from('one'))
      const dir = path.join((a as any).mountPath, path.dirname(key(1)))
      const real = (a as any).etagAtPath.bind(a)
      sinon.stub(a as any, 'etagAtPath').callsFake(async (p: string) => {
        await new Promise((resolve) => setTimeout(resolve, 120))      // past the deadline, but it does finish
        return real(p)
      })
      let outcome: unknown = 'resolved'
      try { await a.deleteObjectsIfMatch([{ key: key(1), etag: 'x' }]) } catch (e) { outcome = e }
      expect(outcome).to.be.instanceOf(Error)
      expect(fs.readdirSync(dir).some((f) => f.endsWith('.lock')), 'released while the section still ran').to.equal(true)
      await new Promise((resolve) => setTimeout(resolve, 250))
      expect(fs.readdirSync(dir).some((f) => f.endsWith('.lock')), 'the lock outlived the section that held it').to.equal(false)
    })

    it('the reclaim guard is exclusive: while another contender holds it, a dead owner\'s lock is left alone', async () => {
      const a = adapter()
      await a.writeObject(key(1), Buffer.from('one'))
      const dir = path.join((a as any).mountPath, path.dirname(key(1)))
      const lockPath = path.join(dir, `.${createHash('sha1').update(path.basename(key(1))).digest('hex')}.lock`)
      fs.writeFileSync(lockPath, JSON.stringify({ token: 'x', pid: 2147483646, host: os.hostname(), ...identity() }))
      fs.writeFileSync(`${lockPath}.reclaim`, 'another contender')            // fresh: it is mid-reclaim
      expect(await (a as any).reclaimAbandonedLock(lockPath)).to.equal(false)
      expect(fs.existsSync(lockPath), 'a contender removed a lock another one was reclaiming').to.equal(true)
    })

    it('a bump whose lock was taken over before its write is refused (changed) and writes nothing', async () => {
      const a = adapter()
      await a.writeObject(key(1), Buffer.from('one'))
      const e = await etagOf(a, key(1))
      const dir = path.join((a as any).mountPath, path.dirname(key(1)))
      const real = (a as any).etagAtPath.bind(a)
      sinon.stub(a as any, 'etagAtPath').callsFake(async (p: string) => {
        const lock = fs.readdirSync(dir).find((f) => f.endsWith('.lock'))!
        fs.writeFileSync(path.join(dir, lock), JSON.stringify({ token: 'a taker' }))
        return real(p)
      })
      expect(await a.writeObjectIfMatch(key(1), Buffer.from('bumped'), e)).to.equal('changed')
      const opened = await a.openObject(key(1))
      expect((await opened!.handle.readFile()).toString()).to.equal('one')
      await opened!.handle.close()
    })

    describe('a holder stalled between its token check and its rename (past the lease)', () => {
      /**
       * Pauses the first rename of a staged file onto the key (that is, after the holder's token check), lets `second`
       * take the expired lock and commit, then lets the first one go. Resolves with what the first call answered.
       */
      const stalledHolder = async (
        a: LocalStorageAdapter,
        first: () => Promise<unknown>,
        second: () => Promise<unknown>,
      ): Promise<unknown> => {
        const realRename = fsp.rename.bind(fsp)
        let reached!: () => void
        const atRename = new Promise<void>((r) => (reached = r))
        let release!: () => void
        const gate = new Promise<void>((r) => (release = r))
        let paused = false
        sinon.stub(fsp, 'rename').callsFake(async (from: any, to: any) => {
          if (!paused && String(from).endsWith('.tmp') && String(to).endsWith('.json')) {
            paused = true
            reached()
            await gate
          }
          return realRename(from, to)
        })
        const dir = path.join((a as any).mountPath, path.dirname(key(1)))
        const firstDone = first().then((v) => v, (e: Error) => e.name)
        await atRename
        // the lease passed while the first writer was stalled
        const lock = fs.readdirSync(dir).find((f) => f.endsWith('.lock'))!
        const old = new Date(Date.now() - 6 * 60 * 1000)
        fs.utimesSync(path.join(dir, lock), old, old)
        await second()
        release()
        return firstDone
      }
      const read = async (a: LocalStorageAdapter): Promise<string> => {
        const o = await a.openObject(key(1))
        const text = (await o!.handle.readFile()).toString()
        await o!.handle.close()
        return text
      }

      it('a bump never replaces the newer write of the writer that took its lock', async () => {
        const a = adapter()
        const b = adapter()
        await a.writeObject(key(1), Buffer.from('{"value":"old"}'))
        const e = await etagOf(a, key(1))
        const answer = await stalledHolder(
          a,
          () => a.writeObjectIfMatch(key(1), Buffer.from('{"value":"old","touchedMs":1}'), e),
          () => b.writeObject(key(1), Buffer.from('{"value":"newer refresh result"}')),
        )
        expect(answer).to.equal('changed')
        expect(await read(a)).to.equal('{"value":"newer refresh result"}')
        const dir = path.join((a as any).mountPath, path.dirname(key(1)))
        expect(fs.readdirSync(dir).filter((f) => f.endsWith('.tmp')), 'a staged file was left behind').to.deep.equal([])
      })

      it('a plain write never replaces the newer write of the writer that took its lock', async () => {
        const a = adapter()
        const b = adapter()
        await a.writeObject(key(1), Buffer.from('old'))
        const answer = await stalledHolder(
          a,
          () => a.writeObject(key(1), Buffer.from('stalled')),
          () => b.writeObject(key(1), Buffer.from('newer')),
        )
        expect(answer).to.equal('StorageUploadError')
        expect(await read(a)).to.equal('newer')
      })

      it('a holder inside its lease still lands its write', async () => {
        const a = adapter()
        await a.writeObject(key(1), Buffer.from('old'))
        const e = await etagOf(a, key(1))
        expect(await a.writeObjectIfMatch(key(1), Buffer.from('bumped'), e)).to.equal('written')
        expect(await read(a)).to.equal('bumped')
      })
    })

    it('a lock whose token is not a uuid never makes a taker remove a file outside the key\'s own folder', async () => {
      const a = adapter()
      await a.writeObject(key(1), Buffer.from('old'))
      const dir = path.join((a as any).mountPath, path.dirname(key(1)))
      const lock = path.join(dir, `.${createHash('sha1').update(path.basename(key(1))).digest('hex')}.lock`)
      for (const [i, prefix] of ['..', '../..', 'x/y'].entries()) {
        // the file a forged token would name through `.<token>.tmp`
        const token = `${prefix}/${'a'.repeat(30)}`
        const victim = path.join(dir, `.${token}.tmp`)
        fs.mkdirSync(path.dirname(victim), { recursive: true })
        fs.writeFileSync(victim, `victim ${i}`)
        fs.writeFileSync(lock, JSON.stringify({ token, pid: 1, host: 'another-host' }))
        const old = new Date(Date.now() - 6 * 60 * 1000)
        fs.utimesSync(lock, old, old)
        await adapter().writeObject(key(1), Buffer.from(`new ${i}`))
        expect(fs.existsSync(victim), `a forged token reached ${victim}`).to.equal(true)
      }
    })

    it('a lock whose owner is in another PID namespace or boot is never judged by its pid: only the lease decides', async function () {
      if (identity().pidNs === undefined) this.skip()
      const a = adapter()
      a.callDeadlineMs = 5000
      await a.writeObject(key(1), Buffer.from('one'))
      const dir = path.join((a as any).mountPath, path.dirname(key(1)))
      const name = `.${createHash('sha1').update(path.basename(key(1))).digest('hex')}.lock`
      for (const other of [{ ...identity(), pidNs: 'pid:[4026539999]' }, { ...identity(), boot: 'another-boot' }, { host: os.hostname() }]) {
        // a pid that exists nowhere here, in a namespace this process does not share: it is not "dead", it is unknown
        fs.writeFileSync(path.join(dir, name), JSON.stringify({ token: 'x', pid: 2147483646, host: os.hostname(), ...other }))
        setTimeout(() => fs.rmSync(path.join(dir, name), { force: true }), 120)
        const started = Date.now()
        await a.writeObject(key(1), Buffer.from('after'))
        expect(Date.now() - started, `a lock of ${JSON.stringify(other)} was taken by its pid`).to.be.greaterThan(80)
      }
      // and the lease still frees it: an old lock of an unknown owner is taken over
      fs.writeFileSync(path.join(dir, name), JSON.stringify({ token: 'x', pid: 2147483646, host: os.hostname(), pidNs: 'pid:[1]' }))
      const old = new Date(Date.now() - 6 * 60 * 1000)
      fs.utimesSync(path.join(dir, name), old, old)
      const t = Date.now()
      await a.writeObject(key(1), Buffer.from('after the lease'))
      // wall-clock-ok: the failure it guards is waiting out the 15 s key-lock wait for a stale lock; 1.5 s is 10x below
      expect(Date.now() - t).to.be.lessThan(1500)
    })

    it('a holder that lost its lease does not delete: the file stays and is reported changed', async () => {
      const a = adapter()
      await a.writeObject(key(1), Buffer.from('one'))
      const e = await etagOf(a, key(1))
      const dir = path.join((a as any).mountPath, path.dirname(key(1)))
      const real = (a as any).etagAtPath.bind(a)
      sinon.stub(a as any, 'etagAtPath').callsFake(async (p: string) => {
        const lock = fs.readdirSync(dir).find((f) => f.endsWith('.lock'))!
        fs.writeFileSync(path.join(dir, lock), 'a taker')
        return real(p)
      })
      const r = await a.deleteObjectsIfMatch([{ key: key(1), etag: e }])
      expect(r.changed).to.deep.equal([key(1)])
      expect((await a.listObjects(`${ROOT}/`, { limit: 10 })).objects).to.have.length(1)
    })

    it('honours conditional operations only on a file system it can name as local', async () => {
      const stat = sinon.stub(fsp, 'statfs')
      LocalStorageAdapter.clearCapabilityMemo()
      stat.resolves({ type: 0xef53 } as never)
      expect(await adapter().conditionalCapabilities()).to.deep.equal({ conditionalDelete: true, bump: true })
      for (const type of [0x6969, 0xff534d42, 0x65735546, 0xc36400, 0x01021994 + 1]) {
        LocalStorageAdapter.clearCapabilityMemo()
        stat.resolves({ type } as never)
        expect(await adapter().conditionalCapabilities(), `0x${type.toString(16)}`).to.deep.equal({ conditionalDelete: false, bump: false })
      }
      LocalStorageAdapter.clearCapabilityMemo()
      stat.rejects(new Error('EIO'))
      expect(await adapter().conditionalCapabilities()).to.deep.equal({ conditionalDelete: false, bump: false })
      // a failed statfs is no answer about the file system: the next call asks again
      stat.resolves({ type: 0xef53 } as never)
      expect(await adapter().conditionalCapabilities()).to.deep.equal({ conditionalDelete: true, bump: true })
    })

    it('says once per process that a mount is not a recognised local file system, however many adapters ask', async () => {
      LocalStorageAdapter.clearCapabilityMemo()
      sinon.stub(fsp, 'statfs').resolves({ type: 0x6969 } as never)
      const warn = sinon.spy()
      for (let i = 0; i < 5; i += 1) {
        const a = adapter()
        ;(a as any).logger = { warn, info: sinon.stub(), error: sinon.stub(), debug: sinon.stub() }
        await a.conditionalCapabilities()
      }
      expect(warn.callCount).to.equal(1)
    })

    it('a lock left by a process that is gone is reclaimed at once; a live owner on this host is waited for until its lease passes, and no longer', async () => {
      const a = adapter()
      await a.writeObject(key(1), Buffer.from('one'))
      const dir = path.join((a as any).mountPath, path.dirname(key(1)))
      const name = `.${createHash('sha1').update(path.basename(key(1))).digest('hex')}.lock`
      const lock = (pid: number): void =>
        fs.writeFileSync(path.join(dir, name), JSON.stringify({ token: 'x', pid, host: os.hostname(), ...identity() }))
      lock(2147483646) // no such process
      const started = Date.now()
      await a.writeObject(key(1), Buffer.from('after the crash'))
      // wall-clock-ok: the failure it guards is waiting the 15 s key-lock wait on a dead owner's lock; 1 s is 15x below
      expect(Date.now() - started, 'a crash-left lock made the writer wait').to.be.lessThan(1000)

      lock(process.pid) // this process: alive. Inside its lease it is waited for.
      const young = new Date(Date.now() - 60 * 1000)
      fs.utimesSync(path.join(dir, name), young, young)
      setTimeout(() => fs.rmSync(path.join(dir, name), { force: true }), 80)
      const t0 = Date.now()
      await a.writeObject(key(1), Buffer.from('after the wait'))
      expect(Date.now() - t0, 'a live owner lost its lock to a lease that is too short').to.be.greaterThan(60)

      lock(process.pid) // past its lease (5 minutes) even a live owner's lock is taken over, so a stuck owner cannot block for ever
      const old = new Date(Date.now() - 6 * 60 * 1000)
      fs.utimesSync(path.join(dir, name), old, old)
      const t1 = Date.now()
      await a.writeObject(key(1), Buffer.from('after the lease'))
      // wall-clock-ok: the failure it guards is waiting the 15 s key-lock wait on an expired lease; 1 s is 15x below
      expect(Date.now() - t1).to.be.lessThan(1000)
    })

    it('a file replaced by a FIFO does not block a conditional operation (and the key lock)', async function () {
      if (process.platform === 'win32') this.skip()
      const a = adapter()
      await a.writeObject(key(1), Buffer.from('one'))
      const full = path.join((a as any).mountPath, key(1))
      fs.rmSync(full)
      require('child_process').execFileSync('mkfifo', [full])
      const started = Date.now()
      const r = await a.deleteObjectsIfMatch([{ key: key(1), etag: 'x' }])
      // wall-clock-ok: the failure it guards is a FIFO open that blocks until mocha's timeout; 2 s only separates that from a return
      expect(Date.now() - started).to.be.lessThan(2000)
      expect(r.changed).to.deep.equal([])
    })

  })

  describe('S3', () => {
    const adapter = (bucket = 'bucket-' + Math.random().toString(36).slice(2)) => {
      const a = new AmazonS3Adapter({ accessKeyId: 'AKIAIOSFODNN7EXAMPLE', secretAccessKey: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY', region: 'us-east-1', bucket })
      a.probeDeniedTtlMs = 0 // a probe that was denied or could not tell is asked again at once, unless a test says otherwise
      a.probeUnsureTtlMs = 0
      return a
    }
    const done = <T>(v: T) => ({ promise: async () => v })
    const fail = (status: number, code?: string) => ({ promise: async () => { throw Object.assign(new Error(code ?? 'x'), { $metadata: { httpStatusCode: status }, code }) } })

    it('lists the etag without quotes, and a signed PUT names the If-Match header the uploader must send', async () => {
      const a = adapter()
      ;(a as any).s3 = {
        listObjectsV2: sinon.stub().returns(done({ Contents: [{ Key: key(1), Size: 3, LastModified: new Date(5), ETag: '"abc123"' }] })),
        getSignedUrlPromise: sinon.stub().resolves('https://s3/put?sig'),
      }
      const page = await a.listObjects(`${ROOT}/`, { limit: 10 })
      expect(page.objects[0]!.etag).to.equal('abc123')
      const signed = await a.signObjectUrl(key(1), { method: 'PUT', expiresInSeconds: 900, ifMatch: 'abc123' })
      expect(signed.headers['If-Match']).to.equal('"abc123"')
      expect((a as any).s3.getSignedUrlPromise.firstCall.args[1].IfMatch).to.equal('"abc123"')
    })

    it('deleteObjectsIfMatch sends each ETag quoted and maps per-object errors: changed kept, missing done, other raised', async () => {
      const a = adapter()
      const del = sinon.stub()
      del.onFirstCall().returns(done({ Errors: [{ Key: key(2), Code: 'PreconditionFailed' }, { Key: key(3), Code: 'NoSuchKey' }, { Key: key(4), Code: 'ConditionalRequestConflict' }] }))
      del.onSecondCall().returns(done({ Errors: [{ Key: key(1), Code: 'AccessDenied', Message: 'no' }] }))
      ;(a as any).s3 = { deleteObjects: del }
      const r = await a.deleteObjectsIfMatch([1, 2, 3, 4].map((n) => ({ key: key(n), etag: `e${n}` })))
      expect(r.changed).to.deep.equal([key(2), key(4)])
      expect(del.firstCall.args[0].Delete.Objects[0]).to.deep.equal({ Key: key(1), ETag: '"e1"' })
      let thrown: unknown
      try { await a.deleteObjectsIfMatch([{ key: key(1), etag: 'e1' }]) } catch (e) { thrown = e }
      expect(thrown).to.be.instanceOf(StorageUploadError)
    })

    /** An in-memory bucket that honours (or not) the conditional forms; the probe talks to it through the adapter. */
    const bucket = (mode: 'honours' | 'ignores-delete' | 'refuses-all' | 'copy-keeps-etag') => {
      const objects = new Map<string, { body: string; etag: string }>()
      const etagOf = (b: string) => createHash('md5').update(b).digest('hex')
      const s3 = {
        upload: (p: any) => { objects.set(p.Key, { body: p.Body.toString(), etag: etagOf(p.Body.toString()) }); return done({ Key: p.Key }) },
        headObject: (p: any) => {
          const o = objects.get(p.Key)
          return o ? done({ ETag: `"${o.etag}"` }) : fail(404, 'NotFound')
        },
        deleteObjects: (p: any) => {
          const errors: any[] = []
          for (const { Key, ETag } of p.Delete.Objects) {
            const o = objects.get(Key)
            if (mode === 'refuses-all' && ETag !== undefined) { errors.push({ Key, Code: 'PreconditionFailed' }); continue }
            if (o && ETag !== undefined && mode !== 'ignores-delete' && ETag.replace(/"/g, '') !== o.etag) { errors.push({ Key, Code: 'PreconditionFailed' }); continue }
            objects.delete(Key)
          }
          return done({ Errors: errors })
        },
      }
      const fetchStub = async (_url: string, init: any) => {
        const k = 'probe-key'
        const [pk, o] = [...objects.entries()][0] ?? [k, undefined]
        const match = init.headers['If-Match']?.replace(/"/g, '')
        if (!o) return new Response(null, { status: 404 })
        if (match !== o.etag) return new Response(null, { status: 412 })
        const body = String(init.body)
        // 'copy-keeps-etag' models a store whose conditional write does not change the version.
        objects.set(pk, { body, etag: mode === 'copy-keeps-etag' ? o.etag : etagOf(body) })
        return new Response(null, { status: 200 })
      }
      return { s3, objects, fetchStub }
    }
    const probed = async (mode: Parameters<typeof bucket>[0]) => {
      const a = adapter()
      const b = bucket(mode)
      ;(a as any).s3 = b.s3
      sinon.stub(a, 'signObjectUrl').callsFake(async (_k: string, o: any) => ({ url: 'http://s3/put', headers: { 'If-Match': `"${o.ifMatch}"` } }))
      sinon.stub(globalThis, 'fetch').callsFake(b.fetchStub as any)
      return { caps: await a.conditionalCapabilities(), b, a }
    }

    it('the probe finds a store that honours both, and leaves no sentinel behind', async () => {
      const { caps, b } = await probed('honours')
      expect(caps).to.deep.equal({ conditionalDelete: true, bump: true })
      expect(b.objects.size).to.equal(0)
    })

    it('the probe finds a store that ignores the delete condition (it would delete on a mismatch)', async () => {
      expect((await probed('ignores-delete')).caps.conditionalDelete).to.equal(false)
    })

    it('the probe finds a store that refuses every conditional delete, the right ETag too', async () => {
      expect((await probed('refuses-all')).caps.conditionalDelete).to.equal(false)
    })

    it('the probe finds a store whose conditional write does not move the ETag, and says no bump', async () => {
      expect((await probed('copy-keeps-etag')).caps.bump).to.equal(false)
    })

    const flaky = (fail: { puts?: number; deletes?: number; code?: string }) => {
      const b = bucket('honours')
      const a = adapter('flaky-' + Math.random().toString(36).slice(2))
      a.probeBackoffMs = [0, 0]
      let puts = 0
      let dels = 0
      ;(a as any).s3 = {
        ...b.s3,
        deleteObjects: (p: any) => {
          if (p.Delete.Objects[0].ETag === undefined) return b.s3.deleteObjects(p) // the probe's own cleanup
          dels += 1
          if (dels <= (fail.deletes ?? 0)) return done({ Errors: [{ Key: p.Delete.Objects[0].Key, Code: fail.code ?? 'SlowDown' }] })
          return b.s3.deleteObjects(p)
        },
      }
      sinon.stub(a, 'signObjectUrl').callsFake(async (_k: string, o: any) => ({ url: 'http://s3/put', headers: { 'If-Match': `"${o.ifMatch}"` } }))
      if ((globalThis.fetch as any).restore) (globalThis.fetch as any).restore()
      sinon.stub(globalThis, 'fetch').callsFake((async (u: string, init: any) => {
        puts += 1
        if (puts <= (fail.puts ?? 0)) return new Response(null, { status: 503 })
        return b.fetchStub(u, init)
      }) as any)
      return a
    }

    it('a 5xx on the conditional PUT, or a SlowDown or InternalError on a delete, is retried and never remembered as "unsupported"', async () => {
      expect(await flaky({ puts: 1 }).conditionalCapabilities(), '503 on the wrong-ETag PUT').to.deep.equal({ conditionalDelete: true, bump: true })
      expect(await flaky({ deletes: 1, code: 'InternalError' }).conditionalCapabilities(), 'InternalError on a delete').to.deep.equal({ conditionalDelete: true, bump: true })
      expect(await flaky({ deletes: 2, code: 'SlowDown' }).conditionalCapabilities(), 'SlowDown twice').to.deep.equal({ conditionalDelete: true, bump: true })
    })

    it('an outage that outlasts the retries answers "none" for that call only; the next call, after it, gets the real answer', async () => {
      const a = flaky({ puts: 100 })
      expect(await a.conditionalCapabilities()).to.deep.equal({ conditionalDelete: false, bump: false })
      ;(globalThis.fetch as sinon.SinonStub).restore()
      const b = bucket('honours')
      ;(a as any).s3 = b.s3
      sinon.stub(globalThis, 'fetch').callsFake(b.fetchStub as any)
      expect(await a.conditionalCapabilities()).to.deep.equal({ conditionalDelete: true, bump: true })
    })

    it('callers arriving while a probe retries share it: one attempt in flight, not one each', async () => {
      const a = flaky({ puts: 1 })
      const up = sinon.spy((a as any).s3, 'upload')
      const [x, y, z] = await Promise.all([a.conditionalCapabilities(), a.conditionalCapabilities(), a.conditionalCapabilities()])
      for (const r of [x, y, z]) expect(r).to.deep.equal({ conditionalDelete: true, bump: true })
      expect(up.callCount, 'a probe per caller').to.equal(2)
    })

    it('a probe whose PUT stalls is aborted by its deadline, answers "cannot tell", and does not hold the callers', async () => {
      const a = flaky({})
      a.probeTimeoutMs = 40
      a.probeBackoffMs = []
      ;(globalThis.fetch as any).restore()
      sinon.stub(globalThis, 'fetch').callsFake(((_u: string, init: any) =>
        new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'TimeoutError' }))))) as any)
      const started = Date.now()
      expect(await a.conditionalCapabilities()).to.deep.equal({ conditionalDelete: false, bump: false })
      // wall-clock-ok: the probe deadline is 40 ms and the failure is a hang; 2 s is 50x the deadline
      expect(Date.now() - started).to.be.lessThan(2000)
    })

    it('a delete by version or a listing whose SDK call never answers fails by its deadline, never as success', async () => {
      const a = adapter()
      a.callDeadlineMs = 40
      const hung = () => ({ promise: () => new Promise(() => undefined) })
      ;(a as any).s3 = { deleteObjects: hung, listObjectsV2: hung }
      for (const [name, run] of [
        ['delete', () => a.deleteObjectsIfMatch([{ key: key(1), etag: 'e1' }])],
        ['listing', () => a.listObjects(`${ROOT}/`, { limit: 10 })],
      ] as const) {
        const started = Date.now()
        let outcome: unknown = 'resolved'
        try { await run() } catch (e) { outcome = e }
        expect(outcome, `${name} reported an answer for a call that never answered`).to.be.instanceOf(Error)
        // wall-clock-ok: the call deadline is 40 ms and the failure is a hang; 2 s is 50x the deadline
        expect(Date.now() - started, name).to.be.lessThan(2000)
      }
    })

    it('a denied probe reads as "not supported" for its window (no error, no new probe, one warning), then is asked again', async () => {
      const a = adapter('denied-window-' + Math.random().toString(36).slice(2))
      a.probeBackoffMs = []
      a.probeDeniedTtlMs = 60
      const warn = sinon.spy()
      ;(a as any).logger = { warn, info: sinon.stub(), error: sinon.stub(), debug: sinon.stub() }
      const up = sinon.stub().returns({ promise: async () => { throw Object.assign(new Error('denied'), { $metadata: { httpStatusCode: 403 }, code: 'AccessDenied' }) } })
      ;(a as any).s3 = { upload: up, deleteObjects: () => done({}) }
      for (let i = 0; i < 5; i += 1) expect(await a.conditionalCapabilities()).to.deep.equal({ conditionalDelete: false, bump: false })
      expect(up.callCount, 'a denied probe was repeated inside its window').to.equal(1)
      expect(warn.callCount, 'logged more than once per window').to.equal(1)
      await new Promise((resolve) => setTimeout(resolve, 90))
      expect(await a.conditionalCapabilities()).to.deep.equal({ conditionalDelete: false, bump: false })
      expect(up.callCount, 'not asked again after the window').to.equal(2)
    })

    it('a probe that could not tell never answers an error: after its bounded retries it reads as "not supported" and is asked again after its window', async () => {
      const a = flaky({ puts: 100 })
      a.probeBackoffMs = [0, 0]
      a.probeUnsureTtlMs = 60
      expect(await a.conditionalCapabilities()).to.deep.equal({ conditionalDelete: false, bump: false })
      await new Promise((resolve) => setTimeout(resolve, 90))
      ;(globalThis.fetch as any).restore()
      const b = bucket('honours')
      ;(a as any).s3 = b.s3
      sinon.stub(globalThis, 'fetch').callsFake(b.fetchStub as any)
      expect(await a.conditionalCapabilities()).to.deep.equal({ conditionalDelete: true, bump: true })
    })

    it('a 403 (denied probe prefix, refreshing credential) or a 400 RequestTimeout is not "unsupported": asked again, never remembered', async () => {
      for (const failure of [
        { $metadata: { httpStatusCode: 403 }, code: 'AccessDenied' },
        { $metadata: { httpStatusCode: 400 }, code: 'RequestTimeout' },
      ]) {
        const a = adapter('denied-' + Math.random().toString(36).slice(2))
        a.probeBackoffMs = []
        const b = bucket('honours')
        let first = true
        ;(a as any).s3 = { ...b.s3, upload: (p: any) => { if (first) { first = false; return { promise: async () => { throw Object.assign(new Error('x'), failure) } } } return b.s3.upload(p) } }
        sinon.stub(a, 'signObjectUrl').callsFake(async (_k: string, o: any) => ({ url: 'http://s3/put', headers: { 'If-Match': `"${o.ifMatch}"` } }))
        if ((globalThis.fetch as any).restore) (globalThis.fetch as any).restore()
        sinon.stub(globalThis, 'fetch').callsFake(b.fetchStub as any)
        expect(await a.conditionalCapabilities(), failure.code).to.deep.equal({ conditionalDelete: false, bump: false })
        expect(await a.conditionalCapabilities(), 'asked again').to.deep.equal({ conditionalDelete: true, bump: true })
      }
    })

    it('a delete that leaves the object without a precondition code (AccessDenied) is no evidence, and is not remembered', async () => {
      const a = adapter('denied-del-' + Math.random().toString(36).slice(2))
      a.probeBackoffMs = []
      const b = bucket('honours')
      let denied = true
      ;(a as any).s3 = {
        ...b.s3,
        deleteObjects: (p: any) => {
          // only the wrong-ETag delete is denied: the later ones work, so only this answer can tell the probe it is unsure
          if (denied && p.Delete.Objects[0].ETag !== undefined) {
            denied = false
            return done({ Errors: [{ Key: p.Delete.Objects[0].Key, Code: 'AccessDenied' }] })
          }
          return b.s3.deleteObjects(p)
        },
      }
      sinon.stub(a, 'signObjectUrl').callsFake(async (_k: string, o: any) => ({ url: 'http://s3/put', headers: { 'If-Match': `"${o.ifMatch}"` } }))
      if ((globalThis.fetch as any).restore) (globalThis.fetch as any).restore()
      sinon.stub(globalThis, 'fetch').callsFake(b.fetchStub as any)
      expect(await a.conditionalCapabilities()).to.deep.equal({ conditionalDelete: false, bump: false })
      expect(denied, 'the denied delete was never reached').to.equal(false)
      expect(await a.conditionalCapabilities()).to.deep.equal({ conditionalDelete: true, bump: true })
    })

    it('a 403 on the sentinel upload is logged with the prefix to allow and is no answer', async () => {
      const a = adapter('denied-setup-' + Math.random().toString(36).slice(2))
      a.probeBackoffMs = []
      const warn = sinon.spy()
      ;(a as any).logger = { warn, info: sinon.stub(), error: sinon.stub(), debug: sinon.stub() }
      ;(a as any).s3 = {
        upload: () => ({ promise: async () => { throw Object.assign(new Error('denied'), { $metadata: { httpStatusCode: 403 }, code: 'AccessDenied' }) } }),
        deleteObjects: () => done({}),
      }
      expect(await a.conditionalCapabilities()).to.deep.equal({ conditionalDelete: false, bump: false })
      expect(warn.calledOnce, 'the denial was not logged').to.equal(true)
      expect(JSON.stringify(warn.firstCall.args)).to.include('paid-results-conditional-probe')
    })

    it('a 501 on the sentinel upload (the setup) is no answer about conditions; a 501 on the conditional write is definitive', async () => {
      const setup = adapter('notimpl-setup-' + Math.random().toString(36).slice(2))
      setup.probeBackoffMs = []
      ;(setup as any).s3 = {
        upload: () => ({ promise: async () => { throw Object.assign(new Error('ni'), { $metadata: { httpStatusCode: 501 } }) } }),
        deleteObjects: () => done({}),
      }
      expect(await setup.conditionalCapabilities()).to.deep.equal({ conditionalDelete: false, bump: false })

      const cond = adapter('notimpl-cond-' + Math.random().toString(36).slice(2))
      cond.probeBackoffMs = []
      const b = bucket('honours')
      ;(cond as any).s3 = b.s3
      sinon.stub(cond, 'signObjectUrl').callsFake(async (_k: string, o: any) => ({ url: 'http://s3/put', headers: { 'If-Match': `"${o.ifMatch}"` } }))
      if ((globalThis.fetch as any).restore) (globalThis.fetch as any).restore()
      sinon.stub(globalThis, 'fetch').callsFake((async () => new Response(null, { status: 501 })) as any)
      const first = await cond.conditionalCapabilities()
      expect(first.bump).to.equal(false)
      expect(await cond.conditionalCapabilities()).to.deep.equal(first)
    })

    it('a HEAD that never answers is cut by the probe deadline and the probe answers "cannot tell"', async () => {
      const a = flaky({})
      a.probeTimeoutMs = 40
      a.probeBackoffMs = []
      ;(a as any).s3 = { ...(a as any).s3, headObject: () => ({ promise: () => new Promise(() => undefined) }) }
      const started = Date.now()
      expect(await a.conditionalCapabilities()).to.deep.equal({ conditionalDelete: false, bump: false })
      // wall-clock-ok: the probe deadline is 40 ms and the failure is a hang; 2 s is 50x the deadline
      expect(Date.now() - started).to.be.lessThan(2000)
    })

    it('an ambiguous answer to the missing-key delete (AccessDenied) is no answer, after a conditional delete that worked', async () => {
      const a = adapter('missing-denied-' + Math.random().toString(36).slice(2))
      a.probeBackoffMs = []
      const b = bucket('honours')
      let deletesWithEtag = 0
      ;(a as any).s3 = {
        ...b.s3,
        deleteObjects: (p: any) => {
          if (p.Delete.Objects[0].ETag === undefined) return b.s3.deleteObjects(p)
          deletesWithEtag += 1
          if (deletesWithEtag === 3) return done({ Errors: [{ Key: p.Delete.Objects[0].Key, Code: 'AccessDenied' }] })   // the missing-key delete
          return b.s3.deleteObjects(p)
        },
      }
      sinon.stub(a, 'signObjectUrl').callsFake(async (_k: string, o: any) => ({ url: 'http://s3/put', headers: { 'If-Match': `"${o.ifMatch}"` } }))
      if ((globalThis.fetch as any).restore) (globalThis.fetch as any).restore()
      sinon.stub(globalThis, 'fetch').callsFake(b.fetchStub as any)
      expect(await a.conditionalCapabilities()).to.deep.equal({ conditionalDelete: false, bump: false })
      expect(await a.conditionalCapabilities()).to.deep.equal({ conditionalDelete: true, bump: true })
    })

    it('a transient failure is not remembered: the next ask probes again and gets the real answer', async () => {
      const a = adapter('one-bucket')
      a.probeBackoffMs = []
      const b = bucket('honours')
      let calls = 0
      ;(a as any).s3 = { ...b.s3, upload: (p: any) => { calls += 1; return calls === 1 ? fail(503, 'SlowDown') : b.s3.upload(p) } }
      sinon.stub(a, 'signObjectUrl').callsFake(async (_k: string, o: any) => ({ url: 'http://s3/put', headers: { 'If-Match': `"${o.ifMatch}"` } }))
      sinon.stub(globalThis, 'fetch').callsFake(b.fetchStub as any)
      expect(await a.conditionalCapabilities()).to.deep.equal({ conditionalDelete: false, bump: false })
      expect(await a.conditionalCapabilities()).to.deep.equal({ conditionalDelete: true, bump: true })
    })

    it('a definitive answer is remembered per bucket and concurrent askers share one probe', async () => {
      const a = adapter('shared-bucket')
      const b = bucket('honours')
      const upload = sinon.spy(b.s3, 'upload')
      ;(a as any).s3 = b.s3
      sinon.stub(a, 'signObjectUrl').callsFake(async (_k: string, o: any) => ({ url: 'http://s3/put', headers: { 'If-Match': `"${o.ifMatch}"` } }))
      sinon.stub(globalThis, 'fetch').callsFake(b.fetchStub as any)
      await Promise.all([a.conditionalCapabilities(), a.conditionalCapabilities(), a.conditionalCapabilities()])
      await a.conditionalCapabilities()
      expect(upload.callCount).to.equal(1)
    })
  })

  describe('Azure', () => {
    const container = (deleteIfExists: sinon.SinonStub, exists: sinon.SinonStub = sinon.stub().resolves(true)) => {
      const a = Object.create(AzureBlobStorageAdapter.prototype)
      a.containerReady = Promise.resolve()
      a.containerClient = { getBlockBlobClient: sinon.stub().returns({ deleteIfExists, exists }) }
      return a as AzureBlobStorageAdapter
    }

    it('deleteObjectsIfMatch conditions every delete on the quoted etag; a refusal is kept and does not reject the others', async () => {
      const del = sinon.stub()
      del.onCall(1).rejects(Object.assign(new Error('cond'), { statusCode: 412 }))
      del.onCall(2).rejects(Object.assign(new Error('gone'), { statusCode: 404 }))
      del.resolves(true)
      const a = container(del)
      const r = await a.deleteObjectsIfMatch([1, 2, 3, 4].map((n) => ({ key: key(n), etag: `e${n}` })))
      expect(r.changed).to.deep.equal([key(2)])
      expect(del.callCount).to.equal(4)
      expect(del.firstCall.args[0]).to.deep.equal({ conditions: { ifMatch: '"e1"' } })
    })

    it('a refused condition on a blob that is gone (Azurite answers 412 for it) is a success, not a change', async () => {
      const del = sinon.stub().rejects(Object.assign(new Error('cond'), { statusCode: 412 }))
      const exists = sinon.stub().resolves(false)
      const r = await container(del, exists).deleteObjectsIfMatch([{ key: key(1), etag: 'e1' }])
      expect(r.changed).to.deep.equal([])
      expect(exists.callCount).to.equal(1)
    })

    it('a delete refused for a snapshot (409) or a lease (412 with another code) is a failure, not a changed object', async () => {
      for (const reason of [
        Object.assign(new Error('snapshots'), { statusCode: 409, code: 'SnapshotsPresent' }),
        Object.assign(new Error('lease'), { statusCode: 412, details: { errorCode: 'LeaseIdMissing' } }),
      ]) {
        let thrown: unknown
        try { await container(sinon.stub().rejects(reason)).deleteObjectsIfMatch([{ key: key(1), etag: 'e1' }]) } catch (e) { thrown = e }
        expect(thrown, reason.message).to.be.instanceOf(StorageUploadError)
      }
      const exists = sinon.stub().resolves(true)
      const r = await container(sinon.stub().rejects(Object.assign(new Error('c'), { statusCode: 412, details: { errorCode: 'ConditionNotMet' } })), exists).deleteObjectsIfMatch([{ key: key(1), etag: 'e1' }])
      expect(r.changed).to.deep.equal([key(1)])
    })

    it('an existence check that fails does not abandon the rest of the batch; it is reported after every slice ran', async () => {
      const del = sinon.stub().resolves(true)
      del.onCall(0).rejects(Object.assign(new Error('c'), { statusCode: 412, details: { errorCode: 'ConditionNotMet' } }))
      const exists = sinon.stub().rejects(new Error('503'))
      const a = container(del, exists)
      let thrown: unknown
      try { await a.deleteObjectsIfMatch(Array.from({ length: 17 }, (_, i) => ({ key: key(i + 1), etag: 'e' }))) } catch (e) { thrown = e }
      expect(thrown).to.be.instanceOf(StorageUploadError)
      expect(del.callCount, 'the second slice was never attempted').to.equal(17)
    })

    it('a conditional delete, an existence check or a listing that never answers fails by its deadline, never as success', async () => {
      const never = new Promise(() => undefined)
      const base = (extra: object) => {
        const a = Object.create(AzureBlobStorageAdapter.prototype)
        a.containerReady = Promise.resolve()
        a.callDeadlineMs = 40
        a.containerClient = extra
        return a as AzureBlobStorageAdapter
      }
      const attempts: [string, () => Promise<unknown>][] = [
        ['delete', () => base({ getBlockBlobClient: () => ({ deleteIfExists: () => never }) }).deleteObjectsIfMatch([{ key: key(1), etag: 'e' }])],
        ['exists', () => base({ getBlockBlobClient: () => ({ deleteIfExists: () => Promise.reject(Object.assign(new Error('c'), { statusCode: 412, details: { errorCode: 'ConditionNotMet' } })), exists: () => never }) }).deleteObjectsIfMatch([{ key: key(1), etag: 'e' }])],
        ['listing', () => base({ listBlobsFlat: () => ({ byPage: () => ({ next: () => never }) }) }).listObjects(`${ROOT}/`, { limit: 10 })],
      ]
      for (const [name, run] of attempts) {
        const started = Date.now()
        let outcome: unknown = 'resolved'
        try { await run() } catch (e) { outcome = e }
        expect(outcome, `${name} reported an answer for a call that never answered`).to.be.instanceOf(Error)
        // wall-clock-ok: the call deadline is 40 ms and the failure is a hang; 2 s is 50x the deadline
        expect(Date.now() - started, name).to.be.lessThan(2000)
      }
    })

    it('any other failure of a conditional delete is raised after the rest were tried', async () => {
      const del = sinon.stub().resolves(true)
      del.onCall(0).rejects(Object.assign(new Error('boom'), { statusCode: 500 }))
      let thrown: unknown
      try { await container(del).deleteObjectsIfMatch([1, 2].map((n) => ({ key: key(n), etag: 'e' }))) } catch (e) { thrown = e }
      expect(thrown).to.be.instanceOf(StorageUploadError)
      expect(del.callCount).to.equal(2)
    })

    it('lists the blob etag, signs the If-Match header, and always honours conditional writes', async () => {
      const a = Object.create(AzureBlobStorageAdapter.prototype)
      a.containerReady = Promise.resolve()
      a.containerClient = {
        listBlobsFlat: sinon.stub().returns({ byPage: () => ({ next: async () => ({ value: { segment: { blobItems: [{ name: key(1), properties: { contentLength: 1, lastModified: new Date(7), etag: '"0x8DB"' } }] } } }) }) }),
        getBlockBlobClient: () => ({ generateSasUrl: async () => 'https://acct.blob/c/k?sas' }),
      }
      const page = await (a as AzureBlobStorageAdapter).listObjects(`${ROOT}/`, { limit: 10 })
      expect(page.objects[0]!.etag).to.equal('0x8DB')
      const signed = await (a as AzureBlobStorageAdapter).signObjectUrl(key(1), { method: 'PUT', expiresInSeconds: 900, ifMatch: '0x8DB' })
      expect(signed.headers['If-Match']).to.equal('"0x8DB"')
      expect(await (a as AzureBlobStorageAdapter).conditionalCapabilities()).to.deep.equal({ conditionalDelete: true, bump: true })
    })
  })
})
