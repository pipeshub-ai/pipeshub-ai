import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { TokenEventProducer } from '../../../../src/modules/tokens_manager/services/token-event.producer'

describe('tokens_manager/services/token-event.producer', () => {
  let producer: TokenEventProducer
  let mockLogger: any
  let mockMessageProducer: any

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
      debug: sinon.stub(),
    }
    mockMessageProducer = {
      connect: sinon.stub().resolves(),
      disconnect: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(false),
      healthCheck: sinon.stub().resolves(true),
      publish: sinon.stub().resolves(),
    }
    producer = new TokenEventProducer(mockMessageProducer, mockLogger)
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

      expect(mockMessageProducer.publish.calledOnce).to.be.true
      const [topic, message] = mockMessageProducer.publish.firstCall.args
      expect(topic).to.equal('token-events')
      expect(message.key).to.equal('ref-123-google')
      expect(message.value).to.deep.equal(event)
    })
  })

  describe('start', () => {
    it('should call connect if not connected', async () => {
      mockMessageProducer.isConnected.returns(false)

      await producer.start()

      expect(mockMessageProducer.connect.calledOnce).to.be.true
    })
  })

  describe('stop', () => {
    it('should call disconnect if connected', async () => {
      mockMessageProducer.isConnected.returns(true)

      await producer.stop()

      expect(mockMessageProducer.disconnect.calledOnce).to.be.true
    })

    it('should not call disconnect if not connected', async () => {
      mockMessageProducer.isConnected.returns(false)

      await producer.stop()

      expect(mockMessageProducer.disconnect.called).to.be.false
    })
  })
})
