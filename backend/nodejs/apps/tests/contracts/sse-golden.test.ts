import 'reflect-metadata'
import fs from 'fs'
import os from 'os'
import path from 'path'
import { expect } from 'chai'
import sinon from 'sinon'
import { GOLDEN_DIR, SCENARIOS, compareOrWrite, normalize, replay } from './support/golden'
import { registerUpstreams } from '../../src/libs/http/upstream-call'
import { registerAppUpstreams } from '../../src/modules/tokens_manager/config/config'
import { appConfig as streamingAppConfig } from '../modules/enterprise_search/controller/streaming-flows'

interface Frame {
  event: string
  data: Record<string, unknown>
}

const frames = (body: string): Frame[] =>
  body
    .split('\n\n')
    .filter((block) => block.trim())
    .map((block) => {
      const lines = block.split('\n')
      expect(lines, `frame is one event line and one data line: ${block}`).to.have.length(2)
      expect(lines[0]).to.match(/^event: [A-Z_]+$/)
      expect(lines[1]).to.match(/^data: /)
      const event = (lines[0] as string).slice('event: '.length)
      const data = JSON.parse((lines[1] as string).slice('data: '.length)) as Record<string, unknown>
      expect(data.type, 'data.type matches the event line').to.equal(event)
      return { event, data }
    })

describe('contract: SSE frames the browser receives for legacy chat and agent streams (G1)', () => {
  beforeEach(() => registerAppUpstreams(streamingAppConfig))
  afterEach(() => registerUpstreams({}))

  afterEach(() => {
    sinon.restore()
  })

  for (const scenario of SCENARIOS) {
    it(scenario.name, async () => {
      const run = await replay(scenario)
      const sent = frames(run.res.body)

      const first = sent[0]
      expect(first?.event).to.equal('CUSTOM')
      expect(first?.data.name, 'Node writes conversation_created first').to.equal('conversation_created')
      expect((first?.data.value as Record<string, unknown>).conversationId).to.equal(String(run.conversation()._id))

      const terminal = sent[sent.length - 1] as Frame
      const rootFinished = sent.filter((f) => f.event === 'RUN_FINISHED' && f.data.result !== undefined)
      if (terminal.event === 'RUN_FINISHED') {
        expect(rootFinished, 'exactly one RUN_FINISHED carries a result').to.have.length(1)
        expect(terminal.data.result).to.have.all.keys('conversation', 'meta')
        expect((terminal.data.result as { meta: Record<string, unknown> }).meta).to.have.all.keys(
          'requestId',
          'timestamp',
          'duration',
        )
      } else {
        expect(terminal.event).to.equal('RUN_ERROR')
        expect(rootFinished).to.deep.equal([])
        // Node's own codes (no_response, save_error, stream_error, internal_error, abort)
        // or the AI service's code passed through unchanged.
        expect(terminal.data.code).to.be.a('string').and.match(/^[a-z_]+$/)
      }
      expect(run.res.writesAfterEnd).to.equal(0)

      compareOrWrite(`${scenario.name}.sse`, normalize(run.res.body))
    })
  }

  it('changes a golden only when UPDATE_GOLDENS=1 is set (G4)', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'golden-'))
    const file = path.relative(GOLDEN_DIR, path.join(dir, 'scenario.sse'))
    fs.writeFileSync(path.join(dir, 'scenario.sse'), 'recorded')
    const previous = process.env.UPDATE_GOLDENS
    try {
      delete process.env.UPDATE_GOLDENS
      expect(() => compareOrWrite(file, 'different')).to.throw()
      expect(fs.readFileSync(path.join(dir, 'scenario.sse'), 'utf8')).to.equal('recorded')
      process.env.UPDATE_GOLDENS = '1'
      compareOrWrite(file, 'different')
      expect(fs.readFileSync(path.join(dir, 'scenario.sse'), 'utf8')).to.equal('different')
    } finally {
      if (previous === undefined) delete process.env.UPDATE_GOLDENS
      else process.env.UPDATE_GOLDENS = previous
      fs.rmSync(dir, { recursive: true, force: true })
    }
  })
})
