import { expect } from 'chai'
import {
  AGUI_PROTOCOL,
  LEGACY_PROTOCOL,
  AGUIEventType,
  resolveProtocol,
  isAGUI,
  frameAGUI,
  aguiErrorCodeFromPayload,
  aguiRunErrorMetadata,
  readRunFrame,
} from '../../../../src/modules/enterprise_search/utils/agui'

describe('AG-UI protocol utils', () => {
  describe('resolveProtocol', () => {
    it('should resolve to agui when body.protocol is "agui"', () => {
      expect(resolveProtocol({ protocol: 'agui' }, undefined)).to.equal(AGUI_PROTOCOL)
    })

    it('should resolve to agui when query.protocol is "agui"', () => {
      expect(resolveProtocol(undefined, { protocol: 'agui' })).to.equal(AGUI_PROTOCOL)
    })

    it('should prefer body.protocol over query.protocol', () => {
      expect(resolveProtocol({ protocol: 'agui' }, { protocol: 'legacy' })).to.equal(AGUI_PROTOCOL)
    })

    it('should resolve to agui when neither body nor query specify protocol', () => {
      expect(resolveProtocol({}, {})).to.equal(AGUI_PROTOCOL)
    })

    it('should resolve to agui when body and query are undefined', () => {
      expect(resolveProtocol(undefined, undefined)).to.equal(AGUI_PROTOCOL)
    })

    it('should resolve to agui even when an unrecognized protocol value is sent', () => {
      expect(resolveProtocol({ protocol: 'ws' }, undefined)).to.equal(AGUI_PROTOCOL)
    })

    it('should resolve to agui even when a legacy protocol value is explicitly sent', () => {
      expect(resolveProtocol({ protocol: 'legacy' }, { protocol: 'agui' })).to.equal(AGUI_PROTOCOL)
    })
  })

  describe('isAGUI', () => {
    it('should return true for the agui protocol', () => {
      expect(isAGUI(AGUI_PROTOCOL)).to.be.true
    })

    it('should return false for the legacy protocol', () => {
      expect(isAGUI(LEGACY_PROTOCOL)).to.be.false
    })

    it('should return false for undefined', () => {
      expect(isAGUI(undefined)).to.be.false
    })
  })

  describe('frameAGUI', () => {
    it('should hybrid-frame with an event: line carrying the AG-UI type name', () => {
      const frame = frameAGUI(AGUIEventType.RUN_STARTED, { runId: 'run-1' })

      expect(frame).to.include('event: RUN_STARTED\n')
      expect(frame.endsWith('\n\n')).to.be.true
    })

    it('should embed "type" plus every extra field inside the JSON data payload', () => {
      const frame = frameAGUI(AGUIEventType.CUSTOM, { name: 'artifact', value: { id: 1 } })

      const dataLine = frame.split('\n').find((line) => line.startsWith('data:'))
      const parsed = JSON.parse(dataLine!.replace(/^data: ?/, ''))
      expect(parsed).to.deep.equal({ type: 'CUSTOM', name: 'artifact', value: { id: 1 } })
    })

    it('should produce a frame with no extra fields when none are given', () => {
      const frame = frameAGUI(AGUIEventType.RUN_FINISHED)

      const dataLine = frame.split('\n').find((line) => line.startsWith('data:'))
      expect(JSON.parse(dataLine!.replace(/^data: ?/, ''))).to.deep.equal({ type: 'RUN_FINISHED' })
    })
  })

  describe('AGUIEventType', () => {
    it('should expose the wire-visible AG-UI type names this proxy layer intercepts', () => {
      expect(AGUIEventType.RUN_STARTED).to.equal('RUN_STARTED')
      expect(AGUIEventType.RUN_FINISHED).to.equal('RUN_FINISHED')
      expect(AGUIEventType.RUN_ERROR).to.equal('RUN_ERROR')
      expect(AGUIEventType.STATE_SNAPSHOT).to.equal('STATE_SNAPSHOT')
      expect(AGUIEventType.CUSTOM).to.equal('CUSTOM')
    })
  })

  describe('aguiErrorCodeFromPayload', () => {
    it('should use the RUN_ERROR code from the payload', () => {
      expect(aguiErrorCodeFromPayload({ code: 'accessible_records_not_found' })).to.equal(
        'accessible_records_not_found',
      )
    })

    it('should fall back to streaming_error when code is missing', () => {
      expect(aguiErrorCodeFromPayload({ message: 'boom' })).to.equal('streaming_error')
    })
  })

  describe('aguiRunErrorMetadata', () => {
    it('should store AG-UI type and code', () => {
      const meta = aguiRunErrorMetadata('no_response')
      expect(meta.get('type')).to.equal('RUN_ERROR')
      expect(meta.get('code')).to.equal('no_response')
    })
  })
})

