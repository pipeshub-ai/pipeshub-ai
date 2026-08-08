import { describe, expect, it } from 'vitest';
import { describeSyncFailure, resolveFailureSummary } from '../sync-failure-copy';

describe('describeSyncFailure', () => {
  it('maps AUTH to reconnect remediation', () => {
    const copy = describeSyncFailure('AUTH');
    expect(copy.key).toBe('workspace.connectors.syncProgress.failure.auth');
    expect(copy.title).toMatch(/authentication/i);
    expect(copy.remediation).toMatch(/Authorize/i);
  });

  it('falls back to UNKNOWN for missing codes', () => {
    const copy = describeSyncFailure(null);
    expect(copy.code).toBe('UNKNOWN');
    expect(copy.remediation).toMatch(/Retry/i);
  });

  it('is case-insensitive', () => {
    expect(describeSyncFailure('permission').code).toBe('PERMISSION');
  });
});

describe('resolveFailureSummary', () => {
  it('humanizes unauthorized_client dumps without repeating the tuple', () => {
    const raw =
      "('unauthorized_client: Client is unauthorized to retrieve access tokens using this method, or client not authorized for any of the scopes requested.', {'error': 'unauthorized_client', 'error_description': 'Client is unauthorized to retrieve access tokens using this method, or client not authorized…";
    const summary = resolveFailureSummary('AUTH', raw);
    expect(summary.text).toMatch(/google blocked this app/i);
    expect(summary.text).not.toContain("{'error'");
    expect(summary.text.toLowerCase().split('unauthorized').length).toBeLessThan(3);
  });

  it('falls back to code summary for technical blobs', () => {
    const summary = resolveFailureSummary('AUTH', "('RefreshError', {'error': 'other'})");
    expect(summary.text).toMatch(/could not sign in/i);
  });

  it('keeps a short human backend reason', () => {
    const summary = resolveFailureSummary(
      'AUTH',
      'The saved Google sign-in expired or was revoked.'
    );
    expect(summary.text).toBe('The saved Google sign-in expired or was revoked.');
  });
});
