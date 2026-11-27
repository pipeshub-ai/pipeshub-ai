import { AsyncLocalStorage } from 'async_hooks';
import { expect } from 'chai';
import type { NextFunction, Request, Response } from 'express';
import {
  hasInboundContextReaders,
  registerInboundContextReader,
  resetInboundContextReaders,
  runWithInboundContext,
  type InboundCarrierKind,
} from '../../../src/libs/context/inbound-readers';
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context';
import { getRootId } from '../../../src/libs/context/request-context';
import { consumeAnnounced } from '../../../src/libs/services/broker-events';
import { onPlatformEvent } from '../../../src/libs/platform-events';

describe('inbound context readers', () => {
  afterEach(() => resetInboundContextReaders());

  describe('with nothing registered (the default)', () => {
    it('reports no reader and runs the handler as it is', () => {
      expect(hasInboundContextReaders()).to.equal(false);
      expect(runWithInboundContext({ a: 1 }, 'message', () => 'ran')).to.equal(
        'ran',
      );
    });

    it('lets what the handler throws through', () => {
      expect(() =>
        runWithInboundContext({}, 'http', () => {
          throw new Error('handler');
        }),
      ).to.throw('handler');
    });
  });

  describe('with a reader registered', () => {
    const restored = new AsyncLocalStorage<string>();
    const seen: Array<[unknown, InboundCarrierKind]> = [];
    const register = (): (() => void) =>
      registerInboundContextReader({
        read(carrier, kind) {
          seen.push([carrier, kind]);
          const mark = carrier['x-mark'];
          if (typeof mark !== 'string') return undefined;
          return (fn) => restored.run(mark, fn);
        },
      });
    beforeEach(() => {
      seen.length = 0;
    });

    it('shows the reader what arrived and runs the handler in its scope', async () => {
      register();
      const carrier = { 'x-mark': 'from-sender' };
      const inside = await runWithInboundContext(carrier, 'message', async () => {
        await new Promise((resolve) => setImmediate(resolve));
        return restored.getStore();
      });
      expect(inside).to.equal('from-sender');
      expect(seen).to.deep.equal([[carrier, 'message']]);
      expect(restored.getStore()).to.equal(undefined);
    });

    it('runs the handler unscoped when the reader has nothing to restore', () => {
      register();
      expect(
        runWithInboundContext({}, 'http', () => restored.getStore() ?? 'none'),
      ).to.equal('none');
    });

    it('does not show a carrier that is not an object', () => {
      register();
      for (const carrier of [null, undefined, 'text', 7, ['a']]) {
        expect(runWithInboundContext(carrier, 'message', () => 'ran')).to.equal(
          'ran',
        );
      }
      expect(seen).to.deep.equal([]);
    });

    it('stops showing arrivals once the reader is removed', () => {
      const remove = register();
      remove();
      runWithInboundContext({ 'x-mark': 'm' }, 'message', () => undefined);
      expect(seen).to.deep.equal([]);
      expect(hasInboundContextReaders()).to.equal(false);
    });

    it('nests the scopes of several readers', () => {
      const other = new AsyncLocalStorage<number>();
      register();
      registerInboundContextReader({ read: () => (fn) => other.run(2, fn) });
      const inside = runWithInboundContext({ 'x-mark': 'm' }, 'message', () => [
        restored.getStore(),
        other.getStore(),
      ]);
      expect(inside).to.deep.equal(['m', 2]);
    });

    it('skips a reader that throws and a scope that fails before the handler', () => {
      registerInboundContextReader({
        read() {
          throw new Error('reader');
        },
      });
      registerInboundContextReader({
        read: () => () => {
          throw new Error('scope');
        },
      });
      register();
      let runs = 0;
      const inside = runWithInboundContext({ 'x-mark': 'm' }, 'message', () => {
        runs += 1;
        return restored.getStore();
      });
      expect(inside).to.equal('m');
      expect(runs).to.equal(1);
    });

    it('runs the handler once and passes its error through a scope', () => {
      register();
      let runs = 0;
      expect(() =>
        runWithInboundContext({ 'x-mark': 'm' }, 'message', () => {
          runs += 1;
          throw new Error('handler');
        }),
      ).to.throw('handler');
      expect(runs).to.equal(1);
    });

    it('a consumed message is handled and announced inside the scope', async () => {
      register();
      const announcedIn: Array<string | undefined> = [];
      const stop = onPlatformEvent('message.consumed', () => {
        announcedIn.push(restored.getStore());
      });
      try {
        const value = { eventType: 'e', 'x-mark': 'sender' };
        const handledIn = await consumeAnnounced(
          { broker: 'redis', topic: 't', value },
          async () => restored.getStore(),
        );
        expect(handledIn).to.equal('sender');
        expect(announcedIn).to.deep.equal(['sender']);
        expect(seen).to.deep.equal([[value, 'message']]);
      } finally {
        stop();
      }
    });

    it('an HTTP request runs inside the scope, with its request context', () => {
      register();
      const req = {
        headers: { 'x-mark': 'caller', 'x-request-id': 'req-1' },
        method: 'GET',
        path: '/',
        protocol: 'http',
        originalUrl: '/',
        get: () => undefined,
        socket: {},
      } as unknown as Request;
      let inside: Array<string | undefined> = [];
      const next: NextFunction = () => {
        inside = [restored.getStore(), getRootId()];
      };
      const res = { setHeader: () => res } as unknown as Response;
      requestContextMiddleware(req, res, next);
      expect(inside[0]).to.equal('caller');
      expect(inside[1]).to.be.a('string');
      expect(seen[0]?.[1]).to.equal('http');
    });
  });
});
