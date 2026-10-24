import { describe, it, expect } from 'vitest';
import type { TFunction } from 'i18next';
import { userRoleLabel } from '../../constants';

const t = ((key: string) => `t:${key}`) as unknown as TFunction;

describe('userRoleLabel', () => {
  it('translates the roles it knows, whatever their case', () => {
    expect(userRoleLabel('Admin', t)).toBe('t:workspace.users.roles.admin');
    expect(userRoleLabel('guest', t)).toBe('t:workspace.users.roles.guest');
  });

  it('treats a missing role as a member', () => {
    expect(userRoleLabel(undefined, t)).toBe('t:workspace.users.roles.member');
  });

  it('shows any other role as the server sent it', () => {
    expect(userRoleLabel('Auditor', t)).toBe('Auditor');
  });
});
