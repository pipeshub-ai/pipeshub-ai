'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Box, Button, Flex, SegmentedControl, Switch, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { toast } from '@/lib/store/toast-store';
import { isProcessedError } from '@/lib/api';
import { McpServersApi } from '../api';
import { ownRulesToSave, policyToSave, startingRule, toolRuleRows, type McpRuleTool } from '../tool-rules';
import type { McpCompanyToolRule, McpToolKind, McpToolRule } from '../types';

/** Whose rules are edited. */
export type McpToolRulesTarget =
  | { kind: 'personal'; instanceId: string }
  | { kind: 'company'; instanceId: string }
  | { kind: 'agent'; agentKey: string; instanceId: string };

type CompanyChoice = 'none' | 'ask' | 'block';

const MAX_TOOL_NAME_CHARS = 200;
const OWN_RULES: McpToolRule[] = ['allow', 'ask', 'block'];
const COMPANY_RULES: CompanyChoice[] = ['none', 'ask', 'block'];
const GROUPS: Array<{ id: McpToolKind | 'notOffered'; hint?: string }> = [
  { id: 'destructive', hint: 'workspace.mcpServers.toolRules.groupHint.destructive' },
  { id: 'write' },
  { id: 'read', hint: 'workspace.mcpServers.toolRules.groupHint.read' },
  { id: 'notOffered' },
];
const FILTERS = ['all', 'destructive', 'write', 'read', 'hasRule'] as const;
type Filter = (typeof FILTERS)[number];

function errorDetail(error: unknown): string | undefined {
  return isProcessedError(error) ? error.message : undefined;
}

function groupOf(row: McpRuleTool): McpToolKind | 'notOffered' {
  if (row.offered === false) return 'notOffered';
  return row.kind ?? 'write';
}

function sameCompanyEntry(a?: McpCompanyToolRule, b?: McpCompanyToolRule): boolean {
  return (a?.rule ?? null) === (b?.rule ?? null) && Boolean(a?.unattended) === Boolean(b?.unattended);
}

export interface McpToolRulesEditor {
  target: McpToolRulesTarget;
  isCompany: boolean;
  loading: boolean;
  loadError: string | null;
  saving: boolean;
  rows: McpRuleTool[];
  /** A person's or agent's rule as shown: the saved one, else where the tool starts. */
  ownRule: (row: McpRuleTool) => McpToolRule;
  companyEntry: (name: string) => McpCompanyToolRule;
  hasRule: (name: string) => boolean;
  setOwnRule: (name: string, rule: McpToolRule) => void;
  setCompanyRule: (name: string, rule: CompanyChoice) => void;
  setUnattended: (name: string, on: boolean) => void;
  addTool: (name: string) => boolean;
  /** Tools whose rule would change on save. */
  changes: number;
  discard: () => void;
  /** Saves and reports success; a failure is toasted and the changes are kept. */
  save: () => Promise<boolean>;
  /** Company only: tools that change or delete data and have no company rule yet. */
  quickSetupNames: string[];
  applyQuickSetup: () => void;
}

