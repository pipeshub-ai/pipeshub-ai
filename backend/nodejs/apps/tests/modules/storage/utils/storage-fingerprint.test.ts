import { expect } from 'chai'
import {
  fingerprintEpoch,
  fingerprintValue,
  isSettled,
  localEtagOf,
  normalizeEtag,
} from '../../../../src/modules/storage/utils/storage-fingerprint'

describe('storage fingerprint: pure parts', () => {
  describe('fingerprintValue', () => {
    // The vectors were produced by an independent implementation (python hashlib), not by this one.
    const v = (epoch: string, writeSeq: number, mutationCount: number, versionCount: number, size: number | null | undefined, etag: string | undefined) =>
      fingerprintValue({ epoch, writeSeq, mutationCount, versionCount, size, etag })
    const vectors: [string, number, number, number, number | null, string | undefined, string][] = [
      ['0', 0, 1, 0, null, undefined, '010aee56c773e1597e4ad2f4fbb33934'],
      ['0', 3, 1, 2, 5, 'abc', 'bde240babc562b7114658df9d383e355'],
      ['0', 3, 1, 2, 5, 'abd', 'ba1852b9f788135a1bfb3508a7ef7402'],
      ['0', 4, 1, 2, 5, 'abc', 'a2b5acf2c9ded62b16a38ee7a3ee3570'],
      ['0', 3, 2, 2, 5, 'abc', '0b20d4cf484ccbda3d9f0ec757e82318'],
      ['0', 3, 1, 3, 5, 'abc', '76af4efbee705b217676eafbe6223f5e'],
      ['0', 3, 1, 2, 6, 'abc', '657656635a1f03f070ee43dfc0ca82f9'],
      ['1', 3, 1, 2, 5, 'abc', 'f20527ac3558922eae741a2b15d9e3bc'],
      ['7', 12, 4, 0, 1048576, 'd41d8cd98f00b204e9800998ecf8427e', '9372f85f402dd0c8c10e5d92367e9c8f'],
      ['0', 0, 0, 0, 0, undefined, 'cdad82c6125f93b2207585133f5c4156'],
    ]
    for (const [epoch, seq, mc, vc, size, etag, want] of vectors)
      it(`epoch ${epoch} seq ${seq} mutations ${mc} versions ${vc} size ${String(size)} etag ${String(etag)}`, () => {
        expect(v(epoch, seq, mc, vc, size, etag)).to.equal(want)
      })

    it('each input moves the value, and the epoch is one of them', () => {
      const base = v('0', 3, 1, 2, 5, 'abc')
      expect(new Set(vectors.slice(1, 8).map(([e, s, m, c, z, t]) => v(e, s, m, c, z, t))).size).to.equal(7)
      expect(base).to.have.length(32)
      expect(v('1', 3, 1, 2, 5, 'abc')).to.not.equal(base)
    })

    it('is a function of its inputs only: a size of undefined and null agree, and the object size decides, not a later stored size', () => {
      expect(v('0', 1, 1, 0, undefined, 'e')).to.equal(v('0', 1, 1, 0, null, 'e'))
    })
  })

  describe('isSettled', () => {
    const rows: [string, { until: number }[], number, boolean][] = [
      ['no writers', [], 100, true],
      ['one live token', [{ until: 101 }], 100, false],
      ['a token that lapses exactly now', [{ until: 100 }], 100, true],
      ['an expired token', [{ until: 99 }], 100, true],
      ['one live among expired', [{ until: 1 }, { until: 500 }, { until: 2 }], 100, false],
      ['all expired', [{ until: 1 }, { until: 2 }], 100, true],
    ]
    for (const [name, writers, now, want] of rows)
      it(name, () => {
        expect(isSettled(writers, now)).to.equal(want)
      })
  })

  describe('normalizeEtag: one form for every vendor', () => {
    it('S3 and Azure return a quoted tag; local a bare digest', () => {
      expect(normalizeEtag('"abc"')).to.equal('abc') // S3, Azure
      expect(normalizeEtag('abc')).to.equal('abc') // local
      expect(normalizeEtag('W/"abc"')).to.equal('abc') // weakened by a proxy
      expect(normalizeEtag('"d41d8cd98f00b204e9800998ecf8427e-2"')).to.equal('d41d8cd98f00b204e9800998ecf8427e-2') // multipart
      expect(normalizeEtag('0x8DC1A2B3C4D5E6F')).to.equal('0x8DC1A2B3C4D5E6F') // Azure style
      expect(normalizeEtag(undefined)).to.equal('')
      expect(normalizeEtag('')).to.equal('')
      expect(normalizeEtag('"')).to.equal('"')
    })
  })

  describe('localEtagOf', () => {
    it('hashes dev:ino:size:mtimeNs and changes with each part', () => {
      const a = { dev: 1n, ino: 2n, size: 3n, mtimeNs: 4n }
      expect(localEtagOf(a)).to.equal('e7b71c81f5a0723e2237f157dba81777ce7c6c21')
      for (const k of ['dev', 'ino', 'size', 'mtimeNs'] as const) expect(localEtagOf({ ...a, [k]: 9n })).to.not.equal(localEtagOf(a))
    })
  })

  describe('fingerprintEpoch', () => {
    const saved = process.env.STORAGE_FINGERPRINT_EPOCH
    afterEach(() => {
      if (saved === undefined) delete process.env.STORAGE_FINGERPRINT_EPOCH
      else process.env.STORAGE_FINGERPRINT_EPOCH = saved
    })
    it('is 0 unless the operator sets it', () => {
      delete process.env.STORAGE_FINGERPRINT_EPOCH
      expect(fingerprintEpoch()).to.equal('0')
      process.env.STORAGE_FINGERPRINT_EPOCH = ' 4 '
      expect(fingerprintEpoch()).to.equal('4')
      process.env.STORAGE_FINGERPRINT_EPOCH = ''
      expect(fingerprintEpoch()).to.equal('0')
    })
  })
})
