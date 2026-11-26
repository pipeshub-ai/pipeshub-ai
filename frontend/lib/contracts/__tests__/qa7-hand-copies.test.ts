import { describe, expect, it } from 'vitest';
import { YIELD_REASONS } from '@/lib/contracts/harness/generated/core/enums';
import {
  EXECUTION_STATUSES,
  TERMINAL_EXECUTION_STATUSES,
} from '@/lib/contracts/harness/generated/ext/pipeshub/enums';
import { ERROR_CODES } from '@/lib/contracts/harness/generated/ext/pipeshub/errors';
import { harnessHttpCode } from '@/lib/contracts/harness/codes';
import { runStillActive } from '@/app/(main)/chat/durable-run-ui';
import { MEMORY_REFUSALS } from '@/app/(main)/workspace/memories/api';

/** QA-7 (frontend half): the frontend reads the generated enumerations and codes, never its own copy. */
describe('QA-7 hand copies (frontend)', () => {
  it('QA7-6 a child run stops refreshing exactly at the run document’s terminal statuses', () => {
    for (const status of EXECUTION_STATUSES) {
      expect(runStillActive(status), status).toBe(!(TERMINAL_EXECUTION_STATUSES as readonly string[]).includes(status));
    }
    expect(runStillActive(undefined)).toBe(false);
  });

  it('QA7-7 the memory refusals are the catalogue’s memory group, every one', () => {
    const memory = Object.entries(ERROR_CODES)
      .filter(([, entry]) => entry.group === 'memory')
      .map(([code]) => code);
    expect(Object.keys(MEMORY_REFUSALS).sort()).toEqual(memory.sort());
  });

  it('QA7-7 a catalogue code is matched as Node’s error middleware answers it', () => {
    expect(harnessHttpCode('APPROVER_REQUIRED')).toBe('HTTP_AGENT_HARNESS_APPROVER_REQUIRED');
  });

  it('QA7-8 payload_store_unavailable is a yield reason', () => {
    expect(YIELD_REASONS).toContain('payload_store_unavailable');
  });
});
