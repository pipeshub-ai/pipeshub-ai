import { describe, it, expect } from 'vitest';
import type { ThreadMessageLike } from '@assistant-ui/react';
import { columnIsThinking, columnStatus } from '../column-state';
import type { ChatSlot, MessagePart } from '../../types';

const slot = (over: Partial<ChatSlot> = {}): ChatSlot =>
  ({ messages: [], isStreaming: false, pendingAskUserQuestion: null, ...over }) as unknown as ChatSlot;

const user: ThreadMessageLike = { id: 'u', role: 'user', content: [{ type: 'text', text: 'Q' }] };
const reply = (text: string, custom: Record<string, unknown> = { messageId: 'a1' }): ThreadMessageLike => ({
  id: 'a',
  role: 'assistant',
  content: [{ type: 'text', text }],
  metadata: { custom },
});

describe('columnStatus', () => {
  it('is starting before the slot has any message', () => {
    expect(columnStatus(undefined)).toBe('starting');
    expect(columnStatus(slot())).toBe('starting');
  });

  it('is streaming while a run is going', () => {
    expect(columnStatus(slot({ isStreaming: true, messages: [user, reply('')] }))).toBe('streaming');
  });

  it('is streaming while a run the page did not start is still going (reload while thinking)', () => {
    expect(columnStatus(slot({ remoteRunInProgress: true, messages: [user] }))).toBe('streaming');
  });

  it('is waiting on an open question card', () => {
    const pendingAskUserQuestion = { status: 'pending' } as ChatSlot['pendingAskUserQuestion'];
    expect(columnStatus(slot({ pendingAskUserQuestion, messages: [user, reply('Which region?')] }))).toBe('waiting');
  });

  it('is waiting on an approval prompt', () => {
    const parts = [
      {
        type: 'tool_call',
        status: 'awaiting_approval',
        approval: { approvalId: 'ap1', toolName: 'send', serverName: 'mail', kind: 'write', expiresAt: 9999999999999 },
      },
    ] as unknown as MessagePart[];
    expect(columnStatus(slot({ messages: [user, reply('Waiting for your approval', { messageId: 'a1', persistedParts: parts })] }))).toBe('waiting');
  });

  it('is error for an unsaved error row and for a row carrying a stream error', () => {
    expect(columnStatus(slot({ messages: [user, reply('Boom', {})] }))).toBe('error');
    expect(columnStatus(slot({ messages: [user, reply('Boom', { messageId: 'a1', streamError: { code: 'x' } })] }))).toBe('error');
    expect(columnStatus(slot({ messages: [user] }))).toBe('error');
  });

  it('is empty for a saved reply with no text and done for a saved answer', () => {
    expect(columnStatus(slot({ messages: [user, reply('  ')] }))).toBe('empty');
    expect(columnStatus(slot({ messages: [user, reply('Revenue was $4.2M.')] }))).toBe('done');
  });

  it('counts a stopped answer that kept its text as done', () => {
    expect(columnStatus(slot({ messages: [user, reply('Partial', { status: 'stopped' })] }))).toBe('done');
  });
});

describe('columnIsThinking', () => {
  it('is thinking while a run is going and nothing has been answered yet', () => {
    expect(columnIsThinking(slot({ remoteRunInProgress: true, messages: [user] }))).toBe(true);
    expect(columnIsThinking(slot({ isStreaming: true, messages: [user] }))).toBe(true);
  });

  it('stops being thinking once answer text arrives, and is never thinking when idle', () => {
    expect(columnIsThinking(slot({ isStreaming: true, streamingContent: 'Pipeshub is', messages: [user] }))).toBe(false);
    expect(columnIsThinking(slot({ messages: [user, reply('Done')] }))).toBe(false);
    expect(columnIsThinking(undefined)).toBe(false);
  });
});
