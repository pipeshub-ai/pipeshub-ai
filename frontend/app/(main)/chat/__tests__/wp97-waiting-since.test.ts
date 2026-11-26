/**
 * WP-97: a run held on an approval for two hours said only "1 waiting for you". The card now carries
 * when it was asked (`requestedAt`, from the journal), and the steps' header says since when the oldest open card
 * waits; a decided card no longer counts, and a time that cannot be read is no time.
 */
import { describe, expect, it } from 'vitest';
import { savedApprovals, waitingSince } from '../interactions';
import { waitingSinceTime } from '../interaction-wording';
import enUS from '@/lib/i18n/locales/en-US.json';
import type { MessagePart, PendingApproval } from '../types';

const card = (status: PendingApproval['status'], requestedAt?: string): Pick<PendingApproval, 'status' | 'requestedAt'> => ({
  status,
  ...(requestedAt !== undefined ? { requestedAt } : {}),
});

describe('WP-97: since when an approval waits', () => {
  it('WP97-F1: the oldest open card sets the time; decided cards and unreadable times do not', () => {
    expect(
      waitingSince([
        card('pending', '2026-11-05T22:17:00.000Z'),
        card('pending', '2026-11-05T22:15:33.435Z'),
        card('approved', '2026-11-05T21:00:00.000Z'),
        card('sending', 'not a time'),
      ]),
    ).toBe('2026-11-05T22:15:33.435Z');
    expect(waitingSince([card('pending')])).toBeUndefined();
    expect(waitingSince([])).toBeUndefined();
  });

  it('WP97-F2: a saved approval part keeps when it was asked after a reload', () => {
    const parts = [
      {
        type: 'approval',
        interactionId: 'ix.approve.k76b4',
        toolCallId: 'ix.approve.k76b4',
        toolName: 'artifacts__save_artifact',
        args: '{}',
        status: 'pending',
        requestedAt: '2026-11-05T22:15:33.435Z',
      },
      { type: 'approval', interactionId: 'ix.approve.k2', toolName: 't', args: '{}', status: 'pending', requestedAt: 7 },
    ] as unknown as MessagePart[];
    const [first, second] = savedApprovals(parts);
    expect(first?.requestedAt).toBe('2026-11-05T22:15:33.435Z');
    expect(second).not.toHaveProperty('requestedAt');
  });

  it('WP97-F3: the time reads as a clock time today and with its date before today', () => {
    const now = new Date('2026-11-05T23:00:00');
    expect(waitingSinceTime('2026-11-05T22:15:00', 'en-US', now)).toMatch(/10:15/);
    expect(waitingSinceTime('2026-11-04T22:15:00', 'en-US', now)).toMatch(/Nov 4/);
    expect(waitingSinceTime('nope', 'en-US', now)).toBeUndefined();
  });

  it('WP97-F4: the header sentence names the count and the time', () => {
    const words = (enUS as { interaction: Record<string, string> }).interaction;
    expect(words['waitingCountSince_one']).toBe('{{count}} waiting for you since {{time}}');
    expect(words['waitingCountSince_other']).toContain('{{time}}');
  });
});
