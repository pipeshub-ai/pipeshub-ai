import { describe, expect, it } from 'vitest';
import {
  canEditLinkedOAuthApp,
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
    {
      mayEditLinkedOAuthApp: canEditLinkedOAuthApp(isAdmin, scope),
      mayEnterOAuthClient: canEnterOAuthClient(isAdmin, scope),
    }
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

  it('lets only an admin on a team connector change the linked app', () => {
    expect(canEditLinkedOAuthApp(true, 'team')).toBe(true);
    expect(canEditLinkedOAuthApp(true, 'personal')).toBe(false);
    expect(canEditLinkedOAuthApp(false, 'team')).toBe(false);
    expect(canEditLinkedOAuthApp(null, 'team')).toBeNull();
  });

  it('shows a member the client fields only while they create their own app', () => {
    expect(clientFieldsShown(false, 'personal')).toBe(true);
    // A saved app (theirs or the organization's) is used as is: its values are never sent to them.
    expect(clientFieldsShown(false, 'personal', 'app-1')).toBe(false);
    expect(clientFieldsShown(false, 'team')).toBe(false);
  });

  it('keeps showing an admin the client fields of the app linked to a team connector', () => {
    expect(clientFieldsShown(true, 'team', 'app-1')).toBe(true);
  });

  it('uses the app linked to an admin personal connector as is, so reusing their own app is not blocked', () => {
    // Their own app's values are never sent, and the server does not change an org app from here.
    expect(clientFieldsShown(true, 'personal', 'app-1')).toBe(false);
    expect(clientFieldsShown(true, 'personal')).toBe(true);
  });
});
