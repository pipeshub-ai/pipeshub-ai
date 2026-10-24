import { describe, expect, it } from 'vitest';
import type { ThreadMessageLike } from '@assistant-ui/react';
import {
  connectToolsPresentation,
  connectableHere,
  parseUnavailableTools,
  toolsUnavailableNotice,
  type UnavailableTool,
} from '../unavailable-tools';
import { MCP_SERVER_CONFIG_MISSING, TOOLSET_CONFIG_MISSING, toolsBlockedDetails } from '../stream-error';
import { loadHistoricalMessages } from '../runtime';
import { stampLiveUnavailableTools } from '../streaming';
import type { ConversationMessage } from '../types';

const GMAIL: UnavailableTool = {
  kind: 'toolset',
  instanceId: 'ts-gmail',
  displayName: 'Gmail',
  name: 'gmail',
  problem: 'not_authenticated',
  sharedCredential: false,
  userCanConnect: true,
};
const GITHUB: UnavailableTool = {
  kind: 'mcp',
  instanceId: 'mcp-gh',
  displayName: 'GitHub',
  problem: 'not_configured',
  authMode: 'oauth',
  sharedCredential: false,
  userCanConnect: true,
};

describe('tools_unavailable event', () => {
  it('reads the items the backend sends', () => {
    expect(toolsUnavailableNotice({ agentId: 'a-1', serviceAccount: false, items: [GMAIL, GITHUB] })).toEqual({
      agentId: 'a-1',
      serviceAccount: false,
      items: [GMAIL, GITHUB],
    });
  });

  it('drops malformed items and repeats, and has nothing when none are left', () => {
    expect(
      parseUnavailableTools([
        null,
        { kind: 'toolset' },
        { kind: 'toolset', instanceId: 'x', problem: 'exploded' },
        { kind: 'robot', instanceId: 'x', problem: 'not_found' },
        { kind: 'mcp', instanceId: 'y', problem: 'not_found', sharedCredential: 'yes', userCanConnect: 1 },
        { kind: 'mcp', instanceId: 'y', problem: 'not_found' },
      ]),
    ).toEqual([{ kind: 'mcp', instanceId: 'y', displayName: 'y', problem: 'not_found', sharedCredential: false, userCanConnect: false }]);
    expect(toolsUnavailableNotice({ items: [{ nope: true }] })).toBeNull();
    expect(toolsUnavailableNotice('x')).toBeNull();
  });
});

describe('connectableHere', () => {
  it('offers Connect only for what the asker can fix', () => {
    expect(connectableHere(GMAIL, false)).toBe(true);
    expect(connectableHere(GITHUB, false)).toBe(true);
    expect(connectableHere(GMAIL, true)).toBe(false);
    expect(connectableHere({ ...GITHUB, sharedCredential: true }, false)).toBe(false);
    expect(connectableHere({ ...GITHUB, problem: 'not_found' }, false)).toBe(false);
    expect(connectableHere({ ...GITHUB, authMode: 'none' }, false)).toBe(false);
    expect(connectableHere({ ...GMAIL, userCanConnect: false }, false)).toBe(false);
  });
});

describe('connectToolsPresentation', () => {
  it('opens the card when the answer names a tool it could not use', () => {
    expect(connectToolsPresentation("I can't read your Gmail until you connect it.", [GMAIL])).toBe('card');
    expect(connectToolsPresentation('GITHUB is not connected.', [GMAIL, GITHUB])).toBe('card');
  });

  it('keeps a chip for an answer that never needed them', () => {
    expect(connectToolsPresentation('Coffee is great, I like it strong.', [GMAIL, GITHUB])).toBe('chip');
    expect(connectToolsPresentation('', [GMAIL])).toBe('chip');
  });
});

