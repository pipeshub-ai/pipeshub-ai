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

describe('deriveInstanceSetupStatus – action items', () => {
  const item = (severity: 'warning' | 'error') => ({
    code: `item-${severity}`,
    severity,
    title: 'Switch to certificate sign-in',
    message: 'Upload a certificate',
    action: 'authenticate' as const,
  });

  it('asks for action while the connector reports something to do', () => {
    const view = deriveInstanceSetupStatus({ ...base, actionItems: [item('warning')] });
    expect(view.key).toBe('action_needed');
    expect(view.badgeColor).toBe('amber');
  });

  it('is red when any item is an error', () => {
    const view = deriveInstanceSetupStatus({ ...base, actionItems: [item('warning'), item('error')] });
    expect(view.badgeColor).toBe('red');
  });

  it('is ready once nothing is left to do', () => {
    expect(deriveInstanceSetupStatus({ ...base, actionItems: [] }).key).toBe('ready');
  });

  it('lets an incomplete configuration win over action items', () => {
    const view = deriveInstanceSetupStatus({ ...base, isConfigured: false, actionItems: [item('error')] });
    expect(view.key).toBe('not_configured');
  });
});
