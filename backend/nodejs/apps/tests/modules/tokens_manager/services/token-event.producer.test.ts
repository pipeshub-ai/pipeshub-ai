import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { TokenEventProducer } from '../../../../src/modules/tokens_manager/services/token-event.producer'

describe('tokens_manager/services/token-event.producer', () => {
  let producer: TokenEventProducer
  let mockLogger: any
  let mockProducer: any

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
      debug: sinon.stub(),
    }
    mockProducer = {
      connect: sinon.stub().resolves(),
      disconnect: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(false),
      publish: sinon.stub().resolves(),
      publishBatch: sinon.stub().resolves(),
      healthCheck: sinon.stub().resolves(true),
    }
    producer = new TokenEventProducer(mockProducer, mockLogger)
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('constructor', () => {
    it('should create instance with correct topic', () => {
      expect(producer).to.be.instanceOf(TokenEventProducer)
      expect((producer as any).topic).to.equal('token-events')
    })
  })

  describe('publishTokenEvent', () => {
    it('should call publish with correct topic and message format', async () => {
      const event: any = {
        tokenReferenceId: 'ref-123',
        serviceType: 'google',
      }

      await producer.publishTokenEvent(event)

      expect(mockProducer.publish.calledOnce).to.be.true
      const [topic, message] = mockProducer.publish.firstCall.args
      expect(topic).to.equal('token-events')
      expect(message.key).to.equal('ref-123-google')
      expect(message.value).to.deep.equal(event)
    })
  })

  describe('start', () => {
    it('should call connect if not connected', async () => {
      mockProducer.isConnected.returns(false)

      await producer.start()

      expect(mockProducer.connect.calledOnce).to.be.true
    })
  })

  describe('stop', () => {
    it('should call disconnect if connected', async () => {
      mockProducer.isConnected.returns(true)

      await producer.stop()

      expect(mockProducer.disconnect.calledOnce).to.be.true
    })

    it('should not call disconnect if not connected', async () => {
      mockProducer.isConnected.returns(false)

      await producer.stop()

      expect(mockProducer.disconnect.called).to.be.false
    })
  })
})
