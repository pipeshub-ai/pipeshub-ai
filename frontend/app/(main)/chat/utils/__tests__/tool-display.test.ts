/**
 * `load_skill`/`skill_search`/`skills_list`/`load_skill_resource`/
 * `skill_manage` go over AG-UI as ordinary `TOOL_CALL_*` frames (see
 * "Skill activity UX" plan) — there is no protocol-level skill event, and
 * no DB migration backing this. These tests lock in the two label sources
 * that must both resolve to sensible copy:
 *
 *  - OLD chats: persisted `MessagePart`s with only `toolName` (no
 *    `displayName`) must still read as "Loaded skill", not "Load Skill".
 *  - NEW chats: a backend-provided `displayName` always wins outright.
 */
import { describe, it, expect } from 'vitest';
import { extractSqlQuery, extractToolsetLabel, isSkillTool, isWebTool, toolActivityLabel, toolStatusLabel, humanizeToolName } from '../tool-display';

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

describe('toolActivityLabel — old chat (toolName only, no displayName)', () => {
  it.each([
    ['skills_list', 'Listed skills'],
    ['skill_search', 'Searched skills'],
    ['load_skill', 'Loaded skill'],
    ['load_skill_resource', 'Loaded skill file'],
    ['skill_manage', 'Managed skill'],
  ])('labels %s as %s', (toolName, expected) => {
    expect(toolActivityLabel(toolName)).toBe(expected);
  });

  it('falls back to generic humanization for a non-skill tool, unaffected by the skill map', () => {
    expect(toolActivityLabel('run_code')).toBe('Run Code');
  });
});

describe('toolActivityLabel — new chat (backend-provided displayName)', () => {
  it('always prefers displayName over the skill label map', () => {
    expect(toolActivityLabel('load_skill', 'Loaded skill docx')).toBe('Loaded skill docx');
    expect(toolActivityLabel('skill_manage', 'Created skill docx')).toBe('Created skill docx');
  });
});

describe('toolStatusLabel — live streaming status (present tense)', () => {
  it.each([
    ['skills_list', 'Listing skills'],
    ['skill_search', 'Searching skills'],
    ['load_skill', 'Loading skill'],
    ['load_skill_resource', 'Loading skill file'],
    ['skill_manage', 'Managing skill'],
  ])('labels %s as %s', (toolName, expected) => {
    expect(toolStatusLabel(toolName)).toBe(expected);
  });

  it('falls back to "Using <Humanized Name>" for a non-skill tool', () => {
    expect(toolStatusLabel('run_code')).toBe('Using Run Code');
  });
});

describe('humanizeToolName — regression guard for the pre-map behavior', () => {
  it('title-cases a skill tool name the old way, confirming the map is what changed', () => {
    expect(humanizeToolName('load_skill')).toBe('Load Skill');
    expect(humanizeToolName('skill_search')).toBe('Skill Search');
  });
});

describe('extractToolsetLabel', () => {
  it('labels connector tools from their `{app}__` prefix', () => {
    expect(extractToolsetLabel('jira__search_issues')).toBe('Jira');
  });

  it('gives MCP tools a generic MCP chip', () => {
    expect(extractToolsetLabel('mcp_pangea_get_audit_logs')).toBe('MCP');
  });

  it('returns undefined for plain built-in tool names', () => {
    expect(extractToolsetLabel('run_code')).toBeUndefined();
    expect(extractToolsetLabel(undefined)).toBeUndefined();
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
