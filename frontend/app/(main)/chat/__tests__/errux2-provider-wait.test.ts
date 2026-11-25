import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { i18n } from '@/lib/i18n';
import type { SSEEvent } from '@/lib/api';
import { createAGUIEventHandler } from '../agui-event-handler';
import type { StreamMessageCallbacks } from '../api';

/** ERR-UX2 (EU-O5): the provider wait's frame, the same from a durable run and today's loop, shows the countdown. */

const VECTOR = JSON.parse(
  readFileSync(join(__dirname, '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'provider-wait.json'), 'utf8'),
) as { frameType: string; status: string; retryAtField: string };

describe('ERR-UX2: provider-wait.json', () => {
  it('the frame shows the wait counted down from the reader\'s clock', async () => {
    await i18n.changeLanguage('en-US');
    const onStatus = vi.fn();
    const handle = createAGUIEventHandler({ onStatus } as unknown as StreamMessageCallbacks, undefined, {
      now: () => Date.parse('2026-10-11T12:00:00.000Z'),
    });
    const frame: SSEEvent = {
      event: VECTOR.frameType,
      data: { type: VECTOR.frameType, snapshot: { status: VECTOR.status, [VECTOR.retryAtField]: '2026-10-11T12:00:07.000Z' } },
    };
    handle(frame);
    expect(onStatus).toHaveBeenLastCalledWith({ status: VECTOR.status, message: 'Waiting for the AI model (busy or too many requests), retrying in 7 s…' });
  });
});
