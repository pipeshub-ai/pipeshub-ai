import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { KafkaError } from '../../../src/libs/errors/kafka.errors'
import { createMockLogger, MockLogger } from '../../helpers/mock-logger'
import { KafkaConfig, KafkaMessage } from '../../../src/libs/types/kafka.types'
import {
  BaseKafkaProducerConnection,
  BaseKafkaConsumerConnection,
} from '../../../src/libs/services/kafka.service'

class TestKafkaProducer extends BaseKafkaProducerConnection {
  constructor(config: KafkaConfig, logger: any) {
    super(config, logger)
  }
}

class TestKafkaConsumer extends BaseKafkaConsumerConnection {
  constructor(config: KafkaConfig, logger: any) {
    super(config, logger)
  }
}

describe('Kafka Service - branch coverage', () => {
  const defaultConfig: KafkaConfig = {
    clientId: 'test-client',
    brokers: ['localhost:9092'],
    groupId: 'test-group',
  }

  let mockLogger: MockLogger

  beforeEach(() => {
    mockLogger = createMockLogger()
  })

  afterEach(() => {
    sinon.restore()
  })

  // =========================================================================
  // BaseKafkaProducerConnection
  // =========================================================================
  describe('Producer - connect', () => {
    it('should skip connect if already initialized', async () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger)
      const mockProd = { connect: sinon.stub().resolves(), disconnect: sinon.stub().resolves(), send: sinon.stub().resolves() }
      ;(producer as any).producer = mockProd
      ;(producer as any).isInitialized = true

      await producer.connect()
      // connect should NOT be called again
      expect(mockProd.connect.called).to.be.false
    })

    it('should set isInitialized false on connect failure', async () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger)
      const mockProd = { connect: sinon.stub().rejects(new Error('Connection refused')), disconnect: sinon.stub(), send: sinon.stub() }
      ;(producer as any).producer = mockProd

      try {
        await producer.connect()
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(KafkaError)
        expect(producer.isConnected()).to.be.false
      }
    })

    it('should handle non-Error in connect failure', async () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger)
      const mockProd = { connect: sinon.stub().rejects('string-error'), disconnect: sinon.stub(), send: sinon.stub() }
      ;(producer as any).producer = mockProd

      try {
        await producer.connect()
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(KafkaError)
      }
    })
  })

  describe('Producer - disconnect', () => {
    it('should skip disconnect if not initialized', async () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger)
      const mockProd = { connect: sinon.stub(), disconnect: sinon.stub().resolves(), send: sinon.stub() }
      ;(producer as any).producer = mockProd
      ;(producer as any).isInitialized = false

      await producer.disconnect()
      expect(mockProd.disconnect.called).to.be.false
    })

    it('should handle error during disconnect', async () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger)
      const mockProd = { connect: sinon.stub(), disconnect: sinon.stub().rejects(new Error('disconnect error')), send: sinon.stub() }
      ;(producer as any).producer = mockProd
      ;(producer as any).isInitialized = true

      await producer.disconnect()
      // Should not throw - error is caught and logged
      expect(mockLogger.error.called).to.be.true
    })

    it('should handle non-Error in disconnect failure', async () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger)
      const mockProd = { connect: sinon.stub(), disconnect: sinon.stub().rejects('string-error'), send: sinon.stub() }
      ;(producer as any).producer = mockProd
      ;(producer as any).isInitialized = true

      await producer.disconnect()
      expect(mockLogger.error.called).to.be.true
    })
  })

  describe('Producer - publish and publishBatch', () => {
    it('should connect and send message via publish', async () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger)
      const mockProd = { connect: sinon.stub().resolves(), disconnect: sinon.stub(), send: sinon.stub().resolves() }
      ;(producer as any).producer = mockProd
      ;(producer as any).isInitialized = false

      await producer.publish('test-topic', { key: 'k1', value: { data: 'test' } })
      // ensureConnection should have called connect
      expect(mockProd.connect.called).to.be.true
      expect(mockProd.send.called).to.be.true
    })

    it('should send batch messages via publishBatch', async () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger)
      const mockProd = { connect: sinon.stub().resolves(), disconnect: sinon.stub(), send: sinon.stub().resolves() }
      ;(producer as any).producer = mockProd
      ;(producer as any).isInitialized = true

      await producer.publishBatch('test-topic', [
        { key: 'k1', value: { data: 'test1' } },
        { key: 'k2', value: { data: 'test2' } },
      ])
      expect(mockProd.send.called).to.be.true
    })

    it('should throw KafkaError on send failure', async () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger)
      const mockProd = { connect: sinon.stub().resolves(), disconnect: sinon.stub(), send: sinon.stub().rejects(new Error('send failed')) }
      ;(producer as any).producer = mockProd
      ;(producer as any).isInitialized = true

      try {
        await producer.publish('test-topic', { key: 'k1', value: { data: 'test' } })
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(KafkaError)
      }
    })
  })

  describe('Producer - healthCheck', () => {
    it('should return true on successful health check', async () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger)
      const mockProd = { connect: sinon.stub().resolves(), disconnect: sinon.stub(), send: sinon.stub().resolves() }
      ;(producer as any).producer = mockProd
      ;(producer as any).isInitialized = true

      const result = await producer.healthCheck()
      expect(result).to.be.true
    })

    it('should return false on health check failure', async () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger)
      const mockProd = { connect: sinon.stub().rejects(new Error('not connected')), disconnect: sinon.stub(), send: sinon.stub() }
      ;(producer as any).producer = mockProd
      ;(producer as any).isInitialized = false

      const result = await producer.healthCheck()
      expect(result).to.be.false
    })
  })

  describe('Producer - formatMessage', () => {
    it('should format message with headers', () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger)
      const msg: KafkaMessage<any> = {
        key: 'k1',
        value: { data: 'test' },
        headers: { 'x-custom': 'value' },
      }

      const formatted = (producer as any).formatMessage(msg)
      expect(formatted.key).to.equal('k1')
      expect(formatted.value).to.equal(JSON.stringify({ data: 'test' }))
      expect(formatted.headers).to.deep.equal({ 'x-custom': 'value' })
    })

    it('should format message without headers', () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger)
      const msg: KafkaMessage<any> = {
        key: 'k1',
        value: { data: 'test' },
      }

      const formatted = (producer as any).formatMessage(msg)
      expect(formatted.headers).to.be.undefined
    })
  })

  // =========================================================================
  // BaseKafkaConsumerConnection
  // =========================================================================
  describe('Consumer - constructor', () => {
    it('should use default groupId when not provided', () => {
      const config = { clientId: 'test', brokers: ['localhost:9092'] }
      const consumer = new TestKafkaConsumer(config as KafkaConfig, mockLogger)
      expect(consumer).to.exist
    })

    it('should use provided groupId', () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      expect(consumer).to.exist
    })

    it('should use default retry values when not provided', () => {
      const config = { clientId: 'test', brokers: ['localhost:9092'], groupId: 'g1' }
      const consumer = new TestKafkaConsumer(config as KafkaConfig, mockLogger)
      expect(consumer).to.exist
    })

    it('should use provided retry values', () => {
      const config = {
        clientId: 'test', brokers: ['localhost:9092'], groupId: 'g1',
        initialRetryTime: 200, maxRetryTime: 60000, maxRetries: 5,
      }
      const consumer = new TestKafkaConsumer(config as KafkaConfig, mockLogger)
      expect(consumer).to.exist
    })
  })

  describe('Consumer - connect', () => {
    it('should connect when not initialized', async () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      const mockCons = { connect: sinon.stub().resolves(), disconnect: sinon.stub(), subscribe: sinon.stub(), run: sinon.stub(), pause: sinon.stub(), resume: sinon.stub() }
      ;(consumer as any).consumer = mockCons
      ;(consumer as any).isInitialized = false

      await consumer.connect()
      expect(consumer.isConnected()).to.be.true
    })

    it('should skip connect if already initialized', async () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      const mockCons = { connect: sinon.stub().resolves(), disconnect: sinon.stub(), subscribe: sinon.stub(), run: sinon.stub(), pause: sinon.stub(), resume: sinon.stub() }
      ;(consumer as any).consumer = mockCons
      ;(consumer as any).isInitialized = true

      await consumer.connect()
      expect(mockCons.connect.called).to.be.false
    })

    it('should throw KafkaError on connect failure', async () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      const mockCons = { connect: sinon.stub().rejects(new Error('connect failed')), disconnect: sinon.stub(), subscribe: sinon.stub(), run: sinon.stub(), pause: sinon.stub(), resume: sinon.stub() }
      ;(consumer as any).consumer = mockCons

      try {
        await consumer.connect()
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(KafkaError)
        expect(consumer.isConnected()).to.be.false
      }
    })
  })

  describe('Consumer - disconnect', () => {
    it('should skip disconnect if not initialized', async () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      const mockCons = { connect: sinon.stub(), disconnect: sinon.stub().resolves(), subscribe: sinon.stub(), run: sinon.stub(), pause: sinon.stub(), resume: sinon.stub() }
      ;(consumer as any).consumer = mockCons
      ;(consumer as any).isInitialized = false

      await consumer.disconnect()
      expect(mockCons.disconnect.called).to.be.false
    })

    it('should handle error during disconnect', async () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      const mockCons = { connect: sinon.stub(), disconnect: sinon.stub().rejects(new Error('err')), subscribe: sinon.stub(), run: sinon.stub(), pause: sinon.stub(), resume: sinon.stub() }
      ;(consumer as any).consumer = mockCons
      ;(consumer as any).isInitialized = true

      await consumer.disconnect()
      expect(mockLogger.error.called).to.be.true
    })
  })

  describe('Consumer - subscribe', () => {
    it('should subscribe to multiple topics', async () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      const mockCons = { connect: sinon.stub().resolves(), disconnect: sinon.stub(), subscribe: sinon.stub().resolves(), run: sinon.stub(), pause: sinon.stub(), resume: sinon.stub() }
      ;(consumer as any).consumer = mockCons
      ;(consumer as any).isInitialized = true

      await consumer.subscribe(['topic1', 'topic2'])
      expect(mockCons.subscribe.calledTwice).to.be.true
    })

    it('should use fromBeginning parameter', async () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      const mockCons = { connect: sinon.stub().resolves(), disconnect: sinon.stub(), subscribe: sinon.stub().resolves(), run: sinon.stub(), pause: sinon.stub(), resume: sinon.stub() }
      ;(consumer as any).consumer = mockCons
      ;(consumer as any).isInitialized = true

      await consumer.subscribe(['topic1'], true)
      expect(mockCons.subscribe.calledWith({ topic: 'topic1', fromBeginning: true })).to.be.true
    })

    it('should throw KafkaError on subscribe failure', async () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      const mockCons = { connect: sinon.stub().resolves(), disconnect: sinon.stub(), subscribe: sinon.stub().rejects(new Error('sub failed')), run: sinon.stub(), pause: sinon.stub(), resume: sinon.stub() }
      ;(consumer as any).consumer = mockCons
      ;(consumer as any).isInitialized = true

      try {
        await consumer.subscribe(['topic1'])
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(KafkaError)
      }
    })
  })

  describe('Consumer - consume', () => {
    it('should process valid messages', async () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      const mockCons = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub(),
        subscribe: sinon.stub().resolves(),
        run: sinon.stub().callsFake(async (opts: any) => {
          // Simulate receiving a message
          await opts.eachMessage({
            topic: 'test',
            partition: 0,
            message: {
              key: Buffer.from('k1'),
              value: Buffer.from(JSON.stringify({ data: 'test' })),
            },
          })
        }),
        pause: sinon.stub(),
        resume: sinon.stub(),
      }
      ;(consumer as any).consumer = mockCons
      ;(consumer as any).isInitialized = true

      const handler = sinon.stub().resolves()
      await consumer.consume(handler)

      expect(handler.calledOnce).to.be.true
      expect(handler.firstCall.args[0].key).to.equal('k1')
    })

    it('should handle null message value (BadRequestError)', async () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      const mockCons = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub(),
        subscribe: sinon.stub(),
        run: sinon.stub().callsFake(async (opts: any) => {
          await opts.eachMessage({
            topic: 'test',
            partition: 0,
            message: { key: null, value: null },
          })
        }),
        pause: sinon.stub(),
        resume: sinon.stub(),
      }
      ;(consumer as any).consumer = mockCons
      ;(consumer as any).isInitialized = true

      const handler = sinon.stub().resolves()
      await consumer.consume(handler)

      // Handler should NOT be called - error is caught internally
      expect(handler.called).to.be.false
      expect(mockLogger.error.called).to.be.true
    })

    it('should handle null message key gracefully', async () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      const mockCons = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub(),
        subscribe: sinon.stub(),
        run: sinon.stub().callsFake(async (opts: any) => {
          await opts.eachMessage({
            topic: 'test',
            partition: 0,
            message: {
              key: null,
              value: Buffer.from(JSON.stringify({ data: 'test' })),
            },
          })
        }),
        pause: sinon.stub(),
        resume: sinon.stub(),
      }
      ;(consumer as any).consumer = mockCons
      ;(consumer as any).isInitialized = true

      const handler = sinon.stub().resolves()
      await consumer.consume(handler)

      expect(handler.calledOnce).to.be.true
      expect(handler.firstCall.args[0].key).to.equal('')
    })

    it('should handle handler errors gracefully', async () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      const mockCons = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub(),
        subscribe: sinon.stub(),
        run: sinon.stub().callsFake(async (opts: any) => {
          await opts.eachMessage({
            topic: 'test',
            partition: 0,
            message: {
              key: Buffer.from('k1'),
              value: Buffer.from(JSON.stringify({ data: 'test' })),
            },
          })
        }),
        pause: sinon.stub(),
        resume: sinon.stub(),
      }
      ;(consumer as any).consumer = mockCons
      ;(consumer as any).isInitialized = true

      const handler = sinon.stub().rejects(new Error('handler failed'))
      await consumer.consume(handler)

      // Error should be caught and logged
      expect(mockLogger.error.called).to.be.true
    })

    it('should throw KafkaError when consumer.run fails', async () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      const mockCons = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub(),
        subscribe: sinon.stub(),
        run: sinon.stub().rejects(new Error('run failed')),
        pause: sinon.stub(),
        resume: sinon.stub(),
      }
      ;(consumer as any).consumer = mockCons
      ;(consumer as any).isInitialized = true

      try {
        await consumer.consume(sinon.stub())
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(KafkaError)
      }
    })
  })

  describe('Consumer - pause and resume', () => {
    it('should pause topics', () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      const mockCons = { connect: sinon.stub(), disconnect: sinon.stub(), subscribe: sinon.stub(), run: sinon.stub(), pause: sinon.stub(), resume: sinon.stub() }
      ;(consumer as any).consumer = mockCons

      consumer.pause(['topic1', 'topic2'])
      expect(mockCons.pause.calledTwice).to.be.true
    })

    it('should resume topics', () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      const mockCons = { connect: sinon.stub(), disconnect: sinon.stub(), subscribe: sinon.stub(), run: sinon.stub(), pause: sinon.stub(), resume: sinon.stub() }
      ;(consumer as any).consumer = mockCons

      consumer.resume(['topic1', 'topic2'])
      expect(mockCons.resume.calledTwice).to.be.true
    })
  })

  describe('Consumer - healthCheck', () => {
    it('should return true when connected', async () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      ;(consumer as any).isInitialized = true

      const result = await consumer.healthCheck()
      expect(result).to.be.true
    })

    it('should return false when connection fails', async () => {
      const consumer = new TestKafkaConsumer(defaultConfig, mockLogger)
      const mockCons = { connect: sinon.stub().rejects(new Error('fail')), disconnect: sinon.stub(), subscribe: sinon.stub(), run: sinon.stub(), pause: sinon.stub(), resume: sinon.stub() }
      ;(consumer as any).consumer = mockCons
      ;(consumer as any).isInitialized = false

      const result = await consumer.healthCheck()
      expect(result).to.be.false
    })
  })
})
