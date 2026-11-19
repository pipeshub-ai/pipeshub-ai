import { describe, expect, it } from 'vitest';
import { canConvertToServiceAgent, isOrgShareLocked } from '../agent-builder-permissions';
import type { AgentDetail } from '../../types';

const agent = (user_role: string) => ({ _key: 'a1', user_role, can_edit: true }) as unknown as AgentDetail;

describe('canConvertToServiceAgent', () => {
  it('lets the creator of a new agent convert it', () => {
    expect(canConvertToServiceAgent(null, false, false)).toBe(true);
  });

  it('does not offer it while an existing agent is still loading', () => {
    expect(canConvertToServiceAgent(null, true, true)).toBe(false);
  });

  it('lets the owner convert', () => {
    expect(canConvertToServiceAgent(agent('OWNER'), false, true)).toBe(true);
  });

  it.each(['WRITER', 'ORGANIZER', 'READER'])('hides it from a %s who is not an administrator', (role) => {
    expect(canConvertToServiceAgent(agent(role), false, true)).toBe(false);
  });

  it('lets an administrator convert an agent they do not own', () => {
    expect(canConvertToServiceAgent(agent('WRITER'), true, true)).toBe(true);
  });

  it('treats an admin state that has not loaded as not an administrator', () => {
    expect(canConvertToServiceAgent(agent('WRITER'), null, true)).toBe(false);
  });
});

describe('isOrgShareLocked', () => {
  const shareable = (can_share: boolean, shareWithOrg: boolean) =>
    ({ _key: 'a1', can_share, shareWithOrg }) as unknown as AgentDetail;

  it('locks the switch for an editor without can_share on an unshared agent', () => {
    expect(isOrgShareLocked(shareable(false, false), true)).toBe(true);
  });

  it('keeps an already shared agent switchable', () => {
    expect(isOrgShareLocked(shareable(false, true), true)).toBe(false);
  });

  it('leaves it open with can_share, and for a new agent', () => {
    expect(isOrgShareLocked(shareable(true, false), true)).toBe(false);
    expect(isOrgShareLocked(null, true)).toBe(false);
  });

  it('locks it without the share permission', () => {
    expect(isOrgShareLocked(shareable(true, true), false)).toBe(true);
  });
});
