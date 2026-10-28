/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as cmConfig from '../../../../src/modules/configuration_manager/config/config'
import * as encryptorModule from '../../../../src/libs/encryptor/encryptor'
import { setPlatformSettings } from '../../../../src/modules/configuration_manager/controller/cm_controller'
import {
  GENERATION_KEY,
  MEMBERSHIP_DIRECTORY,
  evaluateMembership,
  nextGeneration,
  savePutsArtifactReadsAtRisk,
} from '../../../../src/modules/configuration_manager/utils/artifactRollout'

const SETTINGS = '/services/platform/settings'
const NOW = 1_700_000_000_000

function enc(v: unknown): string {
  return `encrypted:${JSON.stringify(v)}`
}

/** An in-memory key-value store holding the settings blob and the membership entries. */
function store(settings: unknown, entries: Record<string, unknown> = {}): any {
  const data: Record<string, unknown> = { ...entries }
  if (settings !== null) data[SETTINGS] = enc(settings)
  const kv = {
    data,
    get: sinon.stub().callsFake(async (k: string) => (k in data ? data[k] : null)),
    set: sinon.stub().callsFake(async (k: string, v: unknown) => {
      data[k] = v
    }),
    listKeysInDirectory: sinon.stub().callsFake(async (d: string) => Object.keys(data).filter((k) => k.startsWith(d))),
  }
  return kv
}

const entry = (over: Record<string, unknown> = {}) => JSON.stringify({ checkedAt: NOW - 1_000, untagged: [], groupsChecked: ['g'], ...over })

function res(): any {
  const r: any = { statusCode: 0, body: null }
  r.status = (c: number) => ((r.statusCode = c), r)
  r.json = (b: unknown) => ((r.body = b), r)
  r.end = () => r
  return r
}

async function save(kv: any, featureFlags: Record<string, boolean>): Promise<{ next: sinon.SinonStub; res: any }> {
  const next = sinon.stub()
  const r = res()
  await setPlatformSettings(kv)({ body: { fileUploadMaxSizeBytes: 1, featureFlags }, user: {} } as any, r, next)
  return { next, res: r }
}

function saved(kv: any): any {
  return JSON.parse((kv.data[SETTINGS] as string).replace('encrypted:', ''))
}

