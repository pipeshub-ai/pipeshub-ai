import { expect } from 'chai'
import {
  AGUI_PROTOCOL,
  LEGACY_PROTOCOL,
  AGUIEventType,
  resolveProtocol,
  isAGUI,
  frameAGUI,
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

    it('should default to legacy when neither body nor query specify protocol', () => {
      expect(resolveProtocol({}, {})).to.equal(LEGACY_PROTOCOL)
    })

    it('should default to legacy when body and query are undefined', () => {
      expect(resolveProtocol(undefined, undefined)).to.equal(LEGACY_PROTOCOL)
    })

    it('should collapse an unrecognized protocol value to legacy', () => {
      expect(resolveProtocol({ protocol: 'ws' }, undefined)).to.equal(LEGACY_PROTOCOL)
    })

    it('should collapse a falsy-but-present protocol value to legacy', () => {
      expect(resolveProtocol({ protocol: '' }, { protocol: 'agui' })).to.equal(LEGACY_PROTOCOL)
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
})
