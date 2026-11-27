import { describe, expect, it } from 'vitest';

import cs from '../locales/cs-CZ.json';
import de from '../locales/de-DE.json';
import enIN from '../locales/en-IN.json';
import en from '../locales/en-US.json';
import es from '../locales/es-ES.json';
import hi from '../locales/hi-IN.json';
import ko from '../locales/ko-KR.json';
import zhCN from '../locales/zh-CN.json';
import zhSG from '../locales/zh-SG.json';
import zhTW from '../locales/zh-TW.json';

// A service agent answers each person with what that person can read, and its tools use
// that person's own connections. The strings that describe it must not say otherwise.
const SERVICE_AGENT_KEYS = [
  'serviceAgentBadgeTooltip',
  'convertToServiceAgentTooltip',
  'convertToServiceAgentTooltipNewAgent',
  'convertToServiceAgentTooltipNoOrg',
  'convertToServiceAgentTooltipNewAgentNoOrg',
  'serviceAgentDesc',
  'identitySearchesAs',
  'identityCreator',
  'identityTooltip',
  'identityDialogBody',
  'serviceAgentCreated',
  'serviceAgentConverted',
  'svcAcctConvertDesc',
  'svcAcctCreateDesc',
  'svcAcctConvertDescNoOrg',
  'svcAcctCreateDescNoOrg',
  'svcAcctKnowledgeNote1',
  'svcAcctKnowledgeNote2',
  'svcAcctToolsetsTitle',
  'svcAcctToolsetsNote1',
  'svcAcctAckKnowledge',
  'svcAcctAckToolsets',
] as const;

type Bundle = { agentBuilder: Record<string, unknown> & { apiToolsets: Record<string, unknown> } };
const locales: Record<string, Bundle> = {
  'en-US': en as unknown as Bundle,
  'en-IN': enIN as unknown as Bundle,
  'de-DE': de as unknown as Bundle,
  'es-ES': es as unknown as Bundle,
  'hi-IN': hi as unknown as Bundle,
  'ko-KR': ko as unknown as Bundle,
  'zh-CN': zhCN as unknown as Bundle,
  'zh-SG': zhSG as unknown as Bundle,
  'zh-TW': zhTW as unknown as Bundle,
  'cs-CZ': cs as unknown as Bundle,
};

