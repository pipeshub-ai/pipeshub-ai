import 'reflect-metadata'
import fs from 'fs'
import path from 'path'
import { expect } from 'chai'
import { updateGoldens } from './support/golden'
import { GoldenJournal, JOURNALS_DIR, journalOnlySteps, liveSteps, project, sseBody } from './support/durable'
import { canonicalJsonBytes } from '../../src/modules/agent-harness/contracts/canonical-json'
import { JournalEvent, validateJournalAppend } from '../../src/modules/agent-harness/domain/journal'
import { inlineContentRef } from '../../src/modules/agent-harness/domain/run-content'
import { foldTimeline } from '../../src/modules/agent-harness/projection/timeline'
import { buildCompletion, runTranscript } from '../../src/modules/agent-harness/projection/transcript'

/**
 * U3: two durable runs as the UI meets them, live and after a reload. An
 * engine run (its task's progress lines, a held call approved from its card,
 * workspace versions, a draft then final file) and a loop parent that hands a
 * task to an engine child. These journals are written here, from the shapes
 * E1's presenter and P6.1's child events have (not recorded from a worker);
 * the frames and parts are the Node projector's and transcript's. The
 * frontend's vitest and Playwright specs read all three files.
 */

const SESSION = 'ses-u3'
const TS = '2026-10-04T12:00:00.000Z'
const IX = 'ix.approve.k0f1e2d3c4b5a69788796a5b4c3d2e1f00112233'
const sha = (c: string): string => `sha256:${c.repeat(64)}`

function journalOf(runId: string, query: string, specs: Array<[string, Record<string, unknown>, 'node' | 'worker']>): GoldenJournal {
  const events = specs.map(
    ([type, data, writer], index): JournalEvent =>
      ({
        orgId: 'org-1',
        sessionId: SESSION,
        streamId: SESSION,
        seq: index + 1,
        runId,
        attemptId: writer === 'worker' ? `${runId}.a1` : undefined,
        fence: writer === 'worker' ? 1 : undefined,
        type,
        schemaVersion: 1,
        critical: !type.startsWith('x.'),
        ts: TS,
        writer,
        visibility: 'owner',
        data,
      }) as JournalEvent,
  )
  return { scenario: '', query, runId, sessionId: SESSION, events, live: [] }
}

const inline = (value: unknown) => inlineContentRef(canonicalJsonBytes(value))

