import { expect } from 'chai'
import type { DocumentRepository } from '../../../../src/libs/repositories/document'
import { ServerClockUnavailable } from '../../../../src/libs/repositories/document/types'
import { ServiceUnavailableError } from '../../../../src/libs/errors/http.errors'
import { StorageUrlParseError, StorageValidationError } from '../../../../src/libs/errors/storage.errors'
import { readDocumentWithFingerprint } from '../../../../src/modules/storage/controllers/storage.fingerprint'
import type { ObjectStat } from '../../../../src/modules/storage/utils/storage-fingerprint'

const NOW = 1_000_000
const quiet = { error() {}, warn() {} }
type Writers = { t: string; until: number }[]

/** A repository over one in-memory row; every read is logged so the tests can see the order. */
function world(initial: Record<string, unknown> = {}) {
  const w = {
    row: { _id: 'd1', orgId: 'o1', documentName: 'n', mutationCount: 1, versionHistory: [], sizeInBytes: 10, ...initial } as Record<string, any> | null,
    seq: 3,
    writers: [] as Writers,
    stat: { size: 5, etag: 'e1', lastModified: null } as ObjectStat | null,
    log: [] as string[],
    adopted: [] as number[],
    corrected: [] as [unknown, number][],
    adoptResult: true,
    clockDown: false,
    // runs before the named step returns (a write landing in the middle of a pass)
    during: {} as Record<string, () => void>,
    statError: undefined as Error | undefined,
  }
  const repo = {
    async getWithFingerprintState() {
      if (w.clockDown) throw new ServerClockUnavailable()
      w.log.push('row')
      const out = w.row ? { row: { ...w.row }, writeSeq: w.seq, writers: w.writers.map((x) => ({ ...x })), serverNow: NOW } : null
      w.during.row?.()
      return out as any
    },
    async peekWriteState() {
      w.log.push('reread')
      return w.row ? { writeSeq: w.seq, writers: w.writers.map((x) => ({ ...x })) } : null
    },
    async adoptDirectUpload(_o: string, _i: string, size: number) {
      w.log.push('adopt')
      w.adopted.push(size)
      if (w.adoptResult && w.row) { w.row.awaitingDirectUpload = undefined; w.row.sizeInBytes = size; w.seq += 1 }
      return w.adoptResult
    },
    async correctSize(_o: string, _i: string, from: unknown, to: number) { w.log.push('correct'); w.corrected.push([from, to]) },
    async get() { return w.row ? { ...w.row } : null },
  } as unknown as DocumentRepository
  const stat = async () => {
    w.log.push('stat')
    w.during.stat?.()
    if (w.statError) throw w.statError
    return w.stat ? { ...w.stat } : null
  }
  return Object.assign(w, { repo, statFn: stat as (d: any) => Promise<ObjectStat | null> })
}

const read = (w: ReturnType<typeof world>, verify = true) =>
  readDocumentWithFingerprint({ repo: w.repo, stat: w.statFn, logger: quiet }, 'o1', 'd1', verify)

