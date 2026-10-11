import { describe, expect, it } from 'vitest';
import {
  deletesByName,
  ownRulesToSave,
  policyToSave,
  ruleToolFromName,
  ruleToolsFromInfo,
  startingRule,
  toolRuleRows,
} from '../tool-rules';

describe('tool rules', () => {
  it('takes the kind the backend worked out, and the title the server sent', () => {
    expect(
      ruleToolsFromInfo([
        { name: 'search', description: 'Find issues', annotations: { title: 'Search issues' }, kind: 'read', kindSource: 'server' },
        { name: 'close_issue', annotations: null, kind: 'write', kindSource: 'none' },
        { name: 'create_issue', kind: 'nonsense' as never },
      ])
    ).toEqual([
      { name: 'search', title: 'Search issues', description: 'Find issues', kind: 'read', kindSource: 'server' },
      { name: 'close_issue', kind: 'write', kindSource: 'none' },
      // An answer without a kind (an older backend): unknown, not "changes data".
      { name: 'create_issue' },
    ]);
  });

  // The same names as `backend/python/tests/unit/agents/mcp/test_tool_kind.py`.
  it.each([
    'delete_issue', 'deleteJiraIssue', 'issues.delete', 'DROP-TABLE', 'removeLabel', 'purge_cache', 'erase', 'wipeDevice',
    'truncate_table', 'revokeToken', 'uninstall_app', 'reset_password', 'bulk_deletion', 'issue removal',
    'deletedItemsRestore', 'remove_user_from_group',
  ])('knows %s deletes from its name', (name) => {
    expect(deletesByName(name)).toBe(true);
  });

  it.each([
    'get_dropdown_options', 'list_presets', 'update_address', 'createIssue', 'search', 'eraser_tool', 'dropbox_upload',
    'removable_media_info', 'resettlement_report',
  ])('knows %s does not delete from its name', (name) => {
    expect(deletesByName(name)).toBe(false);
  });

  it('gives a tool known only by name a kind when the name deletes', () => {
    expect(ruleToolFromName('delete_page', 'Deletes a page')).toEqual({
      name: 'delete_page',
      description: 'Deletes a page',
      kind: 'destructive',
      kindSource: 'name',
    });
    expect(ruleToolFromName('archive_page')).toEqual({ name: 'archive_page' });
  });

  it('starts read-only tools Pre-approved, tools that change data on approval and deleting tools Denied, like the server', () => {
    expect([startingRule('read'), startingRule('write'), startingRule('destructive'), startingRule(undefined)]).toEqual([
      'allow',
      'ask',
      'block',
      'ask',
    ]);
  });

  it("lists the server's tools, then other tools that have a rule", () => {
    const rows = toolRuleRows([{ name: 'b' }, { name: 'a' }], ['zeta', 'a', 'gone', 'gone', 'drop_table']);
    expect(rows).toEqual([
      { name: 'b' },
      { name: 'a' },
      { name: 'drop_table', kind: 'destructive', kindSource: 'name', offered: false },
      { name: 'gone', offered: false },
      { name: 'zeta', offered: false },
    ]);
  });

  it('saves only the rules that differ from where a tool starts', () => {
    const rows = toolRuleRows(
      [
        { name: 'search', kind: 'read' as const },
        { name: 'create_issue', kind: 'write' as const },
        { name: 'update_issue', kind: 'write' as const },
        { name: 'delete_issue', kind: 'destructive' as const },
        { name: 'purge_issue', kind: 'destructive' as const },
      ],
      ['old_tool', 'drop_old']
    );
    const saved = ownRulesToSave(rows, {
      search: 'allow',
      create_issue: 'allow',
      update_issue: 'ask',
      delete_issue: 'block',
      purge_issue: 'ask',
      old_tool: 'ask',
      drop_old: 'block',
    });
    expect(saved).toEqual({ tools: { create_issue: 'allow', purge_issue: 'ask', old_tool: 'ask' } });
  });

  it('keeps any rule of a tool whose kind is not known', () => {
    const saved = ownRulesToSave([{ name: 'search' }, { name: 'create_issue', kind: 'write' }], {
      search: 'ask',
      create_issue: 'ask',
    });
    expect(saved).toEqual({ tools: { search: 'ask' } });
  });

  it('drops company entries that say nothing, and unattended on a denied tool', () => {
    expect(
      policyToSave({
        a: { rule: 'block', unattended: true },
        b: { rule: null, unattended: true },
        c: { rule: 'ask' },
        d: { rule: null, unattended: false },
        e: {},
      })
    ).toEqual({
      tools: { a: { rule: 'block', unattended: false }, b: { rule: null, unattended: true }, c: { rule: 'ask', unattended: false } },
    });
  });
});
