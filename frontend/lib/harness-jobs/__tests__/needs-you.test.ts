import { describe, expect, it } from 'vitest';
import type { MessagePart, PendingAskUserQuestion } from '@/chat/types';
import { needsYou, type NeedsYouSnapshot } from '../needs-you';
import { asked, resolved, vector } from './chat-fixture';
import { plainText, words } from './fixtures';

const base = (over: Partial<NeedsYouSnapshot> = {}): NeedsYouSnapshot => ({
  isStreaming: true,
  pendingApprovals: [],
  pendingAskUserQuestion: null,
  newestReplyId: 'a1',
  newestParts: [],
  streamingParts: [],
  canDecide: true,
  now: Date.parse('2026-11-09T12:00:00Z'),
  ...over,
});

const ids = (actions: ReadonlyArray<{ id: string }>) => actions.map((a) => a.id);

describe('every open ask as the "Needs you" card', () => {
  it('one approval: the action named against the safe alternative, one plain reason, the rest under Why?', () => {
    const [item] = needsYou(base({ pendingApprovals: asked({ reason: { rule: 'flow_external_write', sensitivity: 'private', sources: [{ title: 'Pricing sheet' }], moreSources: 2, destination: '#sales', conversationGrant: false } }) }), words);
    expect(item.ask).toMatchObject({ kind: 'approval', count: 1, reason: 'Because this sends data outside your company' });
    expect(ids(item.ask.actions)).toEqual(['approve', 'deny']);
    expect(item.ask.actions.map((a) => a.label)).toEqual(['Allow', "Don't allow"]);
    expect(item.ask.detail).toBe('Sends to #sales');
    expect(item.ask.why.join(' ')).toContain('Pricing sheet');
    expect(plainText(item.ask.title)).toBe(true);
  });

  it('a batch: one card, "Allow all N", and "Allow for this chat" where the rule offers it', () => {
    const reason = { rule: 'writes', layer: 'org' as const, sources: [], moreSources: 0, conversationGrant: true, destination: 'jira.acme.com' };
    let list = asked({ interactionId: 'ix-1', toolCallId: 'c1', toolName: 'jira__create_issue', afterTurns: 2, reason });
    list = asked({ interactionId: 'ix-2', toolCallId: 'c2', toolName: 'jira__create_issue', afterTurns: 2, reason }, list);
    const items = needsYou(base({ pendingApprovals: list }), words);
    expect(items).toHaveLength(1);
    expect(items[0].ask.count).toBe(2);
    expect(items[0].ask.title).toBe('Create 2 issues in Jira?');
    expect(items[0].ask.detail).toBe('Sends to jira.acme.com');
    expect(items[0].ask.actions.map((a) => a.label)).toEqual(['Allow all 2', "Don't allow any", 'Allow jira.acme.com for this chat']);
  });

  it('a decided card is gone; a run that ended leaves no approval to answer', () => {
    expect(needsYou(base({ pendingApprovals: resolved(asked(), 'ix-1', 'approved') }), words)).toEqual([]);
    expect(needsYou(base({ isStreaming: false, pendingApprovals: asked() }), words)).toEqual([]);
  });

  it('an action whose outcome is unknown asks whether it happened', () => {
    const [item] = needsYou(base({ pendingApprovals: asked({ ...(vector('unknown_effect') as object), kind: 'unknown_effect' }) }), words);
    expect(item.ask.title).toMatch(/^Did this happen: .*\?$/);
    expect(ids(item.ask.actions)).toEqual(['happened', 'deny']);
  });

  it('a connect request names the item and opens where it is connected', () => {
    const v = vector('connect_requested') as Record<string, string>;
    const [item] = needsYou(base({ pendingApprovals: asked({ interactionId: v.interactionId, toolCallId: v.toolCallId, toolName: v.toolName, kind: 'connect', connect: { item: v.item, itemKind: 'toolset', connectPath: v.connectPath } }) }), words);
    expect(item.ask).toMatchObject({ kind: 'connect', title: 'Connect Jira to carry on', href: '/workspace/actions' });
    expect(ids(item.ask.actions)).toEqual(['connect', 'connected', 'skip']);
  });

  it('a question: options and free text; an expired one is not asked (silence is never a yes)', () => {
    const q: PendingAskUserQuestion = {
      assistantMessageId: 'a1',
      status: 'pending',
      answers: {},
      payload: { name: 'ask_user_question', questions: [{ uuid: 'q1', question: 'Which project?', options: [{ id: 'w', label: 'Web', isUserInput: false }, { id: 'o', label: 'Other', isUserInput: true }], multiSelect: false }] },
    };
    const [item] = needsYou(base({ isStreaming: false, pendingAskUserQuestion: q }), words);
    expect(item.ask).toMatchObject({ kind: 'question', title: 'Which project?' });
    expect(item.ask.questions?.[0].options).toEqual([{ id: 'w', label: 'Web' }, { id: 'o', label: 'Other', freeText: true }]);
    expect(needsYou(base({ pendingAskUserQuestion: { ...q, payload: { ...q.payload, expiresAt: '2026-11-09T11:00:00Z' } } }), words)).toEqual([]);
    expect(needsYou(base({ pendingAskUserQuestion: q, newestReplyId: 'a2' }), words)).toEqual([]);
  });

  it('an MCP sign-in from the reply, and an engine sign-in from the run\'s frames', () => {
    const mcp: MessagePart[] = [{ type: 'mcp_sign_in', servers: [{ instanceId: 'm1', serverName: 'Linear', scopes: [] }] }];
    const [signin] = needsYou(base({ isStreaming: false, newestParts: mcp }), words);
    expect(signin.ask).toMatchObject({ kind: 'signin', title: 'Sign in to Linear to carry on' });
    const engine: MessagePart[] = [{ type: 'engine_task', frames: [{ tool: 'code_task', taskId: 'r', kind: 'signin', text: '', data: { attemptId: 'att-1', state: 'requested' } } as never] }];
    const [eng] = needsYou(base({ streamingParts: engine }), words);
    expect(eng.ask).toMatchObject({ kind: 'signin', title: 'Sign in to Claude to carry on' });
  });

  it('no approval wording says "calls"; the action is plain words, the destination its own line only when it adds something', () => {
    const one = needsYou(base({ pendingApprovals: asked({ toolName: 'jira__create_issue', reason: { rule: 'writes', sources: [], moreSources: 0, conversationGrant: false, destination: 'Jira' } }) }), words)[0].ask;
    expect(one.title).toBe('Create issue in Jira?');
    expect(one.detail).toBeUndefined();
    const effect = needsYou(base({ pendingApprovals: asked({ toolName: 'slack__send_message', kind: 'unknown_effect' }) }), words)[0].ask;
    expect(effect.title).toBe('Did this happen: send message in Slack?');
    let three = asked({ interactionId: 'w1', toolCallId: 'w1', toolName: 'web_search', afterTurns: 3 });
    three = asked({ interactionId: 'w2', toolCallId: 'w2', toolName: 'web_search', afterTurns: 3 }, three);
    three = asked({ interactionId: 'w3', toolCallId: 'w3', toolName: 'web_search', afterTurns: 3 }, three);
    const web = needsYou(base({ pendingApprovals: three }), words)[0].ask;
    expect(web.title).toBe('Search the web 3 times?');
    for (const ask of [one, effect, web]) {
      const text = [ask.title, ask.reason, ask.detail, ...ask.actions.map((a) => a.label)].join(' ');
      expect(text).not.toMatch(/\bcalls?\b/i);
    }
  });

  it('someone the chat is shared with sees what waits, with nothing to press', () => {
    const [item] = needsYou(base({ canDecide: false, pendingApprovals: asked() }), words);
    expect(item.ask.actions).toEqual([]);
  });
});