describe('readRunFrame', () => {
  const finished = (data: unknown) => readRunFrame(AGUIEventType.RUN_FINISHED, JSON.stringify(data))
  const failed = (data: unknown) => readRunFrame(AGUIEventType.RUN_ERROR, JSON.stringify(data))

  it("takes the root run's RUN_FINISHED result as the answer", () => {
    const result = { answer: 'Done.', citations: [] }
    expect(finished({ type: 'RUN_FINISHED', runId: 'root', result })).to.deep.equal({ kind: 'answer', result })
  })

  it('passes a RUN_FINISHED whose result is missing or not an object', () => {
    for (const result of [undefined, null, [], 'x', 0]) {
      expect(finished({ runId: 'root', result }), JSON.stringify(result)).to.deep.equal({ kind: 'pass' })
    }
  })

  it("passes a sub-agent's RUN_FINISHED, even one that carries a result", () => {
    expect(finished({ runId: 'child', parentRunId: 'root' })).to.deep.equal({ kind: 'pass' })
    expect(finished({ runId: 'child', parentRunId: 'root', result: { answer: 'x' } })).to.deep.equal({ kind: 'pass' })
  })

  it("passes a sub-agent's RUN_ERROR", () => {
    expect(failed({ runId: 'child', parentRunId: 'root', message: 'boom', code: 'agent_error' })).to.deep.equal({ kind: 'pass' })
  })

  it("returns the root run's RUN_ERROR with its data", () => {
    const data = { type: 'RUN_ERROR', message: 'Rate limited', code: 'rate_limited' }
    expect(failed(data)).to.deep.equal({ kind: 'error', data })
  })

  it('reports a RUN_ERROR whose payload is not an object as a bad frame', () => {
    for (const data of [null, [], 'x', 3]) {
      expect(failed(data), JSON.stringify(data)).to.deep.equal({ kind: 'bad' })
    }
  })

  it('reports a RUN_FINISHED whose payload is not an object as a bad frame', () => {
    for (const data of [null, [], 'x', 3]) {
      expect(finished(data), JSON.stringify(data)).to.deep.equal({ kind: 'bad' })
    }
  })

  it('reports data that does not parse as a bad frame, for both types', () => {
    expect(readRunFrame(AGUIEventType.RUN_FINISHED, '{invalid')).to.deep.equal({ kind: 'bad' })
    expect(readRunFrame(AGUIEventType.RUN_ERROR, '{invalid')).to.deep.equal({ kind: 'bad' })
  })

  it('reports empty data as a bad frame, for both types', () => {
    expect(readRunFrame(AGUIEventType.RUN_FINISHED, '')).to.deep.equal({ kind: 'bad' })
    expect(readRunFrame(AGUIEventType.RUN_ERROR, '')).to.deep.equal({ kind: 'bad' })
  })

  it('takes a root result as the answer only when its citations are omitted or a list', () => {
    for (const result of [{ answer: 'Done.' }, { answer: 'Done.', citations: [] }]) {
      expect(finished({ runId: 'root', result }), JSON.stringify(result)).to.deep.equal({ kind: 'answer', result })
    }
    for (const result of [{ answer: 'Done.', citations: {} }, { answer: 'Done.', citations: null }, { answer: 'Done.', citations: 'x' }]) {
      expect(finished({ runId: 'root', result }), JSON.stringify(result)).to.deep.equal({ kind: 'bad' })
    }
  })

  it('takes a root result as the answer only when its answer is a string, empty included', () => {
    const waiting = { answer: '', status: 'waiting_input', citations: [] }
    expect(finished({ runId: 'root', result: waiting })).to.deep.equal({ kind: 'answer', result: waiting })
    for (const result of [{ citations: [] }, { answer: null }, { answer: 5 }]) {
      expect(finished({ runId: 'root', result }), JSON.stringify(result)).to.deep.equal({ kind: 'bad' })
    }
  })

  it('treats an empty parentRunId as the root run, as the frontend does', () => {
    expect(failed({ parentRunId: '', message: 'boom' })).to.deep.equal({ kind: 'error', data: { parentRunId: '', message: 'boom' } })
    expect(finished({ parentRunId: '', result: { answer: 'a' } })).to.deep.equal({ kind: 'answer', result: { answer: 'a' } })
  })
})
