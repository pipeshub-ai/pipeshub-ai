import 'reflect-metadata'
import fs from 'fs'
import path from 'path'
import { expect } from 'chai'
import { GOLDEN_DIR, updateGoldens } from './support/golden'
import { foldTimeline } from '../../src/modules/agent-harness/projection/timeline'
import { buildCompletion } from '../../src/modules/agent-harness/projection/transcript'
import {
  DURABLE_SCENARIOS,
  JOURNALS_DIR,
  journalOnlySteps,
  liveSteps,
  normalizeFrames,
  parseSse,
  project,
  readJournal,
} from './support/durable'

/**
 * D-5 at browser-frame level: each P0.7 agent and chat scenario, run durably
 * (`contracts/harness/vectors/journals/`), projected by Node, gives the frames
 * the legacy stream gave (`tests/contracts/golden/<scenario>.sse`) modulo ids
 * and delta chunking — live (Redis) and journal-only (no Redis, L-5).
 */
const compareOrWriteAt = (target: string, actual: string): void => {
  if (updateGoldens()) {
    fs.writeFileSync(target, actual)
    return
  }
  expect(fs.existsSync(target), `missing ${path.basename(target)}; record it with UPDATE_GOLDENS=1`).to.equal(true)
  expect(actual, `${path.basename(target)} drifted`).to.equal(fs.readFileSync(target, 'utf8'))
}

describe('contract: durable runs stream the legacy browser frames (D-5)', () => {
  for (const scenario of DURABLE_SCENARIOS) {
    const legacy = (): ReturnType<typeof normalizeFrames> =>
      normalizeFrames(parseSse(fs.readFileSync(path.join(GOLDEN_DIR, `${scenario}.sse`), 'utf8')).map((f) => f.frame))

    it(`${scenario}: live`, () => {
      const journal = readJournal(scenario)
      const frames = project(journal, liveSteps(journal))
      expect(normalizeFrames(frames.map((f) => f.frame))).to.deep.equal(legacy())
    })

    it(`${scenario}: journal only`, () => {
      const journal = readJournal(scenario)
      const frames = project(journal, journalOnlySteps(journal))
      expect(normalizeFrames(frames.map((f) => f.frame))).to.deep.equal(legacy())
    })
  }
})

describe('contract: the transcript projection equals the live parts and the legacy transcript (L-6)', () => {
  for (const scenario of DURABLE_SCENARIOS) {
    it(scenario, () => {
      const journal = readJournal(scenario)
      const completion = buildCompletion(foldTimeline(journal.events, journal.runId)) as { parts?: unknown[] } | null
      // A pre-run clarification's completion has no parts (legacy's neither); the conversation stores none.
      // A run that failed without an answer has no completion and no final snapshot.
      const parts = completion?.parts ?? []
      const frames = project(journal, liveSteps(journal))
      const final = frames.find((f) => f.frame.type === 'STATE_SNAPSHOT' && (f.frame.snapshot as Record<string, unknown>).final === true)
      expect(final === undefined, 'a final snapshot exactly when the run answered').to.equal(completion === null)
      expect((final?.frame.snapshot as { parts?: unknown[] } | undefined)?.parts ?? [], 'live final parts').to.deep.equal(parts)

      const legacy = JSON.parse(fs.readFileSync(path.join(GOLDEN_DIR, `${scenario}.transcript.json`), 'utf8')) as {
        messages: Array<{ messageType: string; parts?: unknown[] }>
      }
      // Legacy saves an error answer as an `error` message, with its parts.
      const persisted = legacy.messages.filter((m) => m.messageType === 'bot_response' || m.messageType === 'error').at(-1)?.parts ?? []
      const sameRunIds = (parts: unknown): unknown =>
        JSON.parse(
          JSON.stringify(parts)
            .replace(/"runId":"[^"]+"/g, '"runId":"<run>"')
            .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|uuid-\d+/g, '<uuid>'),
        )
      // What only a durable run can save (CTL-3b): its plan as a part and each step's timing from the journal.
      const legacyShaped = (parts as Array<Record<string, unknown>>)
        .filter((part) => part['type'] !== 'plan')
        .map((original) => {
          const part = { ...original }
          delete part['durationMs']
          delete part['elapsedMs']
          return part
        })
      expect(sameRunIds(legacyShaped), 'parts equal the legacy persisted parts').to.deep.equal(sameRunIds(persisted))

      // The Python fold reads the same journal and must agree with these parts
      // (backend/python/tests/contracts/test_durable_journals.py).
      compareOrWriteAt(path.join(JOURNALS_DIR, `${scenario}.parts.json`), `${JSON.stringify({ scenario, parts }, null, 1)}\n`)
    })
  }
})
