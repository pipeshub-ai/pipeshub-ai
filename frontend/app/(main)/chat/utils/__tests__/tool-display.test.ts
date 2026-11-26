/**
 * How a step names its tool (TOOLNAMES): one registry (`TOOL_DISPLAY`) and the
 * rules in `contracts/harness/vectors/tool-display-names.json`, which the Node
 * projector and the Slack bot pass too. Old chats (parts with only `toolName`)
 * and new ones (the projector's `displayName`) read the same.
 */
import { readFileSync } from 'fs';
import path from 'path';
import { describe, it, expect } from 'vitest';
import { TOOL_DISPLAY } from '@/lib/contracts/harness/generated/ext/pipeshub/policy-data';
import {
  describeTool,
  extractSqlQuery,
  extractToolsetLabel,
  isSkillTool,
  isWebTool,
  toolActivityLabel,
  toolStatusLabel,
  humanizeToolName,
} from '../tool-display';

interface Case {
  id: string;
  in: { toolName: string; displayName?: string };
  out: { name: string; app: string | null; label: string; present: string; displayName: string };
}

const vectors = JSON.parse(
  readFileSync(path.resolve(__dirname, '../../../../../../contracts/harness/vectors/tool-display-names.json'), 'utf8'),
) as { cases: Case[] };

describe('describeTool — shared vectors with the Node projector', () => {
  it.each(vectors.cases.map((c) => [c.id, c] as const))('%s', (_id, c) => {
    const got = describeTool(c.in.toolName, c.in.displayName);
    expect({ name: got.name, app: got.app ?? null, label: got.label, present: got.present, displayName: got.displayName }).toEqual(c.out);
    const again = describeTool(c.in.toolName, got.displayName);
    expect({ name: again.name, app: again.app ?? null, label: again.label }).toEqual({ name: c.out.name, app: c.out.app, label: c.out.label });
  });

  it('never shows a raw id, a double underscore or a source prefix', () => {
    for (const c of vectors.cases) {
      for (const shown of [toolActivityLabel(c.in.toolName, c.in.displayName), toolStatusLabel(c.in.toolName, c.in.displayName), extractToolsetLabel(c.in.toolName, c.in.displayName) ?? '']) {
        expect(shown).not.toMatch(/__|@/);
      }
    }
  });
});

describe('built-in action labels in i18n', () => {
  it('en-US says what the registry says, so the English fallback and the translation never drift', () => {
    const en = JSON.parse(readFileSync(path.resolve(__dirname, '../../../../../lib/i18n/locales/en-US.json'), 'utf8')) as {
      toolNames: Record<string, unknown>;
    };
    const { using, ...builtins } = en.toolNames;
    const fromRegistry = Object.fromEntries(TOOL_DISPLAY.builtins.map((b) => [b.key, { present: b.present, past: b.past }]));
    expect(builtins).toEqual(fromRegistry);
    expect(using).toBe('Using {{tool}}');
  });
});

describe('humanizeToolName', () => {
  it.each([
    ['dynamic__web_search', 'Web search'],
    ['image_generator__generate_image', 'Generate image'],
    ['pipeshub@sandbox/run_code', 'Run code'],
    ['load_skill', 'Load skill'],
    ['confluence__get_page_by_id', 'Get page by ID'],
  ])('%s → %s', (name, expected) => {
    expect(humanizeToolName(name)).toBe(expected);
  });
});

describe('isSkillTool', () => {
  it('is true for exactly the five built-in skill tools', () => {
    expect(isSkillTool('skills_list')).toBe(true);
    expect(isSkillTool('skill_search')).toBe(true);
    expect(isSkillTool('load_skill')).toBe(true);
    expect(isSkillTool('load_skill_resource')).toBe(true);
    expect(isSkillTool('skill_manage')).toBe(true);
  });

  it('is false for unrelated tools, including ones that also contain "skill"', () => {
    expect(isSkillTool('web_search')).toBe(false);
    expect(isSkillTool('run_code')).toBe(false);
    expect(isSkillTool(undefined)).toBe(false);
  });

  it('does not treat Object.prototype names as skill tools', () => {
    expect(isSkillTool('constructor')).toBe(false);
    expect(isSkillTool('toString')).toBe(false);
    expect(isSkillTool('valueOf')).toBe(false);
  });
});

describe('toolActivityLabel', () => {
  it.each([
    ['skills_list', 'Listed skills'],
    ['skill_search', 'Searched skills'],
    ['load_skill', 'Loaded skill'],
    ['load_skill_resource', 'Loaded skill file'],
    ['skill_manage', 'Managed skill'],
    ['run_code', 'Ran code'],
    ['dynamic__web_search', 'Searched the web'],
  ])('labels an old chat’s %s as %s', (toolName, expected) => {
    expect(toolActivityLabel(toolName)).toBe(expected);
  });

  it('a built-in reads its own (translated) action, whatever English the server sent', () => {
    expect(toolActivityLabel('load_skill', 'Loaded skill')).toBe('Loaded skill');
  });

  it('a tool’s written display name is kept as written', () => {
    expect(toolActivityLabel('retrieval__search_internal_knowledge', 'Searched the knowledge base')).toBe('Searched the knowledge base');
  });

  it('an MCP call names its tool; the server is the chip', () => {
    expect(toolActivityLabel('mcp_e2esaas_create_ticket', 'E2Esaas · create_ticket')).toBe('Create ticket');
    expect(extractToolsetLabel('mcp_e2esaas_create_ticket', 'E2Esaas · create_ticket')).toBe('E2Esaas');
  });
});

