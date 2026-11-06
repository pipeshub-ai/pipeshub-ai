import { describe, it, expect } from 'vitest';
import { isRemoteRunStoppable, remoteRunIdOf } from '../remote-run';
import type { ConversationMessage } from '../../types';

const row = (messageType: ConversationMessage['messageType'], runId?: string) =>
  ({ _id: `${messageType}-${runId ?? 'none'}`, messageType, ...(runId ? { runId } : {}) }) as ConversationMessage;

describe('remoteRunIdOf', () => {
  it('names the run on a question the server has not answered yet', () => {
    expect(remoteRunIdOf([row('user_query', 'old'), row('bot_response', 'old'), row('user_query', 'live')])).toBe('live');
  });

  it('names nothing once the last question has an answer', () => {
    expect(remoteRunIdOf([row('user_query', 'r1'), row('bot_response', 'r1')])).toBeNull();
  });

  it('names nothing for an empty history, a missing id or a missing list', () => {
    expect(remoteRunIdOf([])).toBeNull();
    expect(remoteRunIdOf([row('user_query')])).toBeNull();
    expect(remoteRunIdOf(undefined)).toBeNull();
    expect(remoteRunIdOf(null)).toBeNull();
  });
});

describe('isRemoteRunStoppable', () => {
  const base = { isStreaming: false, remoteRunInProgress: true, remoteRunId: 'live' };

  it('is true for a reloaded run with an id to cancel', () => {
    expect(isRemoteRunStoppable(base)).toBe(true);
  });

  it.each([
    ['a run this tab streams', { isStreaming: true }],
    ['no run left on the server', { remoteRunInProgress: false }],
    ['no id to name', { remoteRunId: null }],
  ])('is false for %s', (_name, patch) => {
    expect(isRemoteRunStoppable({ ...base, ...patch })).toBe(false);
  });

  it('is false without a slot', () => {
    expect(isRemoteRunStoppable(undefined)).toBe(false);
  });
});
