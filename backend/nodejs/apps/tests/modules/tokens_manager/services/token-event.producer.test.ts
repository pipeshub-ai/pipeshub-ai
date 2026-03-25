import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { TokenEventProducer } from '../../../../src/modules/tokens_manager/services/token-event.producer'

describe('tokens_manager/services/token-event.producer', () => {
  let producer: TokenEventProducer
  let mockLogger: any
  let mockConfig: any

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
      debug: sinon.stub(),
    }
    mockConfig = {
      brokers: ['localhost:9092'],
    }
    producer = new TokenEventProducer(mockConfig, mockLogger)
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
      const publishStub = sinon.stub(producer, 'publish' as any).resolves()

      const event: any = {
        tokenReferenceId: 'ref-123',
        serviceType: 'google',
      }

      await producer.publishTokenEvent(event)

      expect(publishStub.calledOnce).to.be.true
      const [topic, message] = publishStub.firstCall.args
      expect(topic).to.equal('token-events')
      expect(message.key).to.equal('ref-123-google')
      expect(message.value).to.deep.equal(event)
    })
  })

  describe('start', () => {
    it('should call connect if not connected', async () => {
      sinon.stub(producer, 'isConnected' as any).value(false)
      const connectStub = sinon.stub(producer, 'connect' as any).resolves()

      await producer.start()

      expect(connectStub.calledOnce).to.be.true
    })
  })

  describe('stop', () => {
    it('should call disconnect if connected', async () => {
      sinon.stub(producer, 'isConnected').returns(true)
      const disconnectStub = sinon.stub(producer, 'disconnect' as any).resolves()

      await producer.stop()

      expect(disconnectStub.calledOnce).to.be.true
    })

    it('should not call disconnect if not connected', async () => {
      sinon.stub(producer, 'isConnected').returns(false)
      const disconnectStub = sinon.stub(producer, 'disconnect' as any).resolves()

      await producer.stop()

      expect(disconnectStub.called).to.be.false
    })
  })
})
