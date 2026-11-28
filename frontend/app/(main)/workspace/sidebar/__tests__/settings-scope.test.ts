import { describe, expect, it } from 'vitest';
import { settingsScopeFor } from '../settings-scope';

describe('settingsScopeFor', () => {
  it.each(['/workspace/profile', '/workspace/connectors/personal', '/workspace/archived-chats', '/workspace/developer-settings/personal-access-tokens', '/workspace/actions/personal/x', '/workspace/ai-accounts', '/workspace/schedules', '/workspace/memories'])(
    'puts an admin on %s in personal settings',
    (path) => expect(settingsScopeFor(path, true)).toBe('personal'),
  );

  it.each(['/workspace/general', '/workspace/connectors/team', '/workspace/developer-settings/oauth2', '/workspace/profile-x', '/workspace/ai-access'])(
    'puts an admin on %s in workspace settings',
    (path) => expect(settingsScopeFor(path, true)).toBe('workspace'),
  );

  it('never puts a member, or someone whose role is still loading, in workspace settings', () => {
    expect(settingsScopeFor('/workspace/general', false)).toBe('personal');
    expect(settingsScopeFor('/workspace/ai-models', null)).toBe('personal');
  });

  it('takes extra personal pages', () => {
    expect(settingsScopeFor('/workspace/mine', true, ['/workspace/mine'])).toBe('personal');
  });
});