/** Loads, edits and saves one target's rules. `tools` is what the server offers now. */
export function useToolRulesEditor(target: McpToolRulesTarget, tools: McpRuleTool[], enabled = true): McpToolRulesEditor {
  const { t } = useTranslation();
  const isCompany = target.kind === 'company';
  const agentKey = target.kind === 'agent' ? target.agentKey : null;
  const [savedRules, setSavedRules] = useState<Record<string, McpToolRule>>({});
  const [rules, setRules] = useState<Record<string, McpToolRule>>({});
  const [savedPolicy, setSavedPolicy] = useState<Record<string, McpCompanyToolRule>>({});
  const [policy, setPolicy] = useState<Record<string, McpCompanyToolRule>>({});
  const [added, setAdded] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setSavedRules({});
    setRules({});
    setSavedPolicy({});
    setPolicy({});
    setAdded([]);
    setLoadError(null);
    setLoading(true);
    const load = async () => {
      if (target.kind === 'company') {
        const saved = (await McpServersApi.getToolPolicy(target.instanceId)).tools ?? {};
        if (!cancelled) {
          setSavedPolicy(saved);
          setPolicy(saved);
        }
      } else {
        const saved =
          (target.kind === 'agent'
            ? await McpServersApi.getAgentToolRules(target.agentKey, target.instanceId)
            : await McpServersApi.getMyToolRules(target.instanceId)
          ).tools ?? {};
        if (!cancelled) {
          setSavedRules(saved);
          setRules(saved);
        }
      }
    };
    load()
      .catch((error) => {
        if (!cancelled) setLoadError(errorDetail(error) ?? t('workspace.mcpServers.toolRules.loadError'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, target.kind, target.instanceId, agentKey]);

  const rows = useMemo(
    () => toolRuleRows(tools, [...Object.keys(isCompany ? policy : rules), ...added]),
    [tools, isCompany, policy, rules, added]
  );

  const changes = useMemo(() => {
    if (isCompany) {
      const before = policyToSave(savedPolicy).tools;
      const after = policyToSave(policy).tools;
      return [...new Set([...Object.keys(before), ...Object.keys(after)])].filter(
        (name) => !sameCompanyEntry(before[name], after[name])
      ).length;
    }
    const before = ownRulesToSave(rows, savedRules).tools;
    const after = ownRulesToSave(rows, rules).tools;
    return [...new Set([...Object.keys(before), ...Object.keys(after)])].filter((name) => before[name] !== after[name])
      .length;
  }, [isCompany, savedPolicy, policy, rows, savedRules, rules]);

  const companyEntry = useCallback((name: string) => policy[name] ?? {}, [policy]);
  const quickSetupNames = useMemo(
    () => (isCompany ? rows.filter((row) => row.offered !== false && row.kind !== 'read' && !policy[row.name]?.rule).map((row) => row.name) : []),
    [isCompany, rows, policy]
  );

  const save = async (): Promise<boolean> => {
    setSaving(true);
    try {
      if (target.kind === 'company') {
        const toSave = policyToSave(policy);
        await McpServersApi.updateToolPolicy(target.instanceId, toSave);
        setSavedPolicy(policy);
      } else {
        const toSave = ownRulesToSave(rows, rules);
        if (target.kind === 'agent') {
          await McpServersApi.updateAgentToolRules(target.agentKey, target.instanceId, toSave);
        } else {
          await McpServersApi.updateMyToolRules(target.instanceId, toSave);
        }
        setSavedRules(rules);
      }
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
    target,
    isCompany,
    loading,
    loadError,
    saving,
    rows,
    ownRule: (row) => rules[row.name] ?? startingRule(row.kind),
    companyEntry,
    hasRule: (name) => (isCompany ? Boolean(policy[name]?.rule || policy[name]?.unattended) : name in rules),
    setOwnRule: (name, rule) => setRules((prev) => ({ ...prev, [name]: rule })),
    setCompanyRule: (name, rule) =>
      setPolicy((prev) => ({ ...prev, [name]: { ...prev[name], rule: rule === 'none' ? null : rule } })),
    setUnattended: (name, on) => setPolicy((prev) => ({ ...prev, [name]: { ...prev[name], unattended: on } })),
    addTool: (name) => {
      const candidate = name.trim();
      if (!candidate || candidate.length > MAX_TOOL_NAME_CHARS || rows.some((row) => row.name === candidate)) return false;
      setAdded((prev) => [...prev, candidate]);
      return true;
    },
    changes,
    discard: () => {
      setRules(savedRules);
      setPolicy(savedPolicy);
      setAdded([]);
    },
    save,
    quickSetupNames,
    applyQuickSetup: () =>
      setPolicy((prev) => {
        const next = { ...prev };
        for (const name of quickSetupNames) next[name] = { ...next[name], rule: 'ask' };
        return next;
      }),
  };
}

const KIND_COLORS: Record<McpToolKind, 'red' | 'blue' | 'green'> = { destructive: 'red', write: 'blue', read: 'green' };

interface McpToolRulesListProps {
  editor: McpToolRulesEditor;
  /** Shown but not changeable. */
  readOnly?: boolean;
  /** Height the list may take before it scrolls; the panel lets its own body scroll instead. */
  maxListHeight?: string;
  /** The server's list isn't known (not signed in, or it failed): rows are rules set by name. */
  toolsUnknown?: boolean;
}

/** The rules, grouped by what each tool does to data, with search, filters and add-by-name. */
export function McpToolRulesList({ editor, readOnly = false, maxListHeight, toolsUnknown = false }: McpToolRulesListProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [newName, setNewName] = useState('');
  const { rows, isCompany, saving } = editor;

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

  const q = query.trim().toLowerCase();
  const shown = rows.filter((row) => {
    if (q && !row.name.toLowerCase().includes(q) && !(row.title ?? '').toLowerCase().includes(q)) return false;
    if (filter === 'hasRule') return editor.hasRule(row.name);
    if (filter !== 'all') return row.offered !== false && (row.kind ?? 'write') === filter;
    return true;
  });
  const addName = () => {
    if (editor.addTool(newName)) setNewName('');
  };

  return (
    <Flex direction="column" gap="3">
      {rows.length > 0 && (
        <Flex direction="column" gap="2">
          <TextField.Root
            size="2"
            placeholder={t('workspace.mcpServers.toolRules.search')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          >
            <TextField.Slot>
              <MaterialIcon name="search" size={16} color="var(--gray-9)" />
            </TextField.Slot>
          </TextField.Root>
          <Flex gap="1" wrap="wrap" role="group" aria-label={t('workspace.mcpServers.toolRules.filterLabel')}>
            {FILTERS.map((id) => (
              <Button
                key={id}
                type="button"
                size="1"
                radius="full"
                variant={filter === id ? 'solid' : 'soft'}
                color={filter === id ? undefined : 'gray'}
                aria-pressed={filter === id}
                onClick={() => setFilter(id)}
              >
                {t(`workspace.mcpServers.toolRules.filter.${id}`)}
              </Button>
            ))}
          </Flex>
        </Flex>
      )}

      {isCompany && !readOnly && editor.quickSetupNames.length > 0 && (
        <Flex
          align="center"
          justify="between"
          gap="3"
          style={{
            padding: 'var(--space-2) var(--space-3)',
            border: '1px dashed var(--gray-a7)',
            borderRadius: 'var(--radius-2)',
          }}
        >
          <Text size="1" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.mcpServers.toolRules.quickSetup.text')}
          </Text>
          <Button type="button" size="1" variant="soft" disabled={saving} onClick={editor.applyQuickSetup}>
            {t('workspace.mcpServers.toolRules.quickSetup.apply')}
          </Button>
        </Flex>
      )}

      {rows.length > 0 && (
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          {t(isCompany ? 'workspace.mcpServers.toolRules.legend.company' : 'workspace.mcpServers.toolRules.legend.own')}
        </Text>
      )}

      {rows.length === 0 ? (
        <Text size="2" style={{ color: 'var(--slate-10)' }}>
          {t('workspace.mcpServers.toolRules.empty')}
        </Text>
      ) : shown.length === 0 ? (
        <Flex direction="column" align="center" gap="2" style={{ padding: 'var(--space-4)' }}>
          <Text size="2" style={{ color: 'var(--gray-10)' }}>
            {t('workspace.mcpServers.toolRules.noMatch')}
          </Text>
          <Button
            type="button"
            size="1"
            variant="ghost"
            onClick={() => {
              setQuery('');
              setFilter('all');
            }}
          >
            {t('workspace.mcpServers.toolRules.clearFilters')}
          </Button>
        </Flex>
      ) : (
        <Flex direction="column" gap="4" style={maxListHeight ? { maxHeight: maxListHeight, overflowY: 'auto' } : undefined}>
          {GROUPS.map((group) => {
            const members = shown.filter((row) => groupOf(row) === group.id);
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
                {members.map((row) => (
                  <ToolRuleRow key={row.name} row={row} editor={editor} readOnly={readOnly} toolsUnknown={toolsUnknown} />
                ))}
              </Flex>
            );
          })}
        </Flex>
      )}

      {!readOnly && (
        <Flex gap="2" align="center">
          <TextField.Root
            size="2"
            style={{ flex: 1 }}
            placeholder={t('workspace.mcpServers.toolRules.addPlaceholder')}
            value={newName}
            maxLength={MAX_TOOL_NAME_CHARS}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') addName();
            }}
          />
          <Button
            type="button"
            size="2"
            variant="soft"
            disabled={saving || !newName.trim() || rows.some((row) => row.name === newName.trim())}
            onClick={addName}
          >
            {t('workspace.mcpServers.toolRules.add')}
          </Button>
        </Flex>
      )}
    </Flex>
  );
}

function ToolRuleRow({
  row,
  editor,
  readOnly,
  toolsUnknown,
}: {
  row: McpRuleTool;
  editor: McpToolRulesEditor;
  readOnly: boolean;
  toolsUnknown: boolean;
}) {
  const { t } = useTranslation();
  const { isCompany, saving } = editor;
  const entry = editor.companyEntry(row.name);
  const companyChoice: CompanyChoice = entry.rule ?? 'none';
  const kind = row.kind;

  return (
    <Flex
      align="start"
      justify="between"
      gap="3"
      wrap="wrap"
      style={{ padding: 'var(--space-2) var(--space-3)', borderRadius: 'var(--radius-2)', backgroundColor: 'var(--gray-a2)' }}
    >
      <Box style={{ minWidth: 0, flex: '1 1 14rem' }}>
        <Flex align="center" gap="2" wrap="wrap">
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)', overflowWrap: 'anywhere' }}>
            {row.title || row.name}
          </Text>
          {kind && row.offered !== false && (
            <Badge size="1" color={KIND_COLORS[kind]} variant="soft">
              {t(`workspace.mcpServers.toolRules.kind.${kind}`)}
            </Badge>
          )}
          {row.offered === false && !toolsUnknown && (
            <Badge size="1" color="gray" variant="soft">
              {t('workspace.mcpServers.toolRules.notOffered')}
            </Badge>
          )}
        </Flex>
        <Flex align="center" gap="2" wrap="wrap">
          {row.title && row.title !== row.name && (
            <Text size="1" style={{ color: 'var(--gray-10)', fontFamily: 'var(--code-font-family)', overflowWrap: 'anywhere' }}>
              {row.name}
            </Text>
          )}
          {row.kindSource && (
            <Text size="1" style={{ color: 'var(--gray-9)' }}>
              {t(`workspace.mcpServers.toolRules.kindSource.${row.kindSource}`)}
            </Text>
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

      {readOnly ? (
        <Badge size="2" color="gray" variant="soft">
          {isCompany
            ? t(`workspace.mcpServers.toolRules.company.${companyChoice}`)
            : t(`workspace.mcpServers.toolRules.rule.${editor.ownRule(row)}`)}
        </Badge>
      ) : isCompany ? (
        <Flex direction="column" align="end" gap="2">
          <SegmentedControl.Root
            size="1"
            aria-label={row.name}
            value={companyChoice}
            onValueChange={(value) => !saving && editor.setCompanyRule(row.name, value as CompanyChoice)}
          >
            {COMPANY_RULES.map((rule) => (
              <SegmentedControl.Item key={rule} value={rule}>
                {t(`workspace.mcpServers.toolRules.company.${rule}`)}
              </SegmentedControl.Item>
            ))}
          </SegmentedControl.Root>
          {companyChoice !== 'block' && (
            <Text as="label" size="1" style={{ color: 'var(--gray-11)', maxWidth: '18rem' }}>
              <Flex align="start" gap="2" justify="end">
                <Flex direction="column" align="end">
                  <span>{t('workspace.mcpServers.toolRules.unattended')}</span>
                  <span style={{ color: 'var(--gray-9)' }}>
                    {t(
                      kind === 'destructive'
                        ? 'workspace.mcpServers.toolRules.unattendedDestructive'
                        : 'workspace.mcpServers.toolRules.unattendedDetail'
                    )}
                  </span>
                </Flex>
                <Switch
                  size="1"
                  aria-label={t('workspace.mcpServers.toolRules.unattendedFor', { name: row.name })}
                  checked={entry.unattended === true}
                  disabled={saving}
                  onCheckedChange={(checked) => editor.setUnattended(row.name, checked === true)}
                />
              </Flex>
            </Text>
          )}
        </Flex>
      ) : (
        <SegmentedControl.Root
          size="1"
          aria-label={row.name}
          value={editor.ownRule(row)}
          onValueChange={(value) => !saving && editor.setOwnRule(row.name, value as McpToolRule)}
        >
          {OWN_RULES.map((rule) => (
            <SegmentedControl.Item key={rule} value={rule}>
              {t(`workspace.mcpServers.toolRules.rule.${rule}`)}
            </SegmentedControl.Item>
          ))}
        </SegmentedControl.Root>
      )}
    </Flex>
  );
}
