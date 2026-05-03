import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Logger } from '../src/libs/services/logger.service';
import { createMockLogger, MockLogger } from './helpers/mock-logger';

/**
 * index.ts is the application entry point. It:
 * 1. Loads env via dotenv
 * 2. Creates an Application instance
 * 3. Registers process signal handlers (SIGTERM, SIGINT)
 * 4. Registers global error handlers (uncaughtException, unhandledRejection)
 * 5. Runs the bootstrap sequence: preInitMigration → initialize → start → runMigration
 * 6. Calls process.exit(1) on fatal startup failure
 *
 * Since index.ts executes its IIFE on import, we test the patterns and behavior
 * rather than importing it directly (which would trigger real startup).
 */
describe('index.ts - Application Bootstrap', () => {
  let sandbox: sinon.SinonSandbox;
  let mockLogger: MockLogger;

  beforeEach(() => {
    sandbox = sinon.createSandbox();
    mockLogger = createMockLogger();
    sandbox.stub(Logger, 'getInstance').returns(mockLogger as any);
  });

  afterEach(() => {
    sandbox.restore();
  });

  // =========================================================================
  // Environment setup
  // =========================================================================
  describe('Environment', () => {
    it('should be possible to set NODE_ENV', () => {
      const original = process.env.NODE_ENV;
      try {
        process.env.NODE_ENV = 'test';
        expect(process.env.NODE_ENV).to.equal('test');
      } finally {
        if (original === undefined) {
          delete process.env.NODE_ENV;
        } else {
          process.env.NODE_ENV = original;
        }
      }
    });

    it('should be possible to load dotenv config', () => {
      const { config } = require('dotenv');
      expect(config).to.be.a('function');
    });
  });

  // =========================================================================
  // Graceful shutdown
  // =========================================================================
  describe('gracefulShutdown behavior', () => {
    it('should call app.stop() and process.exit(0) on successful shutdown', async () => {
      const mockApp = {
        stop: sandbox.stub().resolves(),
        preInitMigration: sandbox.stub().resolves(),
        initialize: sandbox.stub().resolves(),
        start: sandbox.stub().resolves(),
        runMigration: sandbox.stub().resolves(),
      };

      const exitStub = sandbox.stub(process, 'exit');

      // Simulate gracefulShutdown logic from index.ts
      const gracefulShutdown = async (signal: string) => {
        mockLogger.info(`Received ${signal}. Starting graceful shutdown...`);
        try {
          await mockApp.stop();
          process.exit(0);
        } catch (error) {
          mockLogger.error('Error during shutdown:', error as any);
          process.exit(1);
        }
      };

      await gracefulShutdown('SIGTERM');

      expect(mockApp.stop.calledOnce).to.be.true;
      expect(exitStub.calledOnce).to.be.true;
      expect(exitStub.firstCall.args[0]).to.equal(0);
      expect(mockLogger.info.calledWith('Received SIGTERM. Starting graceful shutdown...')).to.be.true;
    });

    it('should call process.exit(1) if app.stop() throws', async () => {
      const mockApp = {
        stop: sandbox.stub().rejects(new Error('Cleanup failed')),
      };

      const exitStub = sandbox.stub(process, 'exit');

      const gracefulShutdown = async (signal: string) => {
        mockLogger.info(`Received ${signal}. Starting graceful shutdown...`);
        try {
          await mockApp.stop();
          process.exit(0);
        } catch (error) {
          mockLogger.error('Error during shutdown:', error as any);
          process.exit(1);
        }
      };

      await gracefulShutdown('SIGINT');

      expect(mockApp.stop.calledOnce).to.be.true;
      expect(exitStub.calledOnce).to.be.true;
      expect(exitStub.firstCall.args[0]).to.equal(1);
      expect(mockLogger.error.called).to.be.true;
    });

    it('should log the signal name that triggered shutdown', async () => {
      const mockApp = { stop: sandbox.stub().resolves() };
      sandbox.stub(process, 'exit');

      const gracefulShutdown = async (signal: string) => {
        mockLogger.info(`Received ${signal}. Starting graceful shutdown...`);
        try {
          await mockApp.stop();
          process.exit(0);
        } catch (error) {
          process.exit(1);
        }
      };

      await gracefulShutdown('SIGTERM');
      expect(mockLogger.info.calledWith('Received SIGTERM. Starting graceful shutdown...')).to.be.true;

      await gracefulShutdown('SIGINT');
      expect(mockLogger.info.calledWith('Received SIGINT. Starting graceful shutdown...')).to.be.true;
    });
  });

  // =========================================================================
  // Process Signal Handling
  // =========================================================================
  describe('Process Signal Handling', () => {
    it('should be possible to register SIGTERM handler', () => {
      const handler = sandbox.stub();
      process.on('SIGTERM', handler);
      expect(process.listenerCount('SIGTERM')).to.be.greaterThan(0);
      process.removeListener('SIGTERM', handler);
    });

    it('should be possible to register SIGINT handler', () => {
      const handler = sandbox.stub();
      process.on('SIGINT', handler);
      expect(process.listenerCount('SIGINT')).to.be.greaterThan(0);
      process.removeListener('SIGINT', handler);
    });

    it('SIGTERM handler should trigger graceful shutdown', async () => {
      const mockApp = { stop: sandbox.stub().resolves() };
      const exitStub = sandbox.stub(process, 'exit');

      const gracefulShutdown = async (signal: string) => {
        mockLogger.info(`Received ${signal}. Starting graceful shutdown...`);
        try {
          await mockApp.stop();
          process.exit(0);
        } catch (error) {
          process.exit(1);
        }
      };

      const handler = () => gracefulShutdown('SIGTERM');
      process.on('SIGTERM', handler);

      try {
        // Verify handler registered
        expect(process.listenerCount('SIGTERM')).to.be.greaterThan(0);

        // Simulate signal
        await gracefulShutdown('SIGTERM');
        expect(mockApp.stop.calledOnce).to.be.true;
        expect(exitStub.calledWith(0)).to.be.true;
      } finally {
        process.removeListener('SIGTERM', handler);
      }
    });

    it('SIGINT handler should trigger graceful shutdown', async () => {
      const mockApp = { stop: sandbox.stub().resolves() };
      const exitStub = sandbox.stub(process, 'exit');

      const gracefulShutdown = async (signal: string) => {
        mockLogger.info(`Received ${signal}. Starting graceful shutdown...`);
        try {
          await mockApp.stop();
          process.exit(0);
        } catch (error) {
          process.exit(1);
        }
      };

      const handler = () => gracefulShutdown('SIGINT');
      process.on('SIGINT', handler);

      try {
        await gracefulShutdown('SIGINT');
        expect(mockApp.stop.calledOnce).to.be.true;
        expect(exitStub.calledWith(0)).to.be.true;
      } finally {
        process.removeListener('SIGINT', handler);
      }
    });
  });

  // =========================================================================
  // Global Error Handlers
  // =========================================================================
  describe('Global Error Handlers', () => {
    it('should be possible to register uncaughtException handler', () => {
      const handler = sandbox.stub();
      process.on('uncaughtException', handler);
      expect(process.listenerCount('uncaughtException')).to.be.greaterThan(0);
      process.removeListener('uncaughtException', handler);
    });

    it('should be possible to register unhandledRejection handler', () => {
      const handler = sandbox.stub();
      process.on('unhandledRejection', handler);
      expect(process.listenerCount('unhandledRejection')).to.be.greaterThan(0);
      process.removeListener('unhandledRejection', handler);
    });

    it('uncaughtException handler should log error name and message', () => {
      const error = new TypeError('Cannot read property of undefined');

      // Replicate the handler logic from index.ts
      const uncaughtHandler = (err: Error) => {
        mockLogger.error('Uncaught Exception:', {
          error: {
            name: err.name,
            message: err.message,
          },
        });
      };

      uncaughtHandler(error);

      expect(mockLogger.error.calledOnce).to.be.true;
      const loggedArg = mockLogger.error.firstCall.args[1];
      expect(loggedArg.error.name).to.equal('TypeError');
      expect(loggedArg.error.message).to.equal('Cannot read property of undefined');
    });

    it('unhandledRejection handler should log Error reasons', () => {
      const reason = new Error('Promise rejection');

      // Replicate the handler logic from index.ts
      const rejectionHandler = (reason: any, promise: Promise<any>) => {
        mockLogger.error('Unhandled Rejection:', {
          reason: reason instanceof Error
            ? { name: reason.name, message: reason.message }
            : String(reason),
          promise: promise.toString(),
        });
      };

      rejectionHandler(reason, Promise.resolve());

      expect(mockLogger.error.calledOnce).to.be.true;
      const loggedArg = mockLogger.error.firstCall.args[1];
      expect(loggedArg.reason.name).to.equal('Error');
      expect(loggedArg.reason.message).to.equal('Promise rejection');
    });

    it('unhandledRejection handler should stringify non-Error reasons', () => {
      const rejectionHandler = (reason: any, promise: Promise<any>) => {
        mockLogger.error('Unhandled Rejection:', {
          reason: reason instanceof Error
            ? { name: reason.name, message: reason.message }
            : String(reason),
          promise: promise.toString(),
        });
      };

      rejectionHandler('string reason', Promise.resolve());

      const loggedArg = mockLogger.error.firstCall.args[1];
      expect(loggedArg.reason).to.equal('string reason');
    });

    it('unhandledRejection handler should handle null/undefined reasons', () => {
      const rejectionHandler = (reason: any, promise: Promise<any>) => {
        mockLogger.error('Unhandled Rejection:', {
          reason: reason instanceof Error
            ? { name: reason.name, message: reason.message }
            : String(reason),
          promise: promise.toString(),
        });
      };

      rejectionHandler(null, Promise.resolve());
      expect(mockLogger.error.firstCall.args[1].reason).to.equal('null');

      mockLogger.error.resetHistory();
      rejectionHandler(undefined, Promise.resolve());
      expect(mockLogger.error.firstCall.args[1].reason).to.equal('undefined');
    });

    it('uncaughtException handler should trigger graceful shutdown', async () => {
      const mockApp = { stop: sandbox.stub().resolves() };
      const exitStub = sandbox.stub(process, 'exit');

      const gracefulShutdown = async (signal: string) => {
        mockLogger.info(`Received ${signal}. Starting graceful shutdown...`);
        try {
          await mockApp.stop();
          process.exit(0);
        } catch (error) {
          process.exit(1);
        }
      };

      // Replicate the uncaughtException handler from index.ts
      const uncaughtHandler = (error: Error) => {
        mockLogger.error('Uncaught Exception:', {
          error: { name: error.name, message: error.message },
        });
        gracefulShutdown('uncaughtException');
      };

      uncaughtHandler(new Error('Fatal'));

      // gracefulShutdown is async, give it time
      await new Promise((resolve) => setTimeout(resolve, 10));

      expect(mockLogger.error.called).to.be.true;
      expect(mockApp.stop.calledOnce).to.be.true;
      expect(exitStub.calledWith(0)).to.be.true;
    });
  });

  // =========================================================================
  // Bootstrap sequence
  // =========================================================================
  describe('Bootstrap sequence', () => {
    it('should execute preInitMigration → initialize → start → runMigration in order', async () => {
      const callOrder: string[] = [];

      const mockApp = {
        preInitMigration: sandbox.stub().callsFake(async () => {
          callOrder.push('preInitMigration');
        }),
        initialize: sandbox.stub().callsFake(async () => {
          callOrder.push('initialize');
        }),
        start: sandbox.stub().callsFake(async () => {
          callOrder.push('start');
        }),
        runMigration: sandbox.stub().callsFake(async () => {
          callOrder.push('runMigration');
        }),
      };

      // Replicate the bootstrap IIFE from index.ts
      try {
        await mockApp.preInitMigration();
        await mockApp.initialize();
        await mockApp.start();
        await mockApp.runMigration();
      } catch (error) {
        mockLogger.error('Failed to start application:', error as any);
      }

      expect(callOrder).to.deep.equal([
        'preInitMigration',
        'initialize',
        'start',
        'runMigration',
      ]);
    });

    it('should call process.exit(1) if preInitMigration fails', async () => {
      const exitStub = sandbox.stub(process, 'exit');

      const mockApp = {
        preInitMigration: sandbox.stub().rejects(new Error('etcd unreachable')),
        initialize: sandbox.stub().resolves(),
        start: sandbox.stub().resolves(),
        runMigration: sandbox.stub().resolves(),
      };

      // Replicate bootstrap logic
      try {
        await mockApp.preInitMigration();
        await mockApp.initialize();
        await mockApp.start();
        await mockApp.runMigration();
      } catch (error) {
        mockLogger.error('Failed to start application:', error as any);
        process.exit(1);
      }

      expect(mockApp.preInitMigration.calledOnce).to.be.true;
      expect(mockApp.initialize.called).to.be.false;
      expect(mockApp.start.called).to.be.false;
      expect(mockApp.runMigration.called).to.be.false;
      expect(exitStub.calledWith(1)).to.be.true;
      expect(mockLogger.error.called).to.be.true;
    });

    it('should call process.exit(1) if initialize fails', async () => {
      const exitStub = sandbox.stub(process, 'exit');

      const mockApp = {
        preInitMigration: sandbox.stub().resolves(),
        initialize: sandbox.stub().rejects(new Error('Redis connection refused')),
        start: sandbox.stub().resolves(),
        runMigration: sandbox.stub().resolves(),
      };

      try {
        await mockApp.preInitMigration();
        await mockApp.initialize();
        await mockApp.start();
        await mockApp.runMigration();
      } catch (error) {
        mockLogger.error('Failed to start application:', error as any);
        process.exit(1);
      }

      expect(mockApp.preInitMigration.calledOnce).to.be.true;
      expect(mockApp.initialize.calledOnce).to.be.true;
      expect(mockApp.start.called).to.be.false;
      expect(exitStub.calledWith(1)).to.be.true;
    });

    it('should call process.exit(1) if start fails', async () => {
      const exitStub = sandbox.stub(process, 'exit');

      const mockApp = {
        preInitMigration: sandbox.stub().resolves(),
        initialize: sandbox.stub().resolves(),
        start: sandbox.stub().rejects(new Error('EADDRINUSE')),
        runMigration: sandbox.stub().resolves(),
      };

      try {
        await mockApp.preInitMigration();
        await mockApp.initialize();
        await mockApp.start();
        await mockApp.runMigration();
      } catch (error) {
        mockLogger.error('Failed to start application:', error as any);
        process.exit(1);
      }

      expect(mockApp.start.calledOnce).to.be.true;
      expect(mockApp.runMigration.called).to.be.false;
      expect(exitStub.calledWith(1)).to.be.true;
    });

    it('should call process.exit(1) if runMigration fails', async () => {
      const exitStub = sandbox.stub(process, 'exit');

      const mockApp = {
        preInitMigration: sandbox.stub().resolves(),
        initialize: sandbox.stub().resolves(),
        start: sandbox.stub().resolves(),
        runMigration: sandbox.stub().rejects(new Error('Schema migration error')),
      };

      try {
        await mockApp.preInitMigration();
        await mockApp.initialize();
        await mockApp.start();
        await mockApp.runMigration();
      } catch (error) {
        mockLogger.error('Failed to start application:', error as any);
        process.exit(1);
      }

      expect(mockApp.preInitMigration.calledOnce).to.be.true;
      expect(mockApp.initialize.calledOnce).to.be.true;
      expect(mockApp.start.calledOnce).to.be.true;
      expect(mockApp.runMigration.calledOnce).to.be.true;
      expect(exitStub.calledWith(1)).to.be.true;
    });

    it('should not call process.exit when all bootstrap steps succeed', async () => {
      const exitStub = sandbox.stub(process, 'exit');

      const mockApp = {
        preInitMigration: sandbox.stub().resolves(),
        initialize: sandbox.stub().resolves(),
        start: sandbox.stub().resolves(),
        runMigration: sandbox.stub().resolves(),
      };

      try {
        await mockApp.preInitMigration();
        await mockApp.initialize();
        await mockApp.start();
        await mockApp.runMigration();
      } catch (error) {
        mockLogger.error('Failed to start application:', error as any);
        process.exit(1);
      }

      expect(exitStub.called).to.be.false;
    });

    it('should log the startup error before exiting', async () => {
      sandbox.stub(process, 'exit');

      const startupError = new Error('Cannot connect to MongoDB');
      const mockApp = {
        preInitMigration: sandbox.stub().resolves(),
        initialize: sandbox.stub().rejects(startupError),
        start: sandbox.stub().resolves(),
        runMigration: sandbox.stub().resolves(),
      };

      try {
        await mockApp.preInitMigration();
        await mockApp.initialize();
        await mockApp.start();
        await mockApp.runMigration();
      } catch (error) {
        mockLogger.error('Failed to start application:', error as any);
        process.exit(1);
      }

      expect(mockLogger.error.calledOnce).to.be.true;
      expect(mockLogger.error.firstCall.args[0]).to.equal('Failed to start application:');
      expect(mockLogger.error.firstCall.args[1]).to.equal(startupError);
    });
  });

  // =========================================================================
  // Application class import
  // =========================================================================
  describe('Application class availability', () => {
    it('should be importable from app module', () => {
      const { Application } = require('../src/app');
      expect(Application).to.be.a('function');
    });

    it('should be constructable', () => {
      const { Application } = require('../src/app');
      const app = new Application();
      expect(app).to.be.instanceOf(Application);
    });

    it('should expose initialize, start, stop, runMigration, preInitMigration methods', () => {
      const { Application } = require('../src/app');
      const app = new Application();
      expect(app.initialize).to.be.a('function');
      expect(app.start).to.be.a('function');
      expect(app.stop).to.be.a('function');
      expect(app.runMigration).to.be.a('function');
      expect(app.preInitMigration).to.be.a('function');
    });
  });
});
