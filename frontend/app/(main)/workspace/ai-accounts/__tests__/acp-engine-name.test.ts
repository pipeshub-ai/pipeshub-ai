/**
 * ACP-driver: an ACP agent run as a coding engine (`acp-<agent>`) is named by its release entry wherever the chat or
 * the pickers name an engine; the native engines keep their product names.
 */
import { beforeAll, describe, expect, it } from 'vitest';
import { i18n } from '@/lib/i18n';
import { RELEASE_ENGINE_NAMES } from '@/lib/contracts/harness/generated/core/policy-data';
import { executorLabel } from '@/app/(main)/chat/components/message-area/builtin-result-rows';
import { childAttribution } from '@/app/(main)/chat/interaction-wording';
import { engineName, isAcpEngine } from '../access-copy';

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});

describe('ACP engine names', () => {
  it('names an ACP engine by its release entry and a native one by its product name', () => {
    expect(RELEASE_ENGINE_NAMES['acp-opencode']).toBe('opencode (ACP)');
    expect(engineName('acp-opencode')).toBe('opencode (ACP)');
    expect(engineName('claude_code')).toBe('Claude Code');
    expect(engineName('acp-unknown')).toBe('acp-unknown');
  });

  it('tells an ACP engine from a native one', () => {
    expect(isAcpEngine('acp-opencode')).toBe(true);
    expect(['opencode', 'codex', 'claude_code', 'acp-', 'acp-UPPER', 'xacp-opencode'].some(isAcpEngine)).toBe(false);
  });

  it('the chat names an ACP engine that compacted or asks by the same name', () => {
    expect(executorLabel(i18n.t.bind(i18n), 'acp-opencode')).toBe('opencode (ACP)');
    expect(childAttribution(i18n.t.bind(i18n), { childEngine: 'acp-opencode' })).toContain('opencode (ACP)');
  });
});
