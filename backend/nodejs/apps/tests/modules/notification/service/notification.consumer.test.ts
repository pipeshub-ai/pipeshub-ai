import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { NotificationConsumer } from '../../../../src/modules/notification/service/notification.consumer'

describe('notification/service/notification.consumer', () => {
  let consumer: NotificationConsumer
  let mockLogger: any
  let mockConsumer: any

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
      debug: sinon.stub(),
    }
    mockConsumer = {
      connect: sinon.stub().resolves(),
      disconnect: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(false),
      subscribe: sinon.stub().resolves(),
      consume: sinon.stub().resolves(),
      pause: sinon.stub(),
      resume: sinon.stub(),
      healthCheck: sinon.stub().resolves(true),
    }
    consumer = new NotificationConsumer(
      mockConsumer,
      mockLogger,
    )
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('start', () => {
    it('should call connect if not connected', async () => {
      mockConsumer.isConnected.returns(false)
      await consumer.start()
      expect(mockConsumer.connect.calledOnce).to.be.true
    })

    it('should not connect if already connected', async () => {
      mockConsumer.isConnected.returns(true)
      await consumer.start()
      expect(mockConsumer.connect.called).to.be.false
    })
  })

  describe('stop', () => {
    it('should disconnect if connected', async () => {
      mockConsumer.isConnected.returns(true)
      await consumer.stop()
      expect(mockConsumer.disconnect.calledOnce).to.be.true
    })

    it('should not disconnect if not connected', async () => {
      mockConsumer.isConnected.returns(false)
      await consumer.stop()
      expect(mockConsumer.disconnect.called).to.be.false
    })
  })

  describe('subscribe', () => {
    it('should subscribe if connected', async () => {
      mockConsumer.isConnected.returns(true)
      await consumer.subscribe(['test-topic'], false)
      expect(mockConsumer.subscribe.calledOnce).to.be.true
    })

    it('should not subscribe if not connected', async () => {
      mockConsumer.isConnected.returns(false)
      await consumer.subscribe(['test-topic'], false)
      expect(mockConsumer.subscribe.called).to.be.false
    })

    it('should subscribe with fromBeginning flag', async () => {
      mockConsumer.isConnected.returns(true)
      await consumer.subscribe(['test-topic'], true)
      expect(mockConsumer.subscribe.calledWith(['test-topic'], true)).to.be.true
    })
  })

  describe('consume', () => {
    it('should not consume if not connected', async () => {
      mockConsumer.isConnected.returns(false)
      const handler = sinon.stub().resolves()
      await consumer.consume(handler)
      expect(mockConsumer.consume.called).to.be.false
    })

    it('should call consumer.consume with wrapped handler if connected', async () => {
      mockConsumer.isConnected.returns(true)
      const handler = sinon.stub().resolves()
      await consumer.consume(handler)
      expect(mockConsumer.consume.calledOnce).to.be.true
    })
  })
})
