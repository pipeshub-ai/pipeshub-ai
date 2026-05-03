import { expect } from 'chai';
import { BaseError } from '../../../src/libs/errors/base.error';
import { KafkaError } from '../../../src/libs/errors/kafka.errors';

describe('Kafka Errors', () => {
  describe('KafkaError', () => {
    it('should have correct name', () => {
      const error = new KafkaError('Kafka connection failed');
      expect(error.name).to.equal('KafkaError');
    });

    it('should have correct code', () => {
      const error = new KafkaError('Kafka connection failed');
      expect(error.code).to.equal('KAFKA_ERROR');
    });

    it('should have correct statusCode of 503', () => {
      const error = new KafkaError('Kafka connection failed');
      expect(error.statusCode).to.equal(503);
    });

    it('should preserve error message', () => {
      const error = new KafkaError('Kafka connection failed');
      expect(error.message).to.equal('Kafka connection failed');
    });

    it('should be instanceof BaseError', () => {
      const error = new KafkaError('Kafka connection failed');
      expect(error).to.be.an.instanceOf(BaseError);
    });

    it('should be instanceof Error', () => {
      const error = new KafkaError('Kafka connection failed');
      expect(error).to.be.an.instanceOf(Error);
    });

    it('should accept metadata', () => {
      const metadata = { broker: 'localhost:9092', topic: 'events' };
      const error = new KafkaError('Kafka connection failed', metadata);
      expect(error.metadata).to.deep.equal(metadata);
    });

    it('should leave metadata undefined when not provided', () => {
      const error = new KafkaError('Kafka connection failed');
      expect(error.metadata).to.be.undefined;
    });

    it('should have a stack trace', () => {
      const error = new KafkaError('Kafka connection failed');
      expect(error.stack).to.be.a('string');
    });

    it('should serialize to JSON via toJSON()', () => {
      const error = new KafkaError('Kafka connection failed');
      const json = error.toJSON();
      expect(json).to.have.property('name', 'KafkaError');
      expect(json).to.have.property('code', 'KAFKA_ERROR');
      expect(json).to.have.property('statusCode', 503);
      expect(json).to.have.property('message', 'Kafka connection failed');
    });

    it('should include metadata in JSON output when provided', () => {
      const metadata = { broker: 'localhost:9092' };
      const error = new KafkaError('Kafka connection failed', metadata);
      const json = error.toJSON();
      expect(json.metadata).to.deep.equal(metadata);
    });
  });
});
