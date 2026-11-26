import 'reflect-metadata'
import { expect } from 'chai'
import { AguiFrame } from '../../src/modules/agent-harness/projection/frames'
import { PLAN_FRAME } from '../../src/modules/agent-harness/projection/run-projector'
import { parseStreamId } from '../../src/modules/agent-harness/projection/stream-id'
import { foldTimeline } from '../../src/modules/agent-harness/projection/timeline'
import { buildParts } from '../../src/modules/agent-harness/projection/transcript'
import { withSteps } from '../../src/modules/agent-harness/application/run-timeline-service'
import { journalOnlySteps, liveSteps, parseSse, project, readJournal, sseBody } from './support/durable'

/**
 * UXR-B1 B1-21: a react run that planned with `write_todos` (recorded by the
 * Python goldens, `test_durable_goldens.py` `agent_react_planned`), as Node
 * streams it: the plan before the work, the work under its step, the plan
 * again when the step ends.
 */
const SCENARIO = 'agent_react_planned'
const isPlan = (frame: AguiFrame): boolean => frame.type === 'CUSTOM' && frame['name'] === PLAN_FRAME
const statuses = (frame: AguiFrame) =>
  ((frame['value'] as { steps: Array<{ taskId: string; status: string }> }).steps).map((s) => [s.taskId, s.status])

describe('contract: a planned react run streams its plan (UXR-B1)', () => {
  it('the plan frames bracket the work, and live equals journal-only', () => {
    const journal = readJournal(SCENARIO)
    const live = project(journal, liveSteps(journal)).map((f) => f.frame)
    const only = project(journal, journalOnlySteps(journal)).map((f) => f.frame)
    expect(live.filter(isPlan)).to.deep.equal(only.filter(isPlan))
    const plans = live.filter(isPlan)
    expect(plans.map(statuses)).to.deep.equal([
      [['task-1', 'in_progress'], ['task-2', 'pending']],
      [['task-1', 'completed'], ['task-2', 'in_progress']],
    ])
    expect(plans.map((p) => (p['value'] as { currentTaskId: string | null }).currentTaskId)).to.deep.equal(['task-1', 'task-2'])
    const search = live.findIndex((f) => f.type === 'TOOL_CALL_START' && f['toolCallName'] === 'goldendocs__search_docs')
    expect(live.indexOf(plans[0]!)).to.be.lessThan(search)
    expect(live.indexOf(plans[1]!)).to.be.greaterThan(search)
  })

  it('a resume after the first plan frame still receives the final plan', () => {
    const journal = readJournal(SCENARIO)
    const full = project(journal, liveSteps(journal))
    const first = full.find((f) => isPlan(f.frame))!
    const resumed = parseSse(sseBody(project(journal, liveSteps(journal), parseStreamId(first.id))))
    const plans = resumed.map((f) => f.frame).filter(isPlan)
    expect(plans.map(statuses)).to.deep.equal([[['task-1', 'completed'], ['task-2', 'in_progress']]])
  })

  it('a shared viewer never receives the plan', () => {
    const journal = readJournal(SCENARIO)
    const shared = project(journal, liveSteps(journal), null, 'shared').map((f) => f.frame)
    expect(shared.filter(isPlan)).to.deep.equal([])
  })

  it('the run timeline names the step each call ran under', () => {
    const journal = readJournal(SCENARIO)
    const timeline = foldTimeline(journal.events, journal.runId)
    const calls = withSteps(buildParts(timeline), timeline).filter((p) => p['type'] === 'tool_call')
    const search = calls.find((p) => p['toolName'] === 'goldendocs__search_docs')
    expect(search?.['stepId']).to.equal('task-1')
    expect(timeline.tasks.map((t) => t.status)).to.deep.equal(['completed', 'in_progress'])
  })
})