describe('readDocumentWithFingerprint', () => {
  it('reads the row, then the object, then the write state again, and trusts a settled, unchanged document', async () => {
    const w = world()
    const out = await read(w)
    expect(w.log).to.deep.equal(['row', 'stat', 'reread', 'correct'])
    expect(out?.storageFingerprint).to.include({ version: 1, verified: true, trusted: true, settled: true, objectMissing: false, objectUnverifiable: false })
    expect(out?.storageFingerprint.object).to.deep.equal({ size: 5, etag: 'e1', lastModified: null })
    expect(out?.document.sizeInBytes, 'the object size, not the declared one').to.equal(5)
    expect(w.corrected).to.deep.equal([[10, 5]])
  })

  it('without verify the object store is not asked, nothing is trusted, and the value follows the counters', async () => {
    const w = world()
    const a = await read(w, false)
    expect(w.log).to.deep.equal(['row'])
    expect(a?.storageFingerprint).to.include({ verified: false, trusted: false })
    expect(a?.storageFingerprint.object).to.equal(null)
    w.seq += 1
    const b = await read(w, false)
    expect(b?.storageFingerprint.value).to.not.equal(a?.storageFingerprint.value)
  })

  it('a live writer: settled false, not trusted; a lapsed one does not count', async () => {
    const w = world()
    w.writers = [{ t: 'a', until: NOW + 5 }]
    expect((await read(w))?.storageFingerprint).to.include({ settled: false, trusted: false })
    w.writers = [{ t: 'a', until: NOW - 1 }]
    expect((await read(w))?.storageFingerprint).to.include({ settled: true, trusted: true })
  })

  it('a write that lands between the row and the re-read (sequence moved) is not trusted', async () => {
    const w = world()
    w.during.stat = () => { w.seq += 1 }
    const fp = (await read(w))?.storageFingerprint
    expect(fp?.trusted).to.equal(false)
  })

  it('the value is built from the counters of the first read and the object of the stat', async () => {
    const w = world()
    const a = (await read(w))?.storageFingerprint.value
    w.stat = { size: 5, etag: 'e2', lastModified: null }
    expect((await read(w))?.storageFingerprint.value, 'a new etag').to.not.equal(a)
    w.stat = { size: 5, etag: 'e1', lastModified: null }
    expect((await read(w))?.storageFingerprint.value, 'the same state again').to.equal(a)
  })

  it('an object that is gone is reported missing, never an error, and never trusted', async () => {
    const w = world()
    w.stat = null
    const fp = (await read(w))?.storageFingerprint
    expect(fp).to.include({ objectMissing: true, trusted: false, verified: true })
    expect(fp?.object).to.equal(null)
  })

  it('a store failure is a 503, not a 200 with no object', async () => {
    const w = world()
    w.statError = new Error('socket hang up')
    let err: unknown
    try { await read(w) } catch (e) { err = e }
    expect(err).to.be.instanceOf(ServiceUnavailableError)
  })

  it('only the S3 key parse failure is "cannot be verified"; any other validation error is not converted', async () => {
    const w = world()
    w.statError = new StorageUrlParseError('Invalid S3 URL format')
    const out = await read(w)
    expect(out?.storageFingerprint).to.include({ objectUnverifiable: true, trusted: false, reason: 'unparseable_storage_url', objectMissing: false })
    expect(out?.storageFingerprint.object).to.equal(null)
    w.statError = new StorageValidationError('Invalid document path') // a traversal rejection
    let err: unknown
    try { await read(w) } catch (e) { err = e }
    expect(err, 'a path guard rejection is not a configuration limit').to.be.instanceOf(ServiceUnavailableError)
  })

  it('an artifact set has no object fingerprint; deleted and retired rows are not trusted', async () => {
    for (const row of [{ artifactSet: { virtualRecordId: 'v', rev: 'r', root: 'x' } }, { isDeleted: true }, { retiredAt: 5 }]) {
      const w = world(row)
      const out = await read(w)
      expect(out?.storageFingerprint.trusted, JSON.stringify(row)).to.equal(false)
      if ('artifactSet' in row) {
        expect(out?.storageFingerprint.object).to.equal(null)
        expect(w.log, 'no stat for an artifact set').to.not.include('stat')
      }
    }
  })

  describe('a finished direct upload (D4)', () => {
    it('absent object: reported awaiting, no object, not trusted, nothing adopted', async () => {
      const w = world({ awaitingDirectUpload: true })
      w.stat = null
      const out = await read(w)
      expect(out?.storageFingerprint).to.include({ trusted: false })
      expect(out?.document.awaitingDirectUpload).to.equal(true)
      expect(w.adopted).to.deep.equal([])
    })

    it('arrived object: adopted with the size of the stat, then the WHOLE pass runs again and the answer is built from it', async () => {
      const w = world({ awaitingDirectUpload: true, sizeInBytes: 10_485_760 })
      const out = await read(w)
      expect(w.log).to.deep.equal(['row', 'stat', 'reread', 'adopt', 'row', 'stat', 'reread'])
      expect(w.adopted).to.deep.equal([5])
      expect(out?.document.awaitingDirectUpload).to.equal(undefined)
      expect(out?.document.sizeInBytes).to.equal(5)
      expect(out?.storageFingerprint).to.include({ trusted: true })
    })

    it('the loser of a concurrent adopt runs the same second pass and reports the same state', async () => {
      const w = world({ awaitingDirectUpload: true })
      w.adoptResult = false
      w.during.row = () => {} // the row the loser reads still says awaiting; the winner had cleared it before the second pass
      const original = w.repo.adoptDirectUpload.bind(w.repo)
      ;(w.repo as any).adoptDirectUpload = async (...a: any[]) => { w.row!.awaitingDirectUpload = undefined; w.seq += 1; return original(...a) }
      const out = await read(w)
      expect(w.log.filter((l) => l === 'row')).to.have.length(2)
      expect(out?.storageFingerprint.trusted).to.equal(true)
      expect(out?.document.awaitingDirectUpload).to.equal(undefined)
    })

    it('a second PUT between the stat and the adopt: the final pass size wins and the stored size converges', async () => {
      const w = world({ awaitingDirectUpload: true, sizeInBytes: 10_000 })
      let first = true
      w.statFn = async () => { w.log.push('stat'); const s = first ? 5 : 7; first = false; return { size: s, etag: `e${s}`, lastModified: null } }
      const out = await readDocumentWithFingerprint({ repo: w.repo, stat: w.statFn, logger: quiet }, 'o1', 'd1', true)
      expect(w.adopted).to.deep.equal([5])
      expect(out?.storageFingerprint.object?.size).to.equal(7)
      expect(out?.document.sizeInBytes).to.equal(7)
      expect(w.corrected).to.deep.equal([[5, 7]])
    })

    it('the row removed by an abort before the second pass: 404, not an answer built from the first pass', async () => {
      const w = world({ awaitingDirectUpload: true })
      ;(w.repo as any).adoptDirectUpload = async () => { w.row = null; return false }
      expect(await read(w)).to.equal(null)
    })

    it('awaiting again in the second pass (a link was re-issued): not trusted, no third pass', async () => {
      const w = world({ awaitingDirectUpload: true })
      ;(w.repo as any).adoptDirectUpload = async () => { w.row!.awaitingDirectUpload = true; w.seq += 1; return true }
      const out = await read(w)
      expect(w.log.filter((l) => l === 'row')).to.have.length(2)
      expect(out?.storageFingerprint.trusted).to.equal(false)
      expect(out?.document.awaitingDirectUpload).to.equal(true)
    })
  })

  it('a missing row is null; a row removed before the re-read is null', async () => {
    const gone = world()
    gone.row = null
    expect(await read(gone)).to.equal(null)
    const w = world()
    w.during.stat = () => { w.row = null }
    expect(await read(w)).to.equal(null)
  })

  it('the clock being unavailable is "not trusted" with a reason, never a trust and never an error', async () => {
    const w = world()
    w.clockDown = true
    expect((await read(w))?.storageFingerprint).to.include({ trusted: false, reason: 'server_clock_unavailable', verified: true })
    expect((await read(w, false))?.storageFingerprint).to.include({ trusted: false, reason: 'server_clock_unavailable', verified: false })
  })
})
