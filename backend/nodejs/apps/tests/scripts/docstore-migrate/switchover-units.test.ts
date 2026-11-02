import 'reflect-metadata'
import { expect } from 'chai'
import { catchUp, changedIds, onlineStart, precheck, reportFailures, unfreeze } from '../../../src/scripts/docstore-migrate/online'
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping'
import { outboxTableId } from '../../../src/scripts/docstore-migrate/mapping/content'

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

  describe('the pre-checks of the cut', () => {
    /** A runtime whose servers answer as the checks expect; `over` changes single answers. */
    const world = (over: { fence?: unknown; backend?: string; readonly?: boolean; triggers?: string; status?: unknown } = {}) => {
      const fence = over.fence ?? { mode: 'active', epoch: 1 }
      const rt = {
        cfg: { mongoDb: 'es' },
        // the catch-up cannot run on these fakes: it is reported as not caught up
        db: () => ({ collection: () => ({ findOne: async () => fence }), command: async () => ({}) }),
        mongo: {
          db: () => ({
            command: async (c: any) => (c.replSetGetStatus ? (over.status ?? { members: [{ health: 1, state: 1 }], writeMajorityCount: 1, optimes: { lastCommittedOpTime: { ts: 'T' } } }) : {}),
          }),
        },
        migratePool: {
          query: async (sql: string) =>
            sql.includes('pg_trigger')
              ? { rows: [{ n: over.triggers ?? '3' }] }
              : { rows: [{ backend: over.backend ?? 'mongo', readonly: over.readonly ?? true }] },
        },
      } as never
      const js = { progress: { mode: 'online', phase: 'online_ready', postLoad: { appliedAt: 'x' }, verify: { ok: true, token: 't' } } } as never
      return { rt, js }
    }
    const opts = { log: () => undefined, lagMaxMs: 1000, fenceArmed: true, shadowClean: true }

    it('names every item that does not hold, and each of them is checked', async () => {
      const { rt, js } = world({ fence: { mode: 'draining', epoch: 1 }, backend: 'postgres', readonly: false, triggers: '0', status: { members: [{ health: 0, state: 8 }], writeMajorityCount: 1 } })
      const r = await precheck(rt, js, { ...opts, fenceArmed: false, shadowClean: false })
      expect(r.ok).to.equal(false)
      const failed = r.items.filter((i) => !i.ok).map((i) => i.name)
      expect(failed).to.include.members([
        'Mongo fence is active',
        'follower caught up',
        'majority reachable',
        'change-log triggers present',
        'PostgreSQL store fence is closed (not yet the store)',
        'every writer carries the Mongo fence (attested)',
        'shadow reads showed no difference (attested)',
      ])
      expect(r.items.find((i) => i.name === 'verified state')!.ok).to.equal(true)
      expect(r.items.find((i) => i.name === 'post_load applied')!.ok).to.equal(true)
    })

    it('refuses a copy that was never verified', async () => {
      const { rt } = world()
      const r = await precheck(rt, { progress: { mode: 'offline', phase: 'x' } } as never, opts)
      expect(r.items.filter((i) => !i.ok).map((i) => i.name)).to.include.members(['verified state', 'post_load applied'])
    })
  })

  it('unfreezing moves a read-only or draining fence back to active, and says whether it changed anything', async () => {
    const seen: any[] = []
    const rt = (matched: number) => ({ db: () => ({ collection: () => ({ updateOne: async (f: unknown, u: unknown) => (seen.push([f, u]), { matchedCount: matched }) }) }) }) as never
    expect(await unfreeze(rt(1))).to.equal(true)
    expect(seen[0][0]).to.deep.equal({ _id: 'fence', mode: { $in: ['readonly', 'draining'] } })
    expect(seen[0][1].$set.mode).to.equal('active')
    expect(await unfreeze(rt(0))).to.equal(false)
  })

  it('collections keyed by something other than the document id say how a source document is known, and the counters are checked elsewhere', () => {
    const by = (c: string) => MAPPINGS.find((m) => m.collection === c)!
    expect(by('usedPasswordResetLinks').sourceKey!({ _id: 'abc' })).to.equal('abc')
    expect(by('slack_conversations').sourceKey!({ _id: 'def' })).to.equal('def')
    expect(by('outbox_events').sourceKey!({ _id: '6ad8b333aaaaaaaaaa000001' })).to.equal('6ad8b333aaaaaaaaaa000001')
    expect(by('outbox_events').tableId!('6ad8b333aaaaaaaaaa000001')).to.equal(outboxTableId('6ad8b333aaaaaaaaaa000001'))
    expect(by('counters').skip!({})).to.contain('counterViolations')
  })

  it('a catch-up writes its marker first and needs a saved position to follow from', async () => {
    const inserted: any[] = []
    const rt = { cfg: { mongoDb: 'es' }, db: () => ({ collection: (n: string) => ({ insertOne: async (d: unknown) => (inserted.push([n, d]), {}) }) }) } as never
    let err: Error | undefined
    await catchUp(rt, { progress: {} } as never, () => undefined, 1000).catch((e: Error) => (err = e))
    expect(inserted).to.have.length(1)
    expect(inserted[0][0]).to.equal('docstore_migrate_marker')
    expect(err?.message).to.contain('follow start')
  })

  it('the online start does nothing while the fence is not active', async () => {
    const rt = (mode: string) => ({ db: () => ({ collection: () => ({ findOne: async () => ({ mode, epoch: 1 }) }) }) }) as never
    for (const mode of ['draining', 'readonly', 'retired']) {
      const r = await onlineStart(rt(mode), { dryRun: false, log: () => undefined })
      expect(r).to.include({ ok: false, stage: 'fence' })
      expect(r.message).to.contain(`'${mode}'`)
    }
  })
})
