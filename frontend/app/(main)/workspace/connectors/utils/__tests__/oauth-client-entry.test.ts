import { describe, expect, it } from 'vitest';
import {
  canEnterOAuthClient,
  resolveOAuthFieldVisibility,
  shouldRenderOAuthAuthSchemaField,
} from '../auth-helpers';

function clientFieldsShown(
  isAdmin: boolean | null,
  scope: string,
  oauthConfigId?: string
): boolean {
  const { oauthFieldVisibility } = resolveOAuthFieldVisibility(
    { oauthConfigId },
    null,
    true,
    { isAdmin, mayEnterOAuthClient: canEnterOAuthClient(isAdmin, scope) }
  );
  return shouldRenderOAuthAuthSchemaField('clientSecret', oauthFieldVisibility);
}

describe('who may enter an OAuth client', () => {
  it('lets anyone enter one on their own personal connector, and only admins on a team connector', () => {
    expect(canEnterOAuthClient(false, 'personal')).toBe(true);
    expect(canEnterOAuthClient(true, 'team')).toBe(true);
    expect(canEnterOAuthClient(false, 'team')).toBe(false);
    expect(canEnterOAuthClient(null, 'team')).toBeNull();
  });

  it('shows a member the client fields only while they create their own app', () => {
    expect(clientFieldsShown(false, 'personal')).toBe(true);
    // A saved app (theirs or the organization's) is used as is: its values are never sent to them.
    expect(clientFieldsShown(false, 'personal', 'app-1')).toBe(false);
    expect(clientFieldsShown(false, 'team')).toBe(false);
  });

  it('keeps showing an admin the client fields of a linked app', () => {
    expect(clientFieldsShown(true, 'team', 'app-1')).toBe(true);
    expect(clientFieldsShown(true, 'personal', 'app-1')).toBe(true);
  });
});
