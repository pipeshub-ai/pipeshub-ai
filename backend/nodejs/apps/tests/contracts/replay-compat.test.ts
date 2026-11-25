import 'reflect-metadata'
import fs from 'fs'
import path from 'path'
import { expect } from 'chai'
import { JournalEvent } from '../../src/modules/agent-harness/domain/journal'
import { readPinnedSpec } from '../../src/modules/agent-harness/domain/run-spec'
import { foldTimeline } from '../../src/modules/agent-harness/projection/timeline'
import { buildCompletion } from '../../src/modules/agent-harness/projection/transcript'
import { GoldenJournal, JOURNALS_DIR, journalOnlySteps, project } from './support/durable'

/**
 * N-1 read guarantee (P1b RU-3c): what an earlier release wrote, this Node
 * projects. Every corpus under `contracts/harness/vectors/replay-corpus/`
 * (recorded by `contracts/harness/record_replay_corpus.py`) is replayed: each
 * golden journal projects to the parts recorded with it, live and from the
 * journal alone; each real run's journal projects without error; each pin
 * reads through `readPinnedSpec` (upcasters). The Python half is
 * `backend/python/tests/contracts/test_replay_compat.py`.
 */
const CORPORA = path.join(JOURNALS_DIR, '..', 'replay-corpus')

interface Manifest {
  name: string
  journals: string[]
  l4: Array<{ name: string; status: string }>
}

interface StoredRun {
  runId: string
  sessionId: string
  status: string
  events: JournalEvent[]
}

const readJson = (file: string): unknown => JSON.parse(fs.readFileSync(file, 'utf8'))

const corpora = (): Array<{ dir: string; manifest: Manifest }> =>
  fs.existsSync(CORPORA)
    ? fs
        .readdirSync(CORPORA)
        .map((name) => path.join(CORPORA, name))
        .filter((dir) => fs.existsSync(path.join(dir, 'manifest.json')))
        .sort()
        .map((dir) => ({ dir, manifest: readJson(path.join(dir, 'manifest.json')) as Manifest }))
    : []

const partsOf = (events: readonly JournalEvent[], runId: string): unknown[] =>
  (buildCompletion(foldTimeline(events, runId)) as { parts?: unknown[] } | null)?.parts ?? []

describe('contract: replay compatibility, release N read by this build (RU-3c)', () => {
  const found = corpora()

  it('there is a corpus to replay', () => {
    expect(found, `no replay corpus under ${CORPORA}`).to.not.have.length(0)
  })

  for (const { dir, manifest } of found) {
    describe(manifest.name, () => {
      for (const name of manifest.journals) {
        const partsFile = path.join(dir, 'journals', `${name}.parts.json`)
        if (!fs.existsSync(partsFile)) continue
        it(`${name}: projects to the parts recorded with it`, () => {
          const journal = readJson(path.join(dir, 'journals', `${name}.journal.json`)) as GoldenJournal
          const recorded = (readJson(partsFile) as { parts: unknown[] }).parts
          expect(partsOf(journal.events, journal.runId)).to.deep.equal(recorded)
          const frames = project(journal, journalOnlySteps(journal))
          const final = frames.find(
            (f) => f.frame.type === 'STATE_SNAPSHOT' && (f.frame.snapshot as Record<string, unknown>).final === true,
          )
          expect((final?.frame.snapshot as { parts?: unknown[] } | undefined)?.parts ?? []).to.deep.equal(recorded)
        })
      }

      for (const run of manifest.l4) {
        // Their answers are sealed (only Node's content store opens them), so the structure is what replays here.
        it(`${run.name}: a real run's journal projects (${run.status})`, () => {
          const stored = readJson(path.join(dir, 'l4', `${run.name}.journal.json`)) as StoredRun
          const timeline = foldTimeline(stored.events, stored.runId)
          expect(() => buildCompletion(timeline)).to.not.throw()
          expect(timeline.end?.status ?? stored.status).to.equal(stored.status)
        })
      }

      it('the pin reads through this build\'s upcasters', () => {
        const pinned = (readJson(path.join(dir, 'pinned-run-spec.json')) as { pinned: { spec: { schemaVersion: number } } }).pinned
        expect(() => readPinnedSpec(pinned.spec)).to.not.throw()
      })
    })
  }
})