describe('toolStatusLabel — live streaming status (present tense)', () => {
  it.each([
    ['skills_list', 'Listing skills'],
    ['skill_search', 'Searching skills'],
    ['load_skill', 'Loading skill'],
    ['load_skill_resource', 'Loading skill file'],
    ['skill_manage', 'Managing skill'],
    ['run_code', 'Running code'],
    ['jira__create_issue', 'Using Jira · Create issue'],
  ])('labels %s as %s', (toolName, expected) => {
    expect(toolStatusLabel(toolName)).toBe(expected);
  });
});

describe('extractToolsetLabel', () => {
  it('labels connector tools from their `{app}__` prefix, in the registry’s spelling', () => {
    expect(extractToolsetLabel('jira__search_issues')).toBe('Jira');
    expect(extractToolsetLabel('github__list_pull_requests')).toBe('GitHub');
    expect(extractToolsetLabel('image_generator__generate_image')).toBe('Image Generator');
  });

  it('names an MCP tool saved without a display name by its server id', () => {
    expect(extractToolsetLabel('mcp_pangea_get_audit_logs')).toBe('Pangea');
  });

  it('returns undefined for plain built-in tool names and plumbing prefixes', () => {
    expect(extractToolsetLabel('run_code')).toBeUndefined();
    expect(extractToolsetLabel(undefined)).toBeUndefined();
    expect(extractToolsetLabel('internaltools__ask_user_question')).toBeUndefined();
  });

  it('gives the built-in web tools no toolset badge', () => {
    expect(extractToolsetLabel('dynamic__web_search')).toBeUndefined();
    expect(extractToolsetLabel('dynamic__fetch_url')).toBeUndefined();
  });
});

describe('isWebTool', () => {
  it('knows the web tools, with or without their prefix', () => {
    expect(isWebTool('dynamic__web_search')).toBe(true);
    expect(isWebTool('fetch_url')).toBe(true);
    expect(isWebTool('dynamic__fetch_url')).toBe(true);
  });

  it('is false for anything else', () => {
    expect(isWebTool('mcp_web_search')).toBe(false);
    expect(isWebTool('knowledgegraph__search')).toBe(false);
    expect(isWebTool(undefined)).toBe(false);
  });
});

describe('extractSqlQuery', () => {
  const args = (query: string) => JSON.stringify({ query, source_name: 'PostgreSQL', connector_id: 'conn-1' });

  it('returns the query of a SQL tool call', () => {
    expect(extractSqlQuery('sql__execute_sql_query', args('SELECT 1'))).toEqual({ text: 'SELECT 1', cutOff: false });
    expect(extractSqlQuery('execute_sql_query', args('SELECT 1'))).toEqual({ text: 'SELECT 1', cutOff: false });
  });

  it('ignores other tools, even with a "query" argument', () => {
    expect(extractSqlQuery('web_search', JSON.stringify({ query: 'weather' }))).toBeUndefined();
    expect(extractSqlQuery(undefined, args('SELECT 1'))).toBeUndefined();
  });

  it('returns undefined for missing, blank or non-string queries', () => {
    expect(extractSqlQuery('sql__execute_sql_query', undefined)).toBeUndefined();
    expect(extractSqlQuery('sql__execute_sql_query', args('  '))).toBeUndefined();
    expect(extractSqlQuery('sql__execute_sql_query', JSON.stringify({ query: 3 }))).toBeUndefined();
    expect(extractSqlQuery('sql__execute_sql_query', 'null')).toBeUndefined();
  });

  it('marks a query that args cut off by the length cap leave unfinished', () => {
    const full = args('SELECT id FROM users WHERE active = true AND secret = false');
    expect(extractSqlQuery('sql__execute_sql_query', full.slice(0, full.indexOf(' AND')))).toEqual({
      text: 'SELECT id FROM users WHERE active = true',
      cutOff: true,
    });
    const cutInEscape = JSON.stringify({ query: 'SELECT \'é\'' }).replace('é', '\\u00e9').slice(0, -6);
    expect(extractSqlQuery('sql__execute_sql_query', cutInEscape)).toEqual({ text: "SELECT '", cutOff: true });
    expect(extractSqlQuery('sql__execute_sql_query', '{"query": "SELECT \\')).toEqual({ text: 'SELECT ', cutOff: true });
  });

  it('keeps a query whole when the cut falls in an argument after it', () => {
    const full = args('SELECT "name",\n  id FROM users');
    expect(extractSqlQuery('sql__execute_sql_query', full.slice(0, full.indexOf('Postgre')))).toEqual({
      text: 'SELECT "name",\n  id FROM users',
      cutOff: false,
    });
  });

  it("flags a query the server shortened, without the server's note", () => {
    expect(extractSqlQuery('sql__execute_sql_query', args('SELECT id -- note… [truncated, 1200 of 5000 chars]'))).toEqual({
      text: 'SELECT id -- note',
      cutOff: true,
    });
  });
});
