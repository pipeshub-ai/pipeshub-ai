import 'reflect-metadata'
import { expect } from 'chai'
import { changedIds, reportFailures } from '../../../src/scripts/docstore-migrate/online'

const clean = () => ({
  ok: true,
  at: 'now',
  diffs: 0,
  global: [{ name: 'g', ok: true }],
  isolation: { probed: 1, failures: [] as string[] },
  collections: [{ collection: 'users', diffCount: 0, countMismatches: [], unmappedPaths: [], nullInOmitField: [], invariantViolations: [] }],
})

describe('online verify helpers', () => {
  it('a position that fell out of the oplog gives no list of changes, not an error', async () => {
    const stream = { tryNext: async () => Promise.reject(Object.assign(new Error('resume of change stream was not possible, as the resume point may no longer be in the oplog'), { code: 286 })), close: async () => undefined }
    const rt = {
      cfg: { mongoDb: 'es' },
      db: () => ({ collection: () => ({ insertOne: async () => ({}) }) }),
      mongo: { watch: () => stream },
    } as never
    expect(await changedIds(rt, { _data: 'old' }, 1000)).to.equal(null)
  })

  it('any other stream error still surfaces', async () => {
    const stream = { tryNext: async () => Promise.reject(new Error('socket closed')), close: async () => undefined }
    const rt = { cfg: { mongoDb: 'es' }, db: () => ({ collection: () => ({ insertOne: async () => ({}) }) }), mongo: { watch: () => stream } } as never
    let err: Error | undefined
    await changedIds(rt, 'x', 1000).catch((e: Error) => (err = e))
    expect(err?.message).to.equal('socket closed')
  })

  it('names what a compare found that is not a difference of one document', () => {
    expect(reportFailures(clean() as never)).to.deep.equal([])
    const bad = clean()
    bad.collections[0] = { ...bad.collections[0]!, countMismatches: [{}] as never, unmappedPaths: [{}] as never, nullInOmitField: [{}] as never, invariantViolations: [{}, {}] as never }
    bad.global[0] = { name: 'org slug sequence', ok: false }
    bad.isolation.failures = ['org a sees org b']
    const out = reportFailures(bad as never)
    expect(out).to.have.length(6)
    expect(out.join(' | ')).to.contain('users: row counts differ').and.to.contain('2 rule(s)').and.to.contain('global check org slug sequence failed').and.to.contain('isolation: 1')
    // a document that differs is not one of these: the passes over changed documents handle those
    const diff = clean()
    diff.collections[0] = { ...diff.collections[0]!, diffCount: 3 }
    expect(reportFailures(diff as never)).to.deep.equal([])
  })
})
