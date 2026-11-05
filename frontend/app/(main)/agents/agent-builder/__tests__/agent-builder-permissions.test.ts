import { describe, expect, it } from 'vitest';
import { canConvertToServiceAgent } from '../agent-builder-permissions';
import type { AgentDetail } from '../../types';

const agent = (user_role: string) => ({ _key: 'a1', user_role, can_edit: true }) as unknown as AgentDetail;

describe('canConvertToServiceAgent', () => {
  it('lets the creator of a new agent convert it', () => {
    expect(canConvertToServiceAgent(null, false)).toBe(true);
  });

  it('lets the owner convert', () => {
    expect(canConvertToServiceAgent(agent('OWNER'), false)).toBe(true);
  });

  it.each(['WRITER', 'ORGANIZER', 'READER'])('hides it from a %s who is not an administrator', (role) => {
    expect(canConvertToServiceAgent(agent(role), false)).toBe(false);
  });

  it('lets an administrator convert an agent they do not own', () => {
    expect(canConvertToServiceAgent(agent('WRITER'), true)).toBe(true);
  });

  it('treats an admin state that has not loaded as not an administrator', () => {
    expect(canConvertToServiceAgent(agent('WRITER'), null)).toBe(false);
  });
});
