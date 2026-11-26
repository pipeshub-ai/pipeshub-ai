/**
 * UXINT-3: card words per rule, as the plan's table has them. Titles come from the tool's name and a verb, never
 * `server · tool_id`; the reason line is keyed by the rule code; a sub-agent's card names its engine.
 */
import { beforeAll, describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import { childAttribution, interactionTitle, interactionWhy } from '../interaction-wording';
import type { ApprovalReason, PendingApproval } from '../types';

const t = i18n.t.bind(i18n);
beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

const writes: ApprovalReason = { rule: 'writes', layer: 'org', sources: [], moreSources: 0, conversationGrant: false };
const flow = (sensitivity: 'unknown' | 'private', destination?: string): ApprovalReason => ({
  rule: 'flow_external_write',
  sensitivity,
  sources: [],
  moreSources: 0,
  conversationGrant: true,
  ...(destination ? { destination } : {}),
});
const call = (toolName: string, displayName?: string, reason?: ApprovalReason): Pick<PendingApproval, 'toolName' | 'displayName' | 'reason'> => ({
  toolName,
  ...(displayName ? { displayName } : {}),
  ...(reason ? { reason } : {}),
});

describe('titles (UXINT-3)', () => {
  it.each([
    ['org write, a connected app', call('mcp_e2esaas_create_ticket', 'E2Esaas · create_ticket', writes), 1, 'Create ticket in E2Esaas?'],
    ['image generation, one', call('image_generator__generate_image', 'Image Generator · generate_image', writes), 1, 'Generate an image?'],
    ['image generation, a batch of seven', call('image_generator__generate_image', 'Image Generator · generate_image', writes), 7, 'Generate 7 images?'],
    ['web search under the flow guard, three', call('web__web_search', 'Web · web_search', flow('unknown', 'duckduckgo.com')), 3, 'Search the web 3 times?'],
    ['page fetch under the flow guard', call('web__fetch_url', 'Web · fetch_url', flow('unknown', 'en.wikipedia.org')), 1, 'Read a web page?'],
    ['a send under the flow guard', call('slack__send_message', undefined, flow('private', '#sales')), 1, 'Send message to #sales in Slack?'],
    ['an engine child’s MCP call', call('mcp_e2esaas_create_ticket'), 1, 'Create ticket in e2esaas?'],
    ['a written name stays as written', call('slack__post_message', 'Post to Slack'), 1, 'Post to Slack?'],
    ['installing packages asks in its own verb (QAFIX2)', call('install_packages', 'Installed packages', writes), 1, 'Install packages?'],
    ['installing packages twice', call('install_packages', 'Installed packages', writes), 2, 'Install packages 2 times?'],
    ['a batch of another tool', call('jira__create_issue', undefined, writes), 2, 'Create issue (2 calls) in Jira?'],
    [
      // The approved WP-63 mock: the title names the repository; the card's body says public, read-only and the size.
      'GITF-18: a repository clone names the repository (WP-63)',
      call('repository_fetch', undefined, { ...writes, rule: 'tool', layer: 'engine', destination: 'github.com/onyx-dot-app/onyx (public, read-only, about 42 MB)' }),
      1,
      'Clone github.com/onyx-dot-app/onyx?',
    ],
    [
      'GITF-18: a clone named without its size keeps the repository whole',
      call('pipeshub__repository_fetch', undefined, { ...writes, rule: 'tool', layer: 'engine', destination: 'gitlab.com/acme/widgets' }),
      1,
      'Clone gitlab.com/acme/widgets?',
    ],
    [
      'GITF-18: a session-branch push names the branch and repository',
      call('pipeshub__repository_push', undefined, { ...writes, rule: 'tool', layer: 'engine', destination: 'pipeshub/session-s1 on github.com/acme/widgets' }),
      1,
      'Push to pipeshub/session-s1 on github.com/acme/widgets?',
    ],
  ])('%s', (_case, approval, count, title) => {
    expect(interactionTitle(t, approval, count)).toBe(title);
  });

  it('never shows the raw `server · tool_id`', () => {
    expect(interactionTitle(t, call('mcp_e2esaas_create_ticket', 'E2Esaas · create_ticket', writes))).not.toContain('·');
  });
});

describe('reason lines, keyed by the rule code (D3)', () => {
  it('a send under the flow guard says what leaves and why it asks', () => {
    const why = interactionWhy(t, call('web__web_search', 'Web · web_search', flow('unknown', 'duckduckgo.com')), 3) ?? '';
    expect(why).toBe(
      'These send words to duckduckgo.com. An earlier step used a result from a connected app or an outside tool whose origin cannot be checked, so we ask before anything leaves your organization.',
    );
    expect(interactionWhy(t, call('web__web_search', undefined, flow('private')), 1)).toContain('This sends words to a site outside your organization');
  });

  it('QAFIX-3: the card names neither a search nor a page address as what is asked for, for private and unknown reads, one call or a batch', () => {
    for (const seen of ['private', 'unknown'] as const) {
      for (const count of [1, 3]) {
        const why = interactionWhy(t, call('web__fetch_url', undefined, flow(seen, 'en.wikipedia.org')), count);
        expect(why).toContain('so we ask before anything leaves your organization.');
        expect(why).not.toMatch(/web search|page address/);
      }
    }
  });

  it('a write under the flow guard keeps its sentence; a layer’s rule says whose it is', () => {
    expect(interactionWhy(t, call('slack__send_message', undefined, flow('private', '#sales')))).toContain('read private content');
    expect(interactionWhy(t, call('jira__create_issue', undefined, writes))).toBe(
      'Your organization requires approval before an agent does this. Nothing happens until you decide.',
    );
    expect(interactionWhy(t, call('jira__create_issue', undefined, { ...writes, rule: 'quota' }))).toBeNull();
    expect(interactionWhy(t, call('jira__create_issue'))).toBeNull();
  });

  it('a destination that looks like a secret is masked in the title and the reason', () => {
    const secret = 'xoxb-1234567890-AbCdEfGhIjKl';
    const reason = flow('unknown', `#ops ${secret}`);
    expect(interactionTitle(t, call('slack__send_message', undefined, reason))).not.toContain(secret);
    expect(interactionWhy(t, call('web__web_search', undefined, reason))).not.toContain(secret);
  });
});

describe('who asks (UXINT-3)', () => {
  it('an engine child names its engine; a delegate its role; else the code task', () => {
    expect(childAttribution(t, { childEngine: 'codex', childRole: 'pipeshub-subagent' })).toBe('Codex wants to:');
    expect(childAttribution(t, { childEngine: 'claude_code' })).toBe('Claude Code wants to:');
    expect(childAttribution(t, { childEngine: 'newengine' })).toBe('Newengine wants to:');
    expect(childAttribution(t, { childRole: 'pipeshub-subagent-research' })).toBe('Sub-agent “research” wants to:');
    expect(childAttribution(t, { childRole: 'pipeshub-subagent' })).toBe('The code task wants to:');
  });
});
