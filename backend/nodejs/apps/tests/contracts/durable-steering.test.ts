import 'reflect-metadata'
import fs from 'fs'
import path from 'path'
import { expect } from 'chai'
import { updateGoldens } from './support/golden'
import { foldTimeline } from '../../src/modules/agent-harness/projection/timeline'
import { buildCompletion, runTranscript } from '../../src/modules/agent-harness/projection/transcript'
import { AguiFrame } from '../../src/modules/agent-harness/projection/frames'
import {
  JOURNALS_DIR,
  STEERED_SCENARIOS,
  journalOnlySteps,
  liveSteps,
  normalizeFrames,
  project,
  readJournal,
  sseBody,
} from './support/durable'

/**
 * P1b T2 (ST-8): a steered run's journal (recorded by the Python goldens,
 * `test_durable_journals.py`) projected by Node. The steering message is shown
 * where the model saw it: a `user_input` frame after what the run did before
 * the boundary and before the turn that reads it, and a `user_input` part at
 * the same place in the saved conversation (live parts == persisted, L-6).
 */
const isInput = (frame: AguiFrame): boolean => frame.type === 'CUSTOM' && frame['name'] === 'user_input'

describe('contract: a steered run shows the steering message where the model saw it (T2)', () => {
  for (const [scenario, text] of Object.entries(STEERED_SCENARIOS)) {
    it(`${scenario}: one user_input frame, before the turn that reads it; live equals journal-only`, () => {
      const journal = readJournal(scenario)
      const live = project(journal, liveSteps(journal)).map((f) => f.frame)
      const only = project(journal, journalOnlySteps(journal)).map((f) => f.frame)
      expect(normalizeFrames(live)).to.deep.equal(normalizeFrames(only))
      const inputs = live.filter(isInput)
      expect(inputs).to.have.length(1)
      expect(inputs[0]!['value']).to.deep.equal({ messageSeq: 1, content: text })
      const at = live.findIndex(isInput)
      const starts = live.map((f, i) => (f.type === 'TEXT_MESSAGE_START' ? i : -1)).filter((i) => i >= 0)
      expect(starts, 'a message before and one after').to.have.length(2)
      expect(starts[0]!).to.be.lessThan(at)
      expect(starts[1]!).to.be.greaterThan(at)
      // What the run did before the boundary is already shown: the tool's result, or the first answer ended.
      const before = live.slice(0, at).map((f) => f.type)
      expect(before).to.include(scenario.endsWith('during_tool') ? 'TOOL_CALL_RESULT' : 'TEXT_MESSAGE_END')
      // The model is called again on it: the status says so before the next message.
      const between = live.slice(at, starts[1]).map((f) => (f.type === 'STATE_SNAPSHOT' ? (f['snapshot'] as Record<string, unknown>)['status'] : f.type))
      expect(between).to.include('calling_llm')
    })

    it(`${scenario}: the saved parts place it the same way, and the final snapshot is those parts (L-6)`, () => {
      const journal = readJournal(scenario)
      const completion = buildCompletion(foldTimeline(journal.events, journal.runId)) as { parts: Array<Record<string, unknown>> }
      const parts = completion.parts
      expect(parts.map((p) => p['type'])).to.deep.equal(
        scenario.endsWith('during_tool') ? ['text', 'tool_call', 'user_input', 'text'] : ['text', 'user_input', 'text'],
      )
      const input = parts.find((p) => p['type'] === 'user_input')!
      expect(input).to.deep.equal({ type: 'user_input', content: text, messageSeq: 1, runId: journal.runId })
      expect(parts[parts.length - 1]!['isFinal']).to.equal(true)
      const frames = project(journal, liveSteps(journal))
      const final = frames.find((f) => f.frame.type === 'STATE_SNAPSHOT' && (f.frame['snapshot'] as Record<string, unknown>)['final'] === true)
      expect((final!.frame['snapshot'] as { parts: unknown[] }).parts).to.deep.equal(parts)
      const transcript = runTranscript(journal.events, journal.runId)
      expect(transcript?.kind === 'answer' ? transcript.completion['parts'] : null).to.deep.equal(parts)
      const target = path.join(JOURNALS_DIR, `${scenario}.parts.json`)
      const body = `${JSON.stringify({ scenario, parts }, null, 1)}\n`
      if (updateGoldens()) fs.writeFileSync(target, body)
      else expect(body, `${path.basename(target)} drifted; record it with UPDATE_GOLDENS=1`).to.equal(fs.readFileSync(target, 'utf8'))
    })

    it(`${scenario}: the owner's frames the web client folds (frontend fixture)`, () => {
      // The frontend's handler test folds these frames and must arrive at the saved parts.
      const journal = readJournal(scenario)
      const frames = project(journal, liveSteps(journal)).map((f) => f.frame)
      const target = path.join(JOURNALS_DIR, `${scenario}.frames.json`)
      const body = `${JSON.stringify({ scenario, frames }, null, 1)}\n`
      if (updateGoldens()) fs.writeFileSync(target, body)
      else expect(body, `${path.basename(target)} drifted; record it with UPDATE_GOLDENS=1`).to.equal(fs.readFileSync(target, 'utf8'))
    })

    it(`${scenario}: shared viewers, Slack and MCP never receive it (T1e table)`, () => {
      const journal = readJournal(scenario)
      for (const audience of ['shared', 'slack', 'external'] as const) {
        for (const steps of [liveSteps(journal), journalOnlySteps(journal)]) {
          const body = sseBody(project(journal, steps, null, audience))
          expect(body, audience).not.to.contain('user_input')
          expect(body, audience).not.to.contain(text)
        }
      }
    })
  }

  it('a message queued and never taken (the run was stopped) is saved after the answer, marked not consumed', () => {
    const journal = readJournal('agent_steered_during_tool')
    const consumed = journal.events.find((e) => e.type === 'input.consumed')!.seq
    const events = journal.events.filter((e) => e.seq !== consumed)
    const parts = (buildCompletion(foldTimeline(events, journal.runId)) as { parts: Array<Record<string, unknown>> }).parts
    expect(parts.map((p) => p['type'])).to.deep.equal(['text', 'tool_call', 'text', 'user_input'])
    expect(parts[parts.length - 1]).to.include({ consumed: false, messageSeq: 1 })
  })

  it('C-21b: a message with attachments shows them in its frame and its part, to the owner only', () => {
    const journal = readJournal('agent_steered_during_tool')
    const file = { recordId: 'rec-1', virtualRecordId: 'vr-1', recordName: 'plan.pdf', mimeType: 'application/pdf' }
    const events = journal.events.map((e) =>
      e.type === 'input.queued' ? { ...e, data: { ...(e.data as Record<string, unknown>), attachments: [file] } } : e,
    )
    const withFile = { ...journal, events }
    const [frame] = project(withFile, liveSteps(withFile)).map((f) => f.frame).filter(isInput)
    expect(frame!['value']).to.deep.include({ messageSeq: 1, attachments: [file] })
    const parts = (buildCompletion(foldTimeline(events, journal.runId)) as { parts: Array<Record<string, unknown>> }).parts
    expect(parts.find((p) => p['type'] === 'user_input')).to.deep.include({ attachments: [file] })
    for (const audience of ['shared', 'slack', 'external'] as const) {
      expect(sseBody(project(withFile, liveSteps(withFile), null, audience))).not.to.contain('plan.pdf')
    }
  })
})
