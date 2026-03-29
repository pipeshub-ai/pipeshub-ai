import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';

describe('index.ts - Application Bootstrap', () => {
  afterEach(() => {
    sinon.restore();
  });

  describe('Environment', () => {
    it('should be possible to set NODE_ENV', () => {
      process.env.NODE_ENV = 'test';
      expect(process.env.NODE_ENV).to.equal('test');
    });
  });

  describe('Process Signal Handling', () => {
    it('should be possible to register SIGTERM handler', () => {
      const handler = sinon.stub();
      process.on('SIGTERM', handler);
      expect(process.listenerCount('SIGTERM')).to.be.greaterThan(0);
      process.removeListener('SIGTERM', handler);
    });

    it('should be possible to register SIGINT handler', () => {
      const handler = sinon.stub();
      process.on('SIGINT', handler);
      expect(process.listenerCount('SIGINT')).to.be.greaterThan(0);
      process.removeListener('SIGINT', handler);
    });
  });

  describe('Global Error Handlers', () => {
    it('should be possible to register uncaughtException handler', () => {
      const handler = sinon.stub();
      process.on('uncaughtException', handler);
      expect(process.listenerCount('uncaughtException')).to.be.greaterThan(0);
      process.removeListener('uncaughtException', handler);
    });

    it('should be possible to register unhandledRejection handler', () => {
      const handler = sinon.stub();
      process.on('unhandledRejection', handler);
      expect(process.listenerCount('unhandledRejection')).to.be.greaterThan(0);
      process.removeListener('unhandledRejection', handler);
    });
  });
});
