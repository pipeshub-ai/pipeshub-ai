import { describe, it, expect } from 'vitest';
import enUS from '../en-US.json';
import enIN from '../en-IN.json';

describe('connector inactive copy (en-US / en-IN)', () => {
  it.each([
    ['en-US', enUS],
    ['en-IN', enIN],
  ] as const)('%s uses "Inactive" / "inactive", not "in-active"', (_name, locale) => {
    expect(locale.status.inactive).toBe('Inactive');

    const { inactiveOne, inactiveMany } = locale.workspace.actions.card;
    expect(inactiveOne).toBe('1 inactive instance');
    expect(inactiveMany).toBe('{{count}} inactive instances');

    expect(inactiveOne.toLowerCase()).not.toContain('in-active');
    expect(inactiveMany.toLowerCase()).not.toContain('in-active');
  });
});
