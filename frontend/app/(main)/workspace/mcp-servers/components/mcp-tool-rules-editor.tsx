'use client';

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, SegmentedControl, Text, TextField, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { toast } from '@/lib/store/toast-store';
import { isProcessedError } from '@/lib/api';
import { McpServersApi } from '../api';
import { ownRulesToSave, startingRule, toolDisplayName, toolRuleRows, type McpRuleTool } from '../tool-rules';
import type { McpToolKind, McpToolRule } from '../types';

const RULES: McpToolRule[] = ['allow', 'ask', 'block'];
type GroupId = McpToolKind | 'notOffered';
const GROUPS: Array<{ id: GroupId; hint?: string }> = [
  { id: 'destructive', hint: 'workspace.mcpServers.toolRules.groupHint.destructive' },
  { id: 'write' },
  { id: 'read', hint: 'workspace.mcpServers.toolRules.groupHint.read' },
  { id: 'notOffered' },
];
// Fewer tools than this are easier to scan than to search.
const SEARCH_FROM = 10;

function errorDetail(error: unknown): string | undefined {
  return isProcessedError(error) ? error.message : undefined;
}

function groupOf(row: McpRuleTool): GroupId {
  if (row.offered === false) return 'notOffered';
  return row.kind ?? 'write';
}

export interface McpToolRulesEditor {
  loading: boolean;
  loadError: string | null;
  saving: boolean;
  rows: McpRuleTool[];
  /** The rule as shown: the person's saved one, else where the tool starts. */
  rule: (row: McpRuleTool) => McpToolRule;
  setRule: (name: string, rule: McpToolRule) => void;
  /** Tools whose rule would change on save. */
  changes: number;
  discard: () => void;
  /** Saves and reports success; a failure is toasted and the changes are kept. */
  save: () => Promise<boolean>;
}

