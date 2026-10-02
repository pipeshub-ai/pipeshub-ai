import { describe, expect, it } from 'vitest';
import i18n from 'i18next';
import testI18n from '@/lib/__tests__/test-i18n';
import de from '@/lib/i18n/locales/de-DE.json';
import type { ServiceHealthDetail } from '@/lib/store/services-health-store';
import { serviceStateMessage } from '../service-state-message';

const t = testI18n.t;
const detail = (state: string, message = 'Waiting for Redis connection') =>
  ({ state, message }) as ServiceHealthDetail;

describe('serviceStateMessage', () => {
  it.each([
    ['healthy', 'Redis is ready'],
    ['unhealthy', "Redis responded but isn't healthy"],
    ['starting', 'Waiting for Redis'],
    ['pending', 'Waiting for the backend to share its configuration'],
    ['unknown', "Couldn't check Redis"],
  ])('words the %s state itself instead of showing the server text', (state, expected) => {
    expect(serviceStateMessage(detail(state), 'Redis', t)).toBe(expected);
  });

  it("shows the server's text only for a state it has no wording for", () => {
    expect(serviceStateMessage(detail('degraded', 'Redis is slow'), 'Redis', t)).toBe('Redis is slow');
    expect(serviceStateMessage(detail('degraded', '  '), 'Redis', t)).toBeUndefined();
  });

  it('shows nothing when the health API sent no detail', () => {
    expect(serviceStateMessage(undefined, 'Redis', t)).toBeUndefined();
  });

  it("uses the viewer's language, not the server's English", () => {
    const german = i18n.createInstance();
    void german.init({
      lng: 'de-DE',
      resources: { 'de-DE': { translation: de } },
      interpolation: { escapeValue: false },
      initAsync: false,
    });
    expect(serviceStateMessage(detail('starting'), 'Redis', german.t)).toBe('Warten auf Redis');
  });
});
