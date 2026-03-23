import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { SOCKET_EVENT_TIMEOUT_MS } from '../../../../src/modules/knowledge_base/constants/upload.constants'

describe('knowledge_base/constants/upload.constants', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('SOCKET_EVENT_TIMEOUT_MS', () => {
    it('should be exported', () => {
      expect(SOCKET_EVENT_TIMEOUT_MS).to.exist
    })

    it('should be a number', () => {
      expect(SOCKET_EVENT_TIMEOUT_MS).to.be.a('number')
    })

    it('should equal 10000 (10 seconds)', () => {
      expect(SOCKET_EVENT_TIMEOUT_MS).to.equal(10000)
    })

    it('should be positive', () => {
      expect(SOCKET_EVENT_TIMEOUT_MS).to.be.greaterThan(0)
    })
  })
})
