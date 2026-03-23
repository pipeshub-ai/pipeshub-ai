import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { NotificationConsumer } from '../../../../src/modules/notification/service/notification.consumer'

describe('notification/service/notification.consumer', () => {
  let consumer: NotificationConsumer
  let mockLogger: any

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
      debug: sinon.stub(),
    }
    consumer = new NotificationConsumer(
      { brokers: ['localhost:9092'] },
      mockLogger,
    )
  })

  afterEach(() => {
    sinon.restore()
  })

  // Get the grandparent prototype (BaseKafkaConsumerConnection.prototype)
  // which has the connect/disconnect methods that super.connect()/super.disconnect() call
  function getBasePrototype() {
    return Object.getPrototypeOf(Object.getPrototypeOf(consumer))
  }

  describe('start', () => {
    it('should call connect if not connected', async () => {
      sinon.stub(consumer, 'isConnected').returns(false)
      const connectStub = sinon.stub(getBasePrototype(), 'connect').resolves()
      await consumer.start()
      expect(connectStub.calledOnce).to.be.true
    })

    it('should not connect if already connected', async () => {
      sinon.stub(consumer, 'isConnected').returns(true)
      const connectStub = sinon.stub(getBasePrototype(), 'connect').resolves()
      await consumer.start()
      expect(connectStub.called).to.be.false
    })
  })

  describe('stop', () => {
    it('should disconnect if connected', async () => {
      sinon.stub(consumer, 'isConnected').returns(true)
      const disconnectStub = sinon.stub(getBasePrototype(), 'disconnect').resolves()
      await consumer.stop()
      expect(disconnectStub.calledOnce).to.be.true
    })

    it('should not disconnect if not connected', async () => {
      sinon.stub(consumer, 'isConnected').returns(false)
      const disconnectStub = sinon.stub(getBasePrototype(), 'disconnect').resolves()
      await consumer.stop()
      expect(disconnectStub.called).to.be.false
    })
  })

  describe('subscribe', () => {
    it('should subscribe if connected', async () => {
      sinon.stub(consumer, 'isConnected').returns(true)
      const superSubscribe = sinon.stub(getBasePrototype(), 'subscribe').resolves()
      await consumer.subscribe(['test-topic'], false)
      expect(superSubscribe.calledOnce).to.be.true
    })

    it('should not subscribe if not connected', async () => {
      sinon.stub(consumer, 'isConnected').returns(false)
      const superSubscribe = sinon.stub(getBasePrototype(), 'subscribe').resolves()
      await consumer.subscribe(['test-topic'], false)
      expect(superSubscribe.called).to.be.false
    })

    it('should subscribe with fromBeginning flag', async () => {
      sinon.stub(consumer, 'isConnected').returns(true)
      const superSubscribe = sinon.stub(getBasePrototype(), 'subscribe').resolves()
      await consumer.subscribe(['test-topic'], true)
      expect(superSubscribe.calledWith(['test-topic'], true)).to.be.true
    })
  })

  describe('consume', () => {
    it('should not consume if not connected', async () => {
      sinon.stub(consumer, 'isConnected').returns(false)
      const superConsume = sinon.stub(getBasePrototype(), 'consume').resolves()
      const handler = sinon.stub().resolves()
      await consumer.consume(handler)
      expect(superConsume.called).to.be.false
    })

    it('should call super.consume with wrapped handler if connected', async () => {
      sinon.stub(consumer, 'isConnected').returns(true)
      const superConsume = sinon.stub(getBasePrototype(), 'consume').resolves()
      const handler = sinon.stub().resolves()
      await consumer.consume(handler)
      expect(superConsume.calledOnce).to.be.true
    })
  })
})
