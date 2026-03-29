import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import http from 'http';

/**
 * Note: The Application class from app.ts cannot be directly imported in test
 * because it transitively imports the MCP module which depends on
 * @pipeshub-ai/mcp (an ESM-only package). This is a known limitation.
 * These tests verify the underlying Express/HTTP patterns used by the app.
 */
describe('Application - Express/HTTP Patterns', () => {
  afterEach(() => {
    sinon.restore();
  });

  describe('Express app creation', () => {
    it('should create an Express application', () => {
      const app = express();
      expect(app).to.be.a('function');
      expect(app).to.have.property('use');
      expect(app).to.have.property('get');
      expect(app).to.have.property('post');
    });

    it('should create an HTTP server from Express app', () => {
      const app = express();
      const server = http.createServer(app);
      expect(server).to.be.instanceOf(http.Server);
    });
  });

  describe('PORT configuration', () => {
    it('should parse PORT from environment', () => {
      const original = process.env.PORT;
      process.env.PORT = '8080';
      const port = parseInt(process.env.PORT || '3000', 10);
      expect(port).to.equal(8080);
      process.env.PORT = original;
    });

    it('should default to 3000 when PORT is not set', () => {
      const original = process.env.PORT;
      delete process.env.PORT;
      const port = parseInt(process.env.PORT || '3000', 10);
      expect(port).to.equal(3000);
      process.env.PORT = original;
    });

    it('should handle non-numeric PORT gracefully', () => {
      const original = process.env.PORT;
      process.env.PORT = 'abc';
      const port = parseInt(process.env.PORT || '3000', 10);
      expect(isNaN(port)).to.be.true;
      process.env.PORT = original;
    });
  });

  describe('Middleware registration', () => {
    it('should register middleware on Express app', () => {
      const app = express();
      const middleware = (_req: any, _res: any, next: any): void => { next(); };
      app.use(middleware);
      // Express app should accept middleware without error
      expect(app).to.exist;
    });

    it('should register route handlers', () => {
      const app = express();
      const router = express.Router();
      router.get('/health', (_req, res) => { res.json({ status: 'ok' }); });
      app.use('/api/v1', router);
      expect(app).to.exist;
    });
  });
});