/** Loads, edits and saves the caller's own rules for one server. `tools` is what it offers now. */
export function useToolRulesEditor(instanceId: string, tools: McpRuleTool[], enabled = true): McpToolRulesEditor {
  const { t } = useTranslation();
  const [savedRules, setSavedRules] = useState<Record<string, McpToolRule>>({});
  const [rules, setRules] = useState<Record<string, McpToolRule>>({});
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!enabled || !instanceId) return;
    let cancelled = false;
    setSavedRules({});
    setRules({});
    setLoadError(null);
    setLoading(true);
    McpServersApi.getMyToolRules(instanceId)
      .then((saved) => {
        if (cancelled) return;
        setSavedRules(saved.tools ?? {});
        setRules(saved.tools ?? {});
      })
      .catch((error) => {
        if (!cancelled) setLoadError(errorDetail(error) ?? t('workspace.mcpServers.toolRules.loadError'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, instanceId]);

  const rows = useMemo(() => toolRuleRows(tools, Object.keys(rules)), [tools, rules]);

  const changes = useMemo(() => {
    const before = ownRulesToSave(rows, savedRules).tools;
    const after = ownRulesToSave(rows, rules).tools;
    return [...new Set([...Object.keys(before), ...Object.keys(after)])].filter((name) => before[name] !== after[name]).length;
  }, [rows, savedRules, rules]);

  const save = async (): Promise<boolean> => {
    setSaving(true);
    try {
      await McpServersApi.updateMyToolRules(instanceId, ownRulesToSave(rows, rules));
      setSavedRules(rules);
      toast.success(t('workspace.mcpServers.toolRules.saved'));
      return true;
    } catch (error) {
      const detail = errorDetail(error);
      toast.error(t('workspace.mcpServers.toolRules.saveError'), detail ? { description: detail } : undefined);
      return false;
    } finally {
      setSaving(false);
    }
  };

  return {
    loading,
    loadError,
    saving,
    rows,
    rule: (row) => rules[row.name] ?? startingRule(row.kind),
    setRule: (name, rule) => setRules((prev) => ({ ...prev, [name]: rule })),
    changes,
    discard: () => setRules(savedRules),
    save,
  };
}

function useSearch(rows: McpRuleTool[]) {
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  const shown = q
    ? rows.filter((row) => row.name.toLowerCase().includes(q) || (row.title ?? '').toLowerCase().includes(q))
    : rows;
  return { query, setQuery, shown, searchable: rows.length > SEARCH_FROM };
}

function SearchField({ query, onChange }: { query: string; onChange: (value: string) => void }) {
  const { t } = useTranslation();
  return (
    <TextField.Root size="2" placeholder={t('workspace.mcpServers.toolRules.search')} value={query} onChange={(e) => onChange(e.target.value)}>
      <TextField.Slot>
        <MaterialIcon name="search" size={16} color="var(--gray-9)" />
      </TextField.Slot>
    </TextField.Root>
  );
}

function NoMatch({ onClear }: { onClear: () => void }) {
  const { t } = useTranslation();
  return (
    <Flex direction="column" align="center" gap="2" style={{ padding: 'var(--space-4)' }}>
      <Text size="2" style={{ color: 'var(--gray-10)' }}>
        {t('workspace.mcpServers.toolRules.noMatch')}
      </Text>
      <Button type="button" size="1" variant="ghost" onClick={onClear}>
        {t('workspace.mcpServers.toolRules.clearSearch')}
      </Button>
    </Flex>
  );
}

/** Tools in groups by what they do to data, deleting ones first. */
function Groups({
  rows,
  toolsUnknown,
  maxListHeight,
  renderRow,
}: {
  rows: McpRuleTool[];
  toolsUnknown: boolean;
  maxListHeight?: string;
  renderRow: (row: McpRuleTool) => ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <Flex direction="column" gap="4" style={maxListHeight ? { maxHeight: maxListHeight, overflowY: 'auto' } : undefined}>
      {GROUPS.map((group) => {
        const members = rows.filter((row) => groupOf(row) === group.id);
        if (members.length === 0) return null;
        return (
          <Flex key={group.id} direction="column" gap="1" data-testid={`tool-group-${group.id}`}>
            <Flex align="baseline" gap="2" mb="1">
              <Text size="1" weight="medium" style={{ color: 'var(--gray-12)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                {t(
                  toolsUnknown && group.id === 'notOffered'
                    ? 'workspace.mcpServers.toolRules.group.byName'
                    : `workspace.mcpServers.toolRules.group.${group.id}`
                )}{' '}
                · {members.length}
              </Text>
              {group.hint && (
                <Text size="1" style={{ color: 'var(--gray-10)' }}>
                  {t(group.hint)}
                </Text>
              )}
            </Flex>
            {members.map(renderRow)}
          </Flex>
        );
      })}
    </Flex>
  );
}

/** A tool's name, kind and description. Where the kind came from is a tooltip, not more text. */
/** A tool's row: its name (the server's id on hover), and how sure the group it sits in is. The
 * group heading says what it does to data; a row only adds why, when the server didn't say. */
function ToolSummary({ row }: { row: McpRuleTool }) {
  const { t } = useTranslation();
  const guessed = row.offered !== false && row.kind && row.kindSource && row.kindSource !== 'server';
  return (
    <Box style={{ minWidth: 0, flex: '1 1 12rem' }}>
      <Flex align="center" gap="1">
        <Text size="2" weight="medium" title={row.name} style={{ color: 'var(--gray-12)', overflowWrap: 'anywhere' }}>
          {toolDisplayName(row.name, row.title)}
        </Text>
        {guessed && (
          <Tooltip content={t(`workspace.mcpServers.toolRules.kindSource.${row.kindSource}`)}>
            <span role="img" aria-label={t(`workspace.mcpServers.toolRules.kindSource.${row.kindSource}`)} style={{ display: 'inline-flex' }}>
              <MaterialIcon name="info" size={14} color="var(--gray-9)" />
            </span>
          </Tooltip>
        )}
      </Flex>
      {row.description && (
        <Text
          as="p"
          size="1"
          style={{
            color: 'var(--gray-11)',
            overflow: 'hidden',
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflowWrap: 'anywhere',
          }}
        >
          {row.description}
        </Text>
      )}
    </Box>
  );
}

const ROW_STYLE = { padding: 'var(--space-2) var(--space-3)', borderRadius: 'var(--radius-2)', backgroundColor: 'var(--gray-a2)' };

interface McpToolRulesListProps {
  editor: McpToolRulesEditor;
  /** Height the list may take before it scrolls; the panel lets its own body scroll instead. */
  maxListHeight?: string;
  /** The server's list isn't known (not signed in, or it failed): rows are rules set by name. */
  toolsUnknown?: boolean;
}

/** The person's own rules, one per tool. */
export function McpToolRulesList({ editor, maxListHeight, toolsUnknown = false }: McpToolRulesListProps) {
  const { t } = useTranslation();
  const { query, setQuery, shown, searchable } = useSearch(editor.rows);

  if (editor.loading) {
    return (
      <Text as="p" size="2" style={{ color: 'var(--slate-10)' }}>
        {t('workspace.mcpServers.toolRules.loading')}
      </Text>
    );
  }
  if (editor.loadError) {
    return (
      <Text as="p" size="2" role="alert" style={{ color: 'var(--red-11)' }}>
        {editor.loadError}
      </Text>
    );
  }
  if (editor.rows.length === 0) {
    return (
      <Text size="2" style={{ color: 'var(--slate-10)' }}>
        {t('workspace.mcpServers.toolRules.empty')}
      </Text>
    );
  }

  return (
    <Flex direction="column" gap="3">
      {searchable && <SearchField query={query} onChange={setQuery} />}
      {shown.length === 0 ? (
        <NoMatch onClear={() => setQuery('')} />
      ) : (
        <Groups
          rows={shown}
          toolsUnknown={toolsUnknown}
          maxListHeight={maxListHeight}
          renderRow={(row) => (
            <Flex key={row.name} align="center" justify="between" gap="3" wrap="wrap" style={ROW_STYLE}>
              <ToolSummary row={row} />
              <SegmentedControl.Root
                size="1"
                aria-label={row.name}
                // Equal segments whatever is chosen, so the controls line up down the list; on a
                // narrow screen they shrink and their labels wrap rather than push the page wide.
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
                  flex: '0 1 26rem',
                  minWidth: 0,
                  height: 'auto',
                  minHeight: 'var(--space-5)',
                }}
                value={editor.rule(row)}
                onValueChange={(value) => !editor.saving && editor.setRule(row.name, value as McpToolRule)}
              >
                {RULES.map((rule) => (
                  <SegmentedControl.Item key={rule} value={rule} style={{ whiteSpace: 'normal', textAlign: 'center', lineHeight: 1.2 }}>
                    {t(`workspace.mcpServers.toolRules.rule.${rule}`)}
                  </SegmentedControl.Item>
                ))}
              </SegmentedControl.Root>
            </Flex>
          )}
        />
      )}
    </Flex>
  );
}

/** What a server offers, without rules: an organization server's tools, as an administrator sees them. */
export function McpToolList({ tools, maxListHeight }: { tools: McpRuleTool[]; maxListHeight?: string }) {
  const { t } = useTranslation();
  const { query, setQuery, shown, searchable } = useSearch(tools);
  if (tools.length === 0) {
    return (
      <Text size="2" style={{ color: 'var(--slate-10)' }}>
        {t('workspace.mcpServers.toolRules.noTools')}
      </Text>
    );
  }
  return (
    <Flex direction="column" gap="3" data-testid="mcp-tool-list">
      {searchable && <SearchField query={query} onChange={setQuery} />}
      {shown.length === 0 ? (
        <NoMatch onClear={() => setQuery('')} />
      ) : (
        <Groups
          rows={shown}
          toolsUnknown={false}
          maxListHeight={maxListHeight}
          renderRow={(row) => (
            <Flex key={row.name} style={ROW_STYLE}>
              <ToolSummary row={row} />
            </Flex>
          )}
        />
      )}
    </Flex>
  );
}
