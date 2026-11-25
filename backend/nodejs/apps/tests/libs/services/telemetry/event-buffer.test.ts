import { expect } from 'chai';
import {
  eventBuffer,
  recordEvent,
  BufferedEvent,
} from '../../../../src/libs/services/telemetry/event-buffer';

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
        props: { method: 'saml' },
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
      recordEvent('signup', { orgId: 'org-1', channel: 'direct' });

      const [event] = eventBuffer.drain();
      expect(event!.event).to.equal('signup');
      expect(event!.props).to.deep.equal({ orgId: 'org-1', channel: 'direct' });
      expect(new Date(event!.timestamp).toISOString()).to.equal(
        event!.timestamp,
      );
    });

    it('drops every prop that names whose event it is, and keeps the opaque ids', () => {
      recordEvent('login', {
        orgId: 'org-1',
        userId: 'user-1',
        email: 'jane@acme.io',
        domain: 'acme.io',
        fullName: 'Jane Doe',
        full_name: 'Jane Doe',
        orgName: 'Acme',
        referrer_domain: 'acme.io',
        user_email: 'jane@acme.io',
        auth_method: 'password',
      });

      const [event] = eventBuffer.drain();
      expect(event!.props).to.deep.equal({
        orgId: 'org-1',
        userId: 'user-1',
        auth_method: 'password',
      });
    });

    it('should allow omitting props', () => {
      recordEvent('login');

      const [event] = eventBuffer.drain();
      expect(event!.event).to.equal('login');
      expect(event!.props).to.be.undefined;
    });
  });
});