describe('setPlatformSettings: the artifact rollout rules', () => {
  let clock: sinon.SinonFakeTimers
  beforeEach(() => {
    clock = sinon.useFakeTimers({ now: NOW, toFake: ['Date'] })
    sinon.stub(cmConfig, 'loadConfigurationManagerConfig').returns({ algorithm: 'aes-256-gcm', secretKey: 'k' } as any)
    sinon.stub(encryptorModule.EncryptionService, 'getInstance').returns({
      encrypt: (v: string) => `encrypted:${v}`,
      decrypt: (v: string) => v.replace('encrypted:', ''),
    } as any)
  })
  afterEach(() => {
    clock.restore()
    sinon.restore()
  })

  const key = (holder: string) => `${MEMBERSHIP_DIRECTORY}${holder}`

  describe('the 409 interlock (T49′, T49″, T52′, T54)', () => {
    const turnsOn: Array<[string, Record<string, boolean>, Record<string, boolean>]> = [
      ['off to shadow', {}, { ENABLE_ARTIFACT_READ_SHADOW: true }],
      ['off to read', {}, { ENABLE_ARTIFACT_READ: true }],
      ['shadow to read', { ENABLE_ARTIFACT_READ_SHADOW: true }, { ENABLE_ARTIFACT_READ: true }],
      ['shadow to read plus shadow', { ENABLE_ARTIFACT_READ_SHADOW: true }, { ENABLE_ARTIFACT_READ_SHADOW: true, ENABLE_ARTIFACT_READ: true }],
    ]

    for (const [name, before, after] of turnsOn) {
      it(`${name}: refused with no entry, a stale entry or an untagged member; accepted with a fresh tagged-only entry`, async () => {
        for (const entries of [
          {},
          { [key('a')]: entry({ checkedAt: NOW - 91_000 }) },
          { [key('a')]: entry({ untagged: ['indexing-0'] }) },
        ]) {
          const kv = store({ featureFlags: before }, entries)
          const { next } = await save(kv, after)
          expect(next.calledOnce, JSON.stringify(entries)).to.be.true
          expect(next.firstCall.args[0].statusCode).to.equal(409)
          expect(kv.set.called).to.be.false
        }
        const ok = store({ featureFlags: before }, { [key('a')]: entry() })
        const { next, res: r } = await save(ok, after)
        expect(next.called).to.be.false
        expect(r.statusCode).to.equal(200)
      })
    }

    it('names the untagged members in the refusal', async () => {
      const kv = store({ featureFlags: {} }, { [key('a')]: entry({ untagged: ['indexing-0', 'indexing-1'] }) })
      const { next } = await save(kv, { ENABLE_ARTIFACT_READ: true })
      expect(next.firstCall.args[0].message).to.include('indexing-0').and.to.include('indexing-1')
    })

    it('an ex-leader\'s clean write after the new leader\'s untagged write still refuses (T52′)', async () => {
      const kv = store(
        { featureFlags: {} },
        {
          [key('new-leader')]: entry({ checkedAt: NOW - 5_000, untagged: ['old-pod'] }),
          [key('ex-leader')]: entry({ checkedAt: NOW - 1_000 }),
        },
      )
      const { next } = await save(kv, { ENABLE_ARTIFACT_READ: true })
      expect(next.firstCall.args[0].statusCode).to.equal(409)
    })

    it('reads an entry Redis stored as a JSON string of JSON (T52)', async () => {
      const doubled = JSON.stringify(entry())
      const kv = store({ featureFlags: {} }, { [key('a')]: doubled })
      const { next, res: r } = await save(kv, { ENABLE_ARTIFACT_READ: true })
      expect(next.called).to.be.false
      expect(r.statusCode).to.equal(200)
    })

    it('a leader on another pod: the entry any pod wrote is the one Node reads (T52)', async () => {
      const kv = store({ featureFlags: {} }, { [key('pod-b-leader')]: entry() })
      const { next, res: r } = await save(kv, { ENABLE_ARTIFACT_READ_SHADOW: true })
      expect(next.called).to.be.false
      expect(r.statusCode).to.equal(200)
    })

    it('never refuses turning flags off, read to shadow, or a save that leaves the flags as they are (T54)', async () => {
      const untagged = { [key('a')]: entry({ untagged: ['x'] }) }
      for (const [before, after] of [
        [{ ENABLE_ARTIFACT_READ: true }, {}],
        [{ ENABLE_ARTIFACT_READ_SHADOW: true }, {}],
        [{ ENABLE_ARTIFACT_READ: true }, { ENABLE_ARTIFACT_READ_SHADOW: true }],
        [{ ENABLE_ARTIFACT_READ: true }, { ENABLE_ARTIFACT_READ: true, ENABLE_OTHER: true }],
        [{ ENABLE_ARTIFACT_READ_SHADOW: true }, { ENABLE_ARTIFACT_READ_SHADOW: true }],
        [{}, {}],
      ] as Array<[Record<string, boolean>, Record<string, boolean>]>) {
        const { next } = await save(store({ featureFlags: before }, untagged), after)
        expect(next.called, JSON.stringify([before, after])).to.be.false
      }
    })
  })

  describe('the generation', () => {
    it('is stamped when a flag turns on, kept while one stays on, and null when both are off', async () => {
      const entries = { [key('a')]: entry() }
      const kv = store({ featureFlags: {} }, entries)

      await save(kv, { ENABLE_ARTIFACT_READ_SHADOW: true })
      const first = saved(kv)[GENERATION_KEY]
      expect(first).to.be.a('string').and.not.empty

      await save(kv, { ENABLE_ARTIFACT_READ_SHADOW: true, OTHER: true })
      expect(saved(kv)[GENERATION_KEY]).to.equal(first)
      await save(kv, { ENABLE_ARTIFACT_READ: true })
      expect(saved(kv)[GENERATION_KEY]).to.equal(first)

      await save(kv, {})
      expect(saved(kv)[GENERATION_KEY]).to.equal(null)
      await save(kv, { ENABLE_ARTIFACT_READ: true })
      expect(saved(kv)[GENERATION_KEY]).to.not.equal(first)
    })

    it('is never taken from the request body', async () => {
      const kv = store({ featureFlags: {} }, { [key('a')]: entry() })
      const next = sinon.stub()
      await setPlatformSettings(kv)(
        { body: { fileUploadMaxSizeBytes: 1, featureFlags: { ENABLE_ARTIFACT_READ: true }, [GENERATION_KEY]: 'chosen-by-caller' }, user: {} } as any,
        res(),
        next,
      )
      expect(saved(kv)[GENERATION_KEY]).to.not.equal('chosen-by-caller')
    })

    it('is re-stamped when a flag is on and the stored blob lost it (an older save dropped it)', async () => {
      const kv = store({ featureFlags: { ENABLE_ARTIFACT_READ: true } })
      await save(kv, { ENABLE_ARTIFACT_READ: true })
      expect(saved(kv)[GENERATION_KEY]).to.be.a('string').and.not.empty
    })
  })

  describe('the rules as pure functions', () => {
    it('only a save that turns read on, or shadow on from both off, needs the check', () => {
      expect(savePutsArtifactReadsAtRisk({}, { ENABLE_ARTIFACT_READ: true })).to.be.true
      expect(savePutsArtifactReadsAtRisk({}, { ENABLE_ARTIFACT_READ_SHADOW: true })).to.be.true
      expect(savePutsArtifactReadsAtRisk({ ENABLE_ARTIFACT_READ_SHADOW: true }, { ENABLE_ARTIFACT_READ: true })).to.be.true
      expect(savePutsArtifactReadsAtRisk({ ENABLE_ARTIFACT_READ: true }, { ENABLE_ARTIFACT_READ_SHADOW: true })).to.be.false
      expect(savePutsArtifactReadsAtRisk({ ENABLE_ARTIFACT_READ: true }, {})).to.be.false
      expect(savePutsArtifactReadsAtRisk({ enable_artifact_read: true }, { ENABLE_ARTIFACT_READ: true })).to.be.false
    })

    it('a stale entry never hides a fresh one, and no fresh entry is stale', () => {
      expect(evaluateMembership([], NOW).reason).to.equal('stale')
      expect(evaluateMembership([{ checkedAt: NOW - 100_000, untagged: [] }], NOW).reason).to.equal('stale')
      expect(evaluateMembership([{ checkedAt: NOW - 100_000, untagged: ['x'] }, { checkedAt: NOW, untagged: [] }], NOW).ok).to.be.true
      expect(evaluateMembership([null, 'junk', {}], NOW).reason).to.equal('stale')
      expect(nextGeneration('g', {}, { ENABLE_ARTIFACT_READ: true })).to.not.equal('g')
    })
  })
})
