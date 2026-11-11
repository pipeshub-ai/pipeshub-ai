import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Logger } from '../../../../src/libs/services/logger.service'
import { flows, startStream, finalAnswer, Flow, RUN } from './streaming-flows'

const SECRET_QUERY = 'merger terms for project nightjar'
const EARLIER_QUERY = 'What changed in the release?'
const EARLIER_ANSWER = 'An older answer.'
const ANSWER_TEXT = 'the kestrel answer'
const ERROR_TEXT = 'the kestrel provider refused'

const withSecretQuery = (flow: Flow): Flow => ({
  ...flow,
  prepare: (store) => {
    const prepared = flow.prepare(store)
    return flow.regenerate ? prepared : { ...prepared, body: { ...prepared.body, query: SECRET_QUERY } }
  },
})

describe('es_controller logs no prompt or history text', () => {
  let logged: string[]

  beforeEach(() => {
    logged = []
    for (const level of ['error', 'warn', 'info', 'debug'] as const) {
      sinon.stub(Logger.prototype, level).callsFake((message: string, meta?: unknown) => {
        logged.push(`${message} ${JSON.stringify(meta ?? null)}`)
      })
    }
  })

  afterEach(() => {
    sinon.restore()
  })

  for (const flow of flows) {
    it(`${flow.name} logs sizes and counts only`, async () => {
      const run = await startStream(withSecretQuery(flow))
      run.ai.send('RUN_FINISHED', finalAnswer(ANSWER_TEXT))
      run.ai.finish()
      await run.res.ended

      expect(run.ai.streamCalls).to.have.length(1)
      const all = logged.join('\n')
      expect(all).to.not.contain('nightjar')
      expect(all).to.not.contain(EARLIER_QUERY)
      expect(all).to.not.contain(EARLIER_ANSWER)
      expect(all).to.not.contain('kestrel')
    })

    it(`${flow.name} logs the code of an error the AI service reports, not its text`, async () => {
      const run = await startStream(flow)
      run.ai.send('RUN_ERROR', { runId: RUN, message: ERROR_TEXT, code: 'llm_provider_error' })
      run.ai.finish()
      await run.res.ended

      const all = logged.join('\n')
      expect(all).to.contain('llm_provider_error')
      expect(all).to.not.contain('kestrel')
    })
  }
})