describe('toolsBlockedDetails', () => {
  it('reads the items of a service account agent that was stopped', () => {
    expect(
      toolsBlockedDetails({
        code: TOOLSET_CONFIG_MISSING,
        details: { agentId: 'sa-1', serviceAccount: true, items: [{ ...GMAIL, userCanConnect: false }], servers: [] },
      }),
    ).toEqual({ agentId: 'sa-1', serviceAccount: true, items: [{ ...GMAIL, userCanConnect: false }] });
  });

  it('maps an older server’s details.servers into the same items', () => {
    expect(
      toolsBlockedDetails({
        code: MCP_SERVER_CONFIG_MISSING,
        details: {
          agentId: 'a-1',
          serviceAccount: false,
          servers: [
            { instanceId: 'mcp-gh', name: 'GitHub', problem: 'not_connected', authMode: 'oauth', sharedCredential: false },
            { instanceId: 'mcp-old', name: 'Old', problem: 'not_found' },
            { instanceId: 'mcp-s', name: 'Search', problem: 'not_connected', authMode: 'api_token', sharedCredential: true },
          ],
        },
      }),
    ).toEqual({
      agentId: 'a-1',
      serviceAccount: false,
      items: [
        { kind: 'mcp', instanceId: 'mcp-gh', displayName: 'GitHub', problem: 'not_authenticated', authMode: 'oauth', sharedCredential: false, userCanConnect: true },
        { kind: 'mcp', instanceId: 'mcp-old', displayName: 'Old', problem: 'not_found', sharedCredential: false, userCanConnect: false },
        { kind: 'mcp', instanceId: 'mcp-s', displayName: 'Search', problem: 'not_authenticated', authMode: 'api_token', sharedCredential: true, userCanConnect: false },
      ],
    });
  });

  it('has nothing for other errors or a toolset error without items', () => {
    expect(toolsBlockedDetails({ code: 'stream_error', details: { items: [GMAIL] } })).toBeNull();
    expect(toolsBlockedDetails({ code: TOOLSET_CONFIG_MISSING })).toBeNull();
    expect(toolsBlockedDetails({ code: TOOLSET_CONFIG_MISSING, details: { servers: [] } })).toBeNull();
  });
});

describe('stored and live unavailable tools', () => {
  const row = (extra: Partial<ConversationMessage>): ConversationMessage => ({
    _id: 'm',
    messageType: 'bot_response',
    content: 'answer',
    contentFormat: 'MARKDOWN',
    citations: [],
    followUpQuestions: [],
    referenceData: [],
    modelInfo: { modelKey: '', modelName: '', modelProvider: '', chatMode: '' } as never,
    createdAt: '2026-10-01T00:00:00.000Z',
    updatedAt: '2026-10-01T00:00:00.000Z',
    feedback: [],
    ...extra,
  });

  it('a stored answer keeps the tools it could not use', () => {
    const { messages } = loadHistoricalMessages([
      row({ _id: 'q', messageType: 'user_query', content: 'hi' }),
      row({ _id: 'a', unavailableTools: [GMAIL, { junk: true }] }),
    ]);
    const answer = messages.find((m) => m.role === 'assistant');
    expect((answer?.metadata?.custom as { unavailableTools?: unknown }).unavailableTools).toEqual([GMAIL]);
  });

  it('a stored answer without them carries none', () => {
    const { messages } = loadHistoricalMessages([row({ _id: 'q', messageType: 'user_query', content: 'hi' }), row({ _id: 'a' })]);
    expect(messages.find((m) => m.role === 'assistant')?.metadata?.custom).not.toHaveProperty('unavailableTools');
  });

  it('the live event fills in only what the stored answer does not say', () => {
    const bare = { role: 'assistant', content: [], metadata: { custom: { messageId: 'a' } } } as unknown as ThreadMessageLike;
    stampLiveUnavailableTools(bare, { serviceAccount: false, items: [GMAIL] });
    expect(bare.metadata?.custom).toEqual({ messageId: 'a', unavailableTools: [GMAIL] });

    const stored = { role: 'assistant', content: [], metadata: { custom: { unavailableTools: [GITHUB] } } } as unknown as ThreadMessageLike;
    stampLiveUnavailableTools(stored, { serviceAccount: false, items: [GMAIL] });
    expect((stored.metadata?.custom as { unavailableTools: unknown }).unavailableTools).toEqual([GITHUB]);
  });
});
