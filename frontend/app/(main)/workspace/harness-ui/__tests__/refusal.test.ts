/**
 * U4 HE-1..HE-3: one refusal mapping for every U4 screen, and every code a
 * screen maps reads in all ten locales.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { refusalText, toHarnessError } from '../refusal';
import { SCHEDULE_REFUSALS, FIRING_REASONS } from '../../schedules/copy';
import { MEMORY_REFUSALS } from '../../memories/api';
import { PUBLICATION_REFUSALS } from '../../../knowledge-base/publications/api';
import { copyKeys, nodeRefusal, relayedRefusal } from './fake-refusal';

const t = ((key: string) => `<${key}>`) as unknown as Parameters<typeof refusalText>[0];

describe('toHarnessError', () => {
  it('HE-1: a Node refusal keeps its code without the prefix and, on a write, the server sentence', () => {
    const error = toHarnessError(nodeRefusal(400, 'SCHEDULE_INVALID', 'cron must fire at most every 5 minutes'), true);
    expect(error).toMatchObject({ kind: 'invalid', status: 400, code: 'SCHEDULE_INVALID' });
    expect(error.message).toBe('cron must fire at most every 5 minutes');
    expect(refusalText(t, SCHEDULE_REFUSALS, error)).toBe(
      '<schedules.refusal.invalid> cron must fire at most every 5 minutes',
    );
    expect(toHarnessError(nodeRefusal(409, 'SCHEDULE_DISABLED'))).toMatchObject({ kind: 'failed', code: 'SCHEDULE_DISABLED' });
    expect(toHarnessError(nodeRefusal(503, null))).toMatchObject({ kind: 'unavailable', code: null });
    expect(toHarnessError(nodeRefusal(400, null, 'Admin access required'))).toMatchObject({ kind: 'forbidden' });
  });

  it('HE-2 (H3 FE-1): a relayed Python refusal gives its reasonCode and, on a write, its own sentence', () => {
    const runner = toHarnessError(relayedRefusal(400, 'runner_egress_invalid', "egress host 'x y' is not a host"), true);
    expect(runner).toMatchObject({ kind: 'invalid', code: 'runner_egress_invalid', message: "egress host 'x y' is not a host" });
    const query = toHarnessError(relayedRefusal(403, 'publication_forbidden', 'Not an owner'));
    expect(query.code).toBe('publication_forbidden');
    expect(query.kind).toBe('forbidden');
    expect(refusalText(t, { other: 'x' }, toHarnessError(nodeRefusal(500, 'UNKNOWN_THING')))).toBe('<aiAccounts.common.failed>');
  });

  it('H3 FE-2: a 502 with discovery_failed keeps its code and reads as that code, not the generic outage', () => {
    const error = toHarnessError(relayedRefusal(502, 'discovery_failed', 'Something went wrong while PipesHub tried to get it.'));
    expect(error).toMatchObject({ kind: 'unavailable', status: 502, code: 'discovery_failed' });
    const copy = { discovery_failed: 'probe.discoveryFailed', runner_start_failed: 'probe.runnerStartFailed', other: 'probe.other' };
    expect(refusalText(t, copy, error)).toBe('<probe.discoveryFailed>');
    expect(refusalText(t, copy, toHarnessError(relayedRefusal(502, 'runner_start_failed')))).toBe(
      '<probe.runnerStartFailed>',
    );
  });

  it('H3 FE-3: JSON text in a message is no longer read as a code', () => {
    const message = JSON.stringify({ reasonCode: 'runner_egress_invalid', message: 'x' });
    const error = toHarnessError(
      { type: 'ERR', statusCode: 400, message, originalError: { response: { status: 400, data: { error: { code: 'HTTP_BAD_REQUEST', message } } } } },
      true,
    );
    expect(error.code).toBe('BAD_REQUEST');
    expect(error.message).toBe(message);
  });
});

describe('copy', () => {
  it('HE-3: every code of every U4 table resolves in all ten locales', () => {
    const dir = path.resolve(__dirname, '../../../../../lib/i18n/locales');
    const locales = readdirSync(dir).filter((f) => f.endsWith('.json'));
    expect(locales).toHaveLength(10);
    const keys = copyKeys(
      SCHEDULE_REFUSALS,
      FIRING_REASONS,
      { other: 'schedules.reason.other' },
      MEMORY_REFUSALS,
      PUBLICATION_REFUSALS,
    );
    for (const file of locales) {
      const catalogue = JSON.parse(readFileSync(path.join(dir, file), 'utf8')) as Record<string, unknown>;
      for (const key of keys) {
        const value = key.split('.').reduce<unknown>(
          (node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined),
          catalogue,
        );
        expect(typeof value, `${file}: ${key}`).toBe('string');
      }
    }
  });
});