function engineRun(): GoldenJournal {
  const run = 'run-u3-engine'
  const line = (kind: string, text: string, data: Record<string, unknown> = {}, seq = 0): [string, Record<string, unknown>, 'worker'] => [
    'x.pipeshub.engine.progress',
    { tool: 'code_task', taskId: run, kind, text, data, turnId: 'trn_1', seq },
    'worker',
  ]
  const card = (lifecycle: string, version: number): [string, Record<string, unknown>, 'worker'] => [
    'x.pipeshub.artifact.presented',
    {
      artifactId: 'art-q3',
      fileName: 'q3-report.md',
      mimeType: 'text/markdown',
      sizeBytes: 64,
      artifactType: 'DOCUMENT',
      isTemporary: false,
      recordId: 'art-q3',
      version,
      visibility: 'VISIBLE',
      card: true,
      lifecycle,
    },
    'worker',
  ]
  const answer = 'The Q3 report is in out/q3-report.md.'
  const journal = journalOf(run, 'Write the Q3 report and file it', [
    ['run.admitted', { runKind: 'interactive', principalRef: 'p_user' }, 'node'],
    ['run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }, 'node'],
    ['message.completed', { role: 'user', textRef: inline('Write the Q3 report and file it'), isFinal: false }, 'worker'],
    ['turn.started', { turn: 1 }, 'worker'],
    line('started', 'Write the Q3 report and file it', {}, 1),
    line('command', 'python report.py', { callId: 'c1' }, 2),
    line('output', 'wrote out/q3-report.md\n', { callId: 'c1', stream: 'stdout', truncated: false }, 3),
    line('command_done', '', { callId: 'c1', exitCode: 0, status: 'completed' }, 4),
    line('file', 'out/q3-report.md', { change: 'added' }, 5),
    line('version', '', { revision: 1, reason: 'autosave', files: 1, changed: 1, omitted: 0 }, 6),
    line('diff', 'out/q3-report.md', { change: 'added', sizeBytes: 64, mimeType: 'text/markdown', diff: '@@ -0,0 +1,2 @@\n+# Q3\n+Revenue up 4%\n', truncated: false }, 7),
    ['workspace.committed', { workspaceId: `ws:${SESSION}`, revision: 1, changed: ['/out/q3-report.md'] }, 'worker'],
    card('draft', 1),
    line('artifact', 'q3-report.md', { artifactId: 'art-q3', version: 1, mimeType: 'text/markdown', sizeBytes: 64, lifecycle: 'draft' }, 8),
    line('approval', 'jira_create_issue', { interactionId: IX, toolName: 'jira_create_issue', argsJson: '{"summary":"Q3 report ready"}' }, 9),
    ['interaction.requested', { interactionId: IX, kind: 'approval', bindingDigest: sha('a') }, 'worker'],
    ['interaction.resolved', { interactionId: IX, decision: 'approved', by: 'p_user', channel: 'web' }, 'node'],
    line('tool', 'jira_create_issue', {}, 10),
    line('version', '', { revision: 2, reason: 'turn_end', files: 1, changed: 1, omitted: 0 }, 11),
    ['workspace.committed', { workspaceId: `ws:${SESSION}`, revision: 2, changed: ['/out/q3-report.md'] }, 'worker'],
    card('final', 2),
    line('artifact', 'q3-report.md', { artifactId: 'art-q3', version: 2, mimeType: 'text/markdown', sizeBytes: 64, lifecycle: 'final' }, 12),
    [
      'model.response',
      {
        messageRef: inline({ text: answer, toolCalls: [] }),
        toolCalls: [],
        usage: { inputTokens: 1200, outputTokens: 80 },
        provider: 'engine',
        transport: 'engine',
      },
      'worker',
    ],
    ['turn.completed', { turn: 1 }, 'worker'],
    ['message.completed', { role: 'assistant', textRef: inline(answer), isFinal: true }, 'worker'],
    line('finished', answer, { success: true }, 13),
    ['x.pipeshub.answer.finalized', { outcome: 'answered', answer, partTexts: [answer], citations: [] }, 'worker'],
    ['run.completed', { outcome: 'answered' }, 'node'],
  ])
  return { ...journal, scenario: 'u3_engine_run' }
}

function parentWithEngineChild(): GoldenJournal {
  const run = 'run-u3-parent'
  const answer = 'Done: the engine wrote the analysis.'
  const journal = journalOf(run, 'Analyse the Q3 numbers with a code task', [
    ['run.admitted', { runKind: 'interactive', principalRef: 'p_user' }, 'node'],
    ['run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }, 'node'],
    ['message.completed', { role: 'user', textRef: inline('Analyse the Q3 numbers with a code task'), isFinal: false }, 'worker'],
    ['turn.started', { turn: 1 }, 'worker'],
    [
      'model.response',
      {
        messageRef: inline({ text: 'Handing this to a code task.', toolCalls: [{ id: 'call-ct', name: 'code_task', args: { goal: 'Analyse in/q3.csv' } }] }),
        toolCalls: [{ callId: 'call-ct', modelName: 'code_task', argsDigest: sha('c') }],
        usage: { inputTokens: 300, outputTokens: 20 },
        provider: 'p',
        transport: 't',
      },
      'worker',
    ],
    ['child.spawned', { childRunId: 'run-u3-child', invocationId: 't1.0', taskId: 'analysis', grantDigest: sha('d') }, 'node'],
    [
      'child.completed',
      {
        childRunId: 'run-u3-child',
        resultRef: { ref: 'inline:e30=', hash: sha('e'), size: 2 },
        labels: { integrity: 'internal' },
        outcome: 'answered',
      },
      'node',
    ],
    ['tool.outcome', { invocationId: 't1.0', status: 'ok', resultRef: inline({ content: 'analysis.csv written' }), labels: { integrity: 'internal' } }, 'worker'],
    ['turn.started', { turn: 2 }, 'worker'],
    [
      'model.response',
      {
        messageRef: inline({ text: answer, toolCalls: [] }),
        toolCalls: [],
        usage: { inputTokens: 400, outputTokens: 12 },
        provider: 'p',
        transport: 't',
      },
      'worker',
    ],
    ['x.pipeshub.answer.finalized', { outcome: 'answered', answer, partTexts: ['Handing this to a code task.', answer], citations: [] }, 'worker'],
    ['run.completed', { outcome: 'answered' }, 'node'],
  ])
  return { ...journal, scenario: 'u3_parent_with_engine_child' }
}

function golden(name: string, body: unknown): void {
  const target = path.join(JOURNALS_DIR, name)
  const text = `${JSON.stringify(body, null, 1)}\n`
  if (updateGoldens()) fs.writeFileSync(target, text)
  else expect(text, `${name} drifted; record it with UPDATE_GOLDENS=1`).to.equal(fs.readFileSync(target, 'utf8'))
}

describe('contract: a durable run the UI shows again after a reload (U3)', () => {
  for (const journal of [engineRun(), parentWithEngineChild()]) {
    const { scenario } = journal

    it(`${scenario}: the journal, the owner's frames and the saved parts (frontend fixtures)`, () => {
      golden(`${scenario}.journal.json`, journal)
      const frames = project(journal, liveSteps(journal)).map((f) => ({ id: f.id, frame: f.frame }))
      golden(`${scenario}.frames.json`, { scenario, frames })
      const completion = buildCompletion(foldTimeline(journal.events, journal.runId)) as { parts: unknown[]; answer: string }
      golden(`${scenario}.parts.json`, { scenario, answer: completion.answer, parts: completion.parts })
    })

    it(`${scenario}: the saved conversation is the final snapshot (L-6), live or journal only`, () => {
      const transcript = runTranscript(journal.events, journal.runId)
      expect(transcript?.kind).to.equal('answer')
      const completion = transcript?.kind === 'answer' ? transcript.completion : {}
      for (const steps of [liveSteps(journal), journalOnlySteps(journal)]) {
        const final = project(journal, steps).find(
          (f) => f.frame.type === 'STATE_SNAPSHOT' && (f.frame['snapshot'] as Record<string, unknown>)['final'] === true,
        )
        expect((final?.frame['snapshot'] as Record<string, unknown>)['parts']).to.deep.equal(completion['parts'])
      }
    })
  }

  it('both journals are what Node accepts from their writers (every event against its schema)', () => {
    const refused: string[] = []
    for (const journal of [engineRun(), parentWithEngineChild()]) {
      for (const event of journal.events) {
        try {
          validateJournalAppend({
            orgId: event.orgId,
            sessionId: event.sessionId,
            streamId: event.streamId,
            runId: event.runId,
            ...(event.attemptId !== undefined ? { attemptId: event.attemptId } : {}),
            ...(event.fence !== undefined ? { fence: event.fence } : {}),
            writer: event.writer,
            events: [{ type: event.type, schemaVersion: event.schemaVersion, critical: event.critical, visibility: event.visibility, data: event.data }],
          } as Parameters<typeof validateJournalAppend>[0])
        } catch (error) {
          refused.push(`${journal.scenario} ${event.type}: ${(error as Error).message}`)
        }
      }
    }
    expect(refused).to.deep.equal([])
  })

  it('u3_engine_run: the card, the approval and the final file are what a reload finds', () => {
    const journal = engineRun()
    const parts = (buildCompletion(foldTimeline(journal.events, journal.runId)) as { parts: Array<Record<string, unknown>> }).parts
    expect(parts.map((p) => p['type'])).to.deep.equal(['engine_task', 'approval', 'text'])
    expect(parts[1]).to.include({ status: 'approved', toolName: 'jira_create_issue' })
    const live = project(journal, liveSteps(journal)).map((f) => f.frame)
    const progress = live.filter((f) => f.type === 'CUSTOM' && f['name'] === 'engine_progress').map((f) => (f['value'] as Record<string, unknown>)['kind'])
    expect(progress).to.deep.equal((parts[0]?.['frames'] as Array<Record<string, unknown>>).map((f) => f['kind']))
    expect(live.filter((f) => f.type === 'CUSTOM' && f['name'] === 'approval_requested')).to.have.length(1)
  })

  it('u3_engine_run: shared, Slack and MCP audiences get no progress, approvals or drafts', () => {
    const journal = engineRun()
    for (const audience of ['shared', 'slack', 'external'] as const) {
      const body = sseBody(project(journal, liveSteps(journal), null, audience))
      expect(body, audience).not.to.contain('engine_progress')
      expect(body, audience).not.to.contain('approval_requested')
      expect(body, audience).not.to.contain('"lifecycle":"draft"')
    }
  })

  it('u3_parent_with_engine_child: the child is a sub_agent part after the delegating turn', () => {
    const journal = parentWithEngineChild()
    const parts = (buildCompletion(foldTimeline(journal.events, journal.runId)) as { parts: Array<Record<string, unknown>> }).parts
    expect(parts.map((p) => p['type'])).to.deep.equal(['text', 'tool_call', 'sub_agent', 'text'])
    expect(parts[2]).to.deep.equal({
      type: 'sub_agent',
      runId: 'run-u3-child',
      parentRunId: 'run-u3-parent',
      roleName: 'pipeshub-subagent-analysis',
      // CTL-6: a code_task child is named by its goal.
      codeTaskGoal: 'Analyse in/q3.csv',
      status: 'completed',
      parts: [],
    })
  })
})
