import { expect } from 'chai';
import {
  applyOutboundContextCarriers,
  hasOutboundContextCarriers,
  outboundCarrierHeaderNames,
  registerOutboundContextCarrier,
  resetOutboundContextCarriers,
} from '../../../src/libs/context/outbound-carriers';
import {
  ENVELOPE_REQUEST_ID,
  HEADER_REQUEST_ID,
  injectEnvelope,
  injectRequestHeaders,
  runWithRequestContext,
} from '../../../src/libs/context/request-context';
import { BaseCommand } from '../../../src/libs/commands/command.interface';

class ProbeCommand extends BaseCommand<void> {
  constructor() {
    super('http://example.com');
  }
  sent(headers: Record<string, string>): Record<string, string> {
    return this.sanitizeHeaders(headers);
  }
  async execute(): Promise<void> {}
}

describe('outbound context carriers', () => {
  afterEach(() => resetOutboundContextCarriers());

  describe('with nothing registered (the default)', () => {
    it('reports no carrier and no header name', () => {
      expect(hasOutboundContextCarriers()).to.equal(false);
      expect(outboundCarrierHeaderNames()).to.deep.equal([]);
    });

    it('leaves outbound headers exactly as they were', () => {
      expect(injectRequestHeaders({ a: '1' })).to.deep.equal({ a: '1' });
      runWithRequestContext({ rootId: 'root-1' }, () => {
        expect(injectRequestHeaders({ a: '1' })).to.deep.equal({
          a: '1',
          [HEADER_REQUEST_ID]: 'root-1',
        });
      });
    });

    it('returns the same envelope object outside a request, and only the request id inside one', () => {
      const message = { eventType: 'x' };
      expect(injectEnvelope(message)).to.equal(message);
      runWithRequestContext({ rootId: 'root-1' }, () => {
        expect(injectEnvelope(message)).to.deep.equal({
          eventType: 'x',
          [ENVELOPE_REQUEST_ID]: 'root-1',
        });
      });
    });

    it('does nothing to a carrier handed to it directly', () => {
      const headers = { a: 1 };
      applyOutboundContextCarriers(headers, 'http');
      expect(headers).to.deep.equal({ a: 1 });
    });
  });

  describe('with a carrier registered', () => {
    it('adds its headers to outbound HTTP calls, next to the request id', () => {
      registerOutboundContextCarrier({
        httpHeaders: ['x-extra'],
        inject: (carrier, kind) => {
          if (kind === 'http') carrier['x-extra'] = 'e-1';
        },
      });
      runWithRequestContext({ rootId: 'root-1' }, () => {
        expect(injectRequestHeaders({ a: '1' })).to.deep.equal({
          a: '1',
          [HEADER_REQUEST_ID]: 'root-1',
          'x-extra': 'e-1',
        });
      });
      expect(injectRequestHeaders({})).to.deep.equal({ 'x-extra': 'e-1' });
    });

    it('adds its fields to a broker envelope, on a copy, with or without a request', () => {
      registerOutboundContextCarrier({
        inject: (carrier, kind) => {
          if (kind === 'message') carrier.extra = 'e-1';
        },
      });
      const message = { eventType: 'x' };
      expect(injectEnvelope(message)).to.deep.equal({ eventType: 'x', extra: 'e-1' });
      expect(message).to.deep.equal({ eventType: 'x' });
      runWithRequestContext({ rootId: 'root-1', orgId: 'org-1' }, () => {
        expect(injectEnvelope(message)).to.deep.equal({
          eventType: 'x',
          [ENVELOPE_REQUEST_ID]: 'root-1',
          orgId: 'org-1',
          extra: 'e-1',
        });
      });
    });

    it('tells the carrier which kind of call it is', () => {
      const kinds: string[] = [];
      registerOutboundContextCarrier({ inject: (_carrier, kind) => kinds.push(kind) });
      injectRequestHeaders({});
      injectEnvelope({});
      expect(kinds).to.deep.equal(['http', 'message']);
    });

    it('drops a header value that is not a string and keeps what the caller passed', () => {
      registerOutboundContextCarrier({
        inject: (carrier) => {
          carrier['x-number'] = 7;
          carrier['x-kept'] = { not: 'a string' };
        },
      });
      expect(injectRequestHeaders({ 'x-kept': 'mine' })).to.deep.equal({ 'x-kept': 'mine' });
    });

    it('skips a carrier that throws and still runs the next one', () => {
      registerOutboundContextCarrier({
        inject: () => {
          throw new Error('broken carrier');
        },
      });
      registerOutboundContextCarrier({
        inject: (carrier) => {
          carrier['x-extra'] = 'e-1';
        },
      });
      expect(injectRequestHeaders({})).to.deep.equal({ 'x-extra': 'e-1' });
      expect(injectEnvelope({ a: 1 })).to.deep.equal({ a: 1, 'x-extra': 'e-1' });
    });

    it('stops adding once the carrier is removed', () => {
      const remove = registerOutboundContextCarrier({
        httpHeaders: ['X-Extra'],
        inject: (carrier) => {
          carrier['x-extra'] = 'e-1';
        },
      });
      expect(outboundCarrierHeaderNames()).to.deep.equal(['x-extra']);
      remove();
      remove();
      expect(hasOutboundContextCarriers()).to.equal(false);
      expect(injectRequestHeaders({})).to.deep.equal({});
    });

    it('lets its declared headers through the header allow-list of a service command', () => {
      registerOutboundContextCarrier({
        httpHeaders: ['x-extra'],
        inject: (carrier, kind) => {
          if (kind !== 'http') return;
          carrier['x-extra'] = 'e-1';
          carrier['x-undeclared'] = 'u-1';
        },
      });
      const sent = new ProbeCommand().sent({ 'x-not-allowed': 'n' });
      expect(sent).to.have.property('x-extra', 'e-1');
      expect(sent).not.to.have.property('x-undeclared');
      expect(sent).not.to.have.property('x-not-allowed');
    });
  });
});