describe('service agent wording', () => {
  const english = en.agentBuilder as Record<string, string>;

  it.each(SERVICE_AGENT_KEYS)('%s does not promise the creator\'s access or agent-held credentials', (key) => {
    const text = english[key];
    expect(text).toBeTruthy();
    expect(text).not.toMatch(/creator'?s access|searches as its creator|agent level|agent-level|key icon/i);
    expect(text).not.toMatch(/credentials (stored|kept) for this agent|its own (toolset )?credentials/i);
  });

  it('tells people that each person sees what they can read and connects their own actions', () => {
    expect(english.serviceAgentBadgeTooltip).toMatch(/each person sees only what they can read/i);
    expect(english.serviceAgentCreated).toMatch(/each person connects their own actions/i);
    expect(english.svcAcctToolsetsNote1).toMatch(/never yours/i);
  });

  it('says a missing action is reported in the answer, not asked for before the run', () => {
    // The run goes on without an unconnected action; the answer says so.
    expect(english.svcAcctToolsetsNote1).not.toMatch(/before the agent runs/i);
    expect(english.svcAcctToolsetsNote1).toMatch(/the answer tells them/i);
  });

  it('no longer asks anyone to remove toolsets or to set agent-level credentials', () => {
    for (const key of ['svcAcctRemoveToolsetsTitle', 'svcAcctRemoveToolsetsDesc', 'svcAcctToolsetsNote2Convert']) {
      expect(english[key]).toBeUndefined();
    }
    expect(english.apiToolsets && (english.apiToolsets as Record<string, unknown>).serviceAccountBlocked).toBeUndefined();
  });

  it.each(Object.keys(locales))('%s carries every rewritten key and none of the removed ones', (locale) => {
    const bundle = locales[locale]!.agentBuilder;
    for (const key of SERVICE_AGENT_KEYS) expect(typeof bundle[key]).toBe('string');
    expect(bundle.svcAcctRemoveToolsetsDesc).toBeUndefined();
    expect(bundle.apiToolsets.serviceAccountBlocked).toBeUndefined();
  });

  // Each locale's own words for the workspace and the Actions page come from its existing strings,
  // not from a list here: the page label is `workspace.sidebar.nav.actions`, and the word for the
  // workspace is whatever its existing "… → …" instructions already say.
  const existingArrowTerms = (bundle: Bundle & { workspace: { sidebar: { nav: { actions: string } } } }): Set<string> => {
    const terms = new Set<string>();
    const walk = (node: unknown, path: string): void => {
      if (typeof node === 'string') {
        if (path.startsWith('agentBuilder.') && SERVICE_AGENT_KEYS.includes(path.split('.')[1] as never)) return;
        for (const found of node.matchAll(/((?:\S+\s)?\S+)\s→/g)) {
          const words = found[1]!.split(/\s/);
          terms.add(words[words.length - 1]!);
          terms.add(words.join(' '));
        }
      } else if (node && typeof node === 'object') {
        for (const [key, value] of Object.entries(node)) walk(value, path ? `${path}.${key}` : key);
      }
    };
    walk(bundle, '');
    return terms;
  };

  // Variants that were found wrong in review, per locale. Add to this table, not to the test.
  const KNOWN_WRONG: Record<string, RegExp[]> = {
    'hi-IN': [/एक्शन/],
    'zh-TW': [/代理(?!程式)/, /連接/],
  };

  // The Agents nav label, matched without a Latin plural ending so that "Agent" fits "Agenten",
  // "Agente" fits "Agentes" and "Agent" fits "Agenti". Other scripts must use the label as is.
  const localAgentTerm = (navLabel: string): string =>
    navLabel.toLowerCase().replace(/(?<=\p{Script=Latin})(?:en|es|s|i)$/u, '');

  it.each(Object.keys(locales))('%s names the workspace, the Actions page and agents as its nav labels do', (locale) => {
    const bundle = locales[locale]! as Bundle & {
      nav: { agents: string };
      workspace: { sidebar: { nav: { actions: string } } };
    };
    const existing = existingArrowTerms(bundle);
    const actionsLabel = bundle.workspace.sidebar.nav.actions;
    let arrows = 0;
    for (const key of SERVICE_AGENT_KEYS) {
      const text = String(bundle.agentBuilder[key]);
      for (const found of text.matchAll(/((?:\S+\s)?\S+)\s→\s(\S+)/g)) {
        arrows += 1;
        const words = found[1]!.split(/\s/);
        const used = [words[words.length - 1]!, words.join(' ')];
        expect(used.some((term) => existing.has(term)), `${locale} ${key}: "${found[1]}" is not how this locale writes the workspace elsewhere`).toBe(true);
        expect(found[2]!.startsWith(actionsLabel), `${locale} ${key}: expected the Actions page label "${actionsLabel}" after the arrow`).toBe(true);
      }
    }
    expect(arrows).toBeGreaterThan(0);
    const agentTerm = localAgentTerm(bundle.nav.agents);
    for (const key of SERVICE_AGENT_KEYS) {
      if (!/agent/i.test(english[key]!)) continue;
      expect(String(bundle.agentBuilder[key]).toLowerCase(), `${locale} ${key}: expected this locale's Agents nav term "${bundle.nav.agents}"`).toContain(agentTerm);
    }
    const joined = SERVICE_AGENT_KEYS.map((key) => String(bundle.agentBuilder[key])).join('\n');
    for (const wrong of KNOWN_WRONG[locale] ?? []) expect(joined).not.toMatch(wrong);
  });
});
