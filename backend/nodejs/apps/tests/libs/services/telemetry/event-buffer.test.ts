import { expect } from 'chai';
import {
  eventBuffer,
  recordEvent,
  BufferedEvent,
} from '../../../../src/libs/services/telemetry/event-buffer';
import { runWithRequestContext } from '../../../../src/libs/context/request-context';

describe('telemetry event-buffer', () => {
  beforeEach(() => {
    eventBuffer.drain();
  });

  afterEach(() => {
    eventBuffer.drain();
  });

  describe('enqueue / drain / size', () => {
    it('should enqueue events and report size', () => {
      eventBuffer.enqueue({ event: 'a', timestamp: 't1' });
      eventBuffer.enqueue({ event: 'b', timestamp: 't2' });

      expect(eventBuffer.size()).to.equal(2);
    });

    it('should drain all events and empty the buffer', () => {
      eventBuffer.enqueue({ event: 'a', timestamp: 't1' });
      eventBuffer.enqueue({ event: 'b', timestamp: 't2' });

      const drained = eventBuffer.drain();

      expect(drained.map((e) => e.event)).to.deep.equal(['a', 'b']);
      expect(eventBuffer.size()).to.equal(0);
      expect(eventBuffer.drain()).to.deep.equal([]);
    });

    it('should preserve event props through drain', () => {
      const event: BufferedEvent = {
        event: 'login',
        timestamp: '2026-01-01T00:00:00Z',
        props: { domain: 'example.com', method: 'saml' },
      };
      eventBuffer.enqueue(event);

      const [drained] = eventBuffer.drain();
      expect(drained).to.deep.equal(event);
    });

    it('should drop events beyond the 5000-event cap instead of growing unbounded', () => {
      for (let i = 0; i < 5010; i++) {
        eventBuffer.enqueue({ event: `e${i}`, timestamp: 't' });
      }

      expect(eventBuffer.size()).to.equal(5000);
      const drained = eventBuffer.drain();
      // The overflow events (e5000..e5009) must be the ones dropped
      expect(drained[drained.length - 1]!.event).to.equal('e4999');
    });
  });

  describe('recordEvent', () => {
    it('should enqueue an event with an ISO timestamp and props', () => {
      recordEvent('signup', { domain: 'acme.io' });

      const [event] = eventBuffer.drain();
      expect(event!.event).to.equal('signup');
      expect(event!.props).to.deep.equal({ domain: 'acme.io', client: 'other' });
      expect(new Date(event!.timestamp).toISOString()).to.equal(
        event!.timestamp,
      );
    });

    it('should allow omitting props', () => {
      recordEvent('login');

      const [event] = eventBuffer.drain();
      expect(event!.event).to.equal('login');
      expect(event!.props).to.deep.equal({ client: 'other' });
    });

    it('should stamp the client and SDK language of the request in flight', () => {
      runWithRequestContext({ rootId: 'sdk-py-u1-abc' }, () =>
        recordEvent('search_performed', { orgId: 'o1' }),
      );
      runWithRequestContext({ rootId: 'mcp-abc' }, () => recordEvent('agent_run'));

      const [sdk, mcp] = eventBuffer.drain();
      expect(sdk!.props).to.deep.equal({ orgId: 'o1', client: 'sdk', sdk: 'py' });
      expect(mcp!.props).to.deep.equal({ client: 'mcp' });
    });

    it("should keep a caller's own client prop", () => {
      runWithRequestContext({ rootId: 'mcp-abc' }, () =>
        recordEvent('login', { client: 'slack' }),
      );

      expect(eventBuffer.drain()[0]!.props).to.deep.equal({ client: 'slack' });
    });
  });
});
