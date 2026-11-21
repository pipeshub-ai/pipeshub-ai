import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { SCENARIOS, compareOrWrite, normalize, readUpstream, replay } from './support/golden'

/** The `result` of the AI service's own root RUN_FINISHED, which Node swallows and persists. */
const upstreamResult = (name: string): Record<string, unknown> | undefined => {
  for (const block of readUpstream(name).split('\n\n')) {
    const data = block.split('\n').find((l) => l.startsWith('data: '))
    if (!data) continue
    const parsed = JSON.parse(data.slice('data: '.length)) as Record<string, unknown>
    if (parsed.type === 'RUN_FINISHED' && parsed.result && !parsed.parentRunId) return parsed.result as Record<string, unknown>
  }
  return undefined
}

describe('contract: conversation and messages persisted for legacy chat and agent streams (G2)', () => {
  afterEach(() => {
    sinon.restore()
  })

  for (const scenario of SCENARIOS) {
    it(scenario.name, async () => {
      const run = await replay(scenario)
      const conversation = run.conversation().toObject()
      const messages = run.messages()

      const upstream = upstreamResult(scenario.name)
      const answer = run.answer()
      if (upstream?.parts) {
        // Live and reloaded transcripts render from the same parts[].
        expect(answer?.parts, 'persisted parts[] equal what the live builder produced').to.deep.equal(upstream.parts)
      }

      compareOrWrite(`${scenario.name}.transcript.json`, `${normalize(JSON.stringify({ conversation, messages }, null, 2))}\n`)
    })
  }
})
