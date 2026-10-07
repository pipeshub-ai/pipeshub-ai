import { describe, it, expect } from 'vitest';
import { deriveInstanceSetupStatus } from '../instance-status';

const base = {
  isConfigured: true,
  isAuthenticated: true,
  authType: 'NONE',
  type: 'Local FS',
  scope: 'personal',
  isActive: true,
} as const;

describe('deriveInstanceSetupStatus – desktop presence', () => {
  it('shows desktop_offline when Node reports no desktop holds the claim', () => {
    const view = deriveInstanceSetupStatus({ ...base, desktopOnline: false });
    expect(view.key).toBe('desktop_offline');
    expect(view.badgeColor).toBe('amber');
  });

  it('is ready when the desktop is online', () => {
    expect(deriveInstanceSetupStatus({ ...base, desktopOnline: true }).key).toBe('ready');
  });

  it('treats an absent value as unknown, not offline', () => {
    expect(deriveInstanceSetupStatus({ ...base }).key).toBe('ready');
  });

  it('never reports offline while sync is disabled: no claim is expected yet', () => {
    const view = deriveInstanceSetupStatus({ ...base, isActive: false, desktopOnline: false });
    expect(view.key).toBe('ready');
  });

  it('lets an incomplete configuration win over presence', () => {
    const view = deriveInstanceSetupStatus({ ...base, isConfigured: false, desktopOnline: false });
    expect(view.key).toBe('not_configured');
  });
});
