import { expect } from 'chai'
import { stripNul } from '../../../src/libs/repositories/nul'
import { metricsBackend } from '../../../src/libs/services/telemetry/metrics-backend'

const counter = async (): Promise<number> => {
  const text = await metricsBackend.serialize()
  return text
    .split('\n')
    .filter((l) => l.startsWith('docstore_nul_stripped_total{') && l.includes('repo="t-nul"'))
    .reduce((n, l) => n + Number(l.split(' ').pop()), 0)
}

describe('stripNul', () => {
  it('removes U+0000 from strings, nested values and object keys, and counts each one', async () => {
    const before = await counter()
    const out = stripNul({ 'a\u0000b': ['x\u0000y', { z: 'q\u0000\u0000' }], n: 1, d: new Date(0), k: null }, 't-nul', 'doc')
    expect(out).to.deep.equal({ ab: ['xy', { z: 'q' }], n: 1, d: new Date(0), k: null })
    expect((await counter()) - before).to.equal(4)
  })

  it('returns the same object, and counts nothing, when there is no NUL', async () => {
    const before = await counter()
    const input = { a: ['b', { c: 'd' }] }
    expect(stripNul(input, 't-nul')).to.equal(input)
    expect(await counter()).to.equal(before)
  })

  it('leaves buffers and class instances alone', () => {
    const buf = Buffer.from([0, 1])
    expect(stripNul({ b: buf }, 't-nul').b).to.equal(buf)
  })

  it('handles a bare string', () => {
    expect(stripNul('a\u0000', 't-nul')).to.equal('a')
  })
})
