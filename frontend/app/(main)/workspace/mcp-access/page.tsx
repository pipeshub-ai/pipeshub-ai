'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import { Badge, Box, Button, Flex, Heading, Switch, Text, TextField } from '@radix-ui/themes';
import { SettingsSaveBar, SettingsSection } from '../components';
import { SettingsPageContainer } from '@/app/(main)/workspace/components/settings-page-container';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useToastStore } from '@/lib/store/toast-store';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';
import { McpAccessApi, type McpAccessPolicy, type McpAccessTool } from './api';
import { McpConnectCard, McpToolGroup, McpToolRow } from './components';
import {
  TOOL_GROUPS,
  blockReason,
  groupOf,
  isOffered,
  matchesTool,
  type ToolFilter,
  type ToolGroup,
} from './tool-groups';

const FILTERS: ToolFilter[] = ['all', 'read', 'writes', 'off'];

/** Every group open while a search or filter narrows the list, so its matches show; none otherwise. */
function openWhileSearching(query: string, filter: ToolFilter): Set<ToolGroup> {
  return query.trim() !== '' || filter !== 'all' ? new Set(TOOL_GROUPS) : new Set();
}

const samePolicy = (a: McpAccessPolicy, b: McpAccessPolicy): boolean =>
  a.enabled === b.enabled &&
  a.allowWriteTools === b.allowWriteTools &&
  a.disabledTools.length === b.disabledTools.length &&
  a.disabledTools.every((name) => b.disabledTools.includes(name));

function SwitchRow({
  label,
  description,
  checked,
  disabled,
  onCheckedChange,
}: {
  label: string;
  description?: string;
  checked: boolean;
  disabled?: boolean;
  onCheckedChange: (value: boolean) => void;
}) {
  return (
    <Flex align="center" justify="between" style={{ width: '100%', gap: 'var(--space-4)', minHeight: 44 }}>
      <Box style={{ flex: 1, minWidth: 0 }}>
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {label}
        </Text>
        {description && (
          <Text
            size="1"
            style={{ color: 'var(--slate-11)', display: 'block', marginTop: 2, lineHeight: '16px', fontWeight: 300, wordBreak: 'break-word' }}
          >
            {description}
          </Text>
        )}
      </Box>
      <Switch
        size="1"
        checked={checked}
        disabled={disabled}
        onCheckedChange={onCheckedChange}
        aria-label={label}
        style={{ flexShrink: 0, cursor: disabled ? 'default' : 'pointer' }}
      />
    </Flex>
  );
}

/** The text of `key` when this build has one; tools come from the server, so a newer one may not. */
function useOptionalText(): (key: string) => string | undefined {
  const { t } = useTranslation();
  return useCallback(
    (key: string) => {
      const text = t(key);
      return text === key ? undefined : text;
    },
    [t],
  );
}

export default function McpAccessPage() {
  const { t } = useTranslation();
  const optionalText = useOptionalText();
  const router = useRouter();
  const addToast = useToastStore((s) => s.addToast);
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  const [saved, setSaved] = useState<McpAccessPolicy | null>(null);
  const [form, setForm] = useState<McpAccessPolicy | null>(null);
  const [tools, setTools] = useState<McpAccessTool[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<ToolFilter>('all');
  const [openTools, setOpenTools] = useState<Set<string>>(new Set());
  // Groups start closed; a search or filter opens them all, so the matches show.
  const [openGroups, setOpenGroups] = useState<Set<ToolGroup>>(new Set());

  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

  useEffect(() => {
    if (!isProfileInitialized || isAdmin === false) return;
    let cancelled = false;
    void (async () => {
      const [policyResult, toolsResult] = await Promise.allSettled([
        McpAccessApi.getPolicy(),
        McpAccessApi.getTools(),
      ]);
      if (cancelled) return;
      if (policyResult.status === 'fulfilled') {
        setSaved(policyResult.value);
        setForm(policyResult.value);
      } else {
        setLoadFailed(true);
      }
      setTools(toolsResult.status === 'fulfilled' ? toolsResult.value : []);
    })();
    return () => {
      cancelled = true;
    };
  }, [isProfileInitialized, isAdmin]);

  const endpoint = useMemo(() => {
    const origin = getApiBaseUrl() || (typeof window === 'undefined' ? '' : window.location.origin);
    return `${origin.replace(/\/$/, '')}/mcp`;
  }, []);

  const summaryOf = useCallback(
    (name: string) => optionalText(`workspace.mcpAccess.tools.descriptions.${name}`),
    [optionalText],
  );

  const setToolsOn = useCallback((names: string[], on: boolean) => {
    setForm(
      (current) =>
        current && {
          ...current,
          disabledTools: on
            ? current.disabledTools.filter((n) => !names.includes(n))
            : [...current.disabledTools.filter((n) => !names.includes(n)), ...names],
        },
    );
  }, []);

  const handleSave = useCallback(async () => {
    if (!form) return;
    setIsSaving(true);
    try {
      const stored = await McpAccessApi.savePolicy(form);
      setSaved(stored);
      setForm(stored);
      addToast({ variant: 'success', title: t('workspace.mcpAccess.toasts.saveSuccess') });
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.mcpAccess.toasts.saveError'),
        description: t('workspace.mcpAccess.toasts.saveErrorDescription'),
      });
    } finally {
      setIsSaving(false);
    }
  }, [form, addToast, t]);

  if (!isProfileInitialized || isAdmin === false) return null;

  if (!loadFailed && (!form || tools === null)) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', width: '100%' }}>
        <LottieLoader variant="loader" size={48} showLabel />
      </Flex>
    );
  }

  const dirty = !!form && !!saved && !samePolicy(form, saved);
  const allTools = tools ?? [];
  const offeredCount = form ? allTools.filter((tool) => isOffered(tool, form)).length : 0;
  const searching = query.trim() !== '' || filter !== 'all';
  const allOpen =
    allTools.length > 0 &&
    allTools.every((tool) => openTools.has(tool.name) && openGroups.has(groupOf(tool.name)));

  const availabilityOf = (tool: McpAccessTool, group: ToolGroup): string =>
    optionalText(`workspace.mcpAccess.tools.availability.${tool.name}`) ??
    optionalText(`workspace.mcpAccess.tools.groups.${group}.availability`) ??
    t('workspace.mcpAccess.tools.availability.default');

  const groups = form
    ? TOOL_GROUPS.map((group) => {
        const all = allTools.filter((tool) => groupOf(tool.name) === group);
        const switchable = all.filter((tool) => !blockReason(tool, form));
        return {
          group,
          all,
          shown: all.filter((tool) => matchesTool(tool, summaryOf(tool.name), query, filter, form)),
          offered: all.filter((tool) => isOffered(tool, form)).length,
          switchable,
          allOn: switchable.length > 0 && switchable.every((tool) => !form.disabledTools.includes(tool.name)),
        };
      }).filter((g) => g.all.length > 0)
    : [];
  const shownGroups = searching ? groups.filter((g) => g.shown.length > 0) : groups;

  return (
    <Box style={{ height: '100%', overflowY: 'auto' }}>
      <SettingsPageContainer>
        <Flex justify="between" align="end" gap="3" wrap="wrap" style={{ marginBottom: 'var(--space-6)' }}>
          <Box style={{ minWidth: 0 }}>
            <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('workspace.mcpAccess.title')}
            </Heading>
            <Text size="2" style={{ color: 'var(--slate-11)', marginTop: 'var(--space-1)', display: 'block' }}>
              {t('workspace.mcpAccess.subtitle')}
            </Text>
          </Box>
          {form && (
            <Badge size="2" color={form.enabled ? 'jade' : 'gray'} variant="soft">
              <span aria-hidden="true" style={{ display: 'inline-flex' }}>
                <MaterialIcon name={form.enabled ? 'check_circle' : 'block'} size={14} />
              </span>
              {form.enabled
                ? t('workspace.mcpAccess.status.on', { offered: offeredCount, total: allTools.length })
                : t('workspace.mcpAccess.status.off')}
            </Badge>
          )}
        </Flex>

        {loadFailed || !form ? (
          <Text size="2" style={{ color: 'var(--red-a11)' }}>
            {t('workspace.mcpAccess.loadError')}
          </Text>
        ) : (
          <Flex direction="column" gap="5" style={{ paddingBottom: 'var(--space-9)' }}>
            <McpConnectCard url={endpoint} />

            <SettingsSection>
              <SwitchRow
                label={t('workspace.mcpAccess.enabled.label')}
                description={t('workspace.mcpAccess.enabled.description')}
                checked={form.enabled}
                onCheckedChange={(enabled) => setForm({ ...form, enabled })}
              />
              <SwitchRow
                label={t('workspace.mcpAccess.writeTools.label')}
                description={t('workspace.mcpAccess.writeTools.description')}
                checked={form.allowWriteTools}
                disabled={!form.enabled}
                onCheckedChange={(allowWriteTools) => setForm({ ...form, allowWriteTools })}
              />
            </SettingsSection>

            {allTools.length > 0 && (
              <Flex direction="column" gap="3">
                <Flex justify="between" align="end" gap="3" wrap="wrap">
                  <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
                    <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
                      {t('workspace.mcpAccess.tools.title')}
                      <Text weight="regular" style={{ color: 'var(--slate-11)' }}>
                        {' · '}
                        {t('workspace.mcpAccess.tools.count', { offered: offeredCount, total: allTools.length })}
                      </Text>
                    </Text>
                    <Text size="1" style={{ color: 'var(--slate-11)', fontWeight: 300, lineHeight: '16px' }}>
                      {t('workspace.mcpAccess.tools.subtitle')}
                    </Text>
                  </Flex>
                  <Button
                    size="2"
                    variant="soft"
                    color="gray"
                    onClick={() => {
                      setOpenTools(allOpen ? new Set() : new Set(allTools.map((tool) => tool.name)));
                      setOpenGroups(allOpen ? new Set() : new Set(TOOL_GROUPS));
                    }}
                  >
                    {t(allOpen ? 'workspace.mcpAccess.tools.collapseAll' : 'workspace.mcpAccess.tools.expandAll')}
                  </Button>
                </Flex>

                <Flex gap="2" align="center" wrap="wrap">
                  <TextField.Root
                    size="2"
                    type="search"
                    value={query}
                    onChange={(event) => {
                      setQuery(event.target.value);
                      setOpenGroups(openWhileSearching(event.target.value, filter));
                    }}
                    placeholder={t('workspace.mcpAccess.tools.searchPlaceholder')}
                    aria-label={t('workspace.mcpAccess.tools.searchLabel')}
                    style={{ flex: '1 1 260px', minWidth: 0 }}
                  >
                    <TextField.Slot>
                      <span aria-hidden="true" style={{ display: 'inline-flex' }}>
                        <MaterialIcon name="search" size={16} color="var(--gray-11)" />
                      </span>
                    </TextField.Slot>
                  </TextField.Root>
                  {/* Buttons, not a segmented control: they wrap at phone width. */}
                  <Flex gap="2" wrap="wrap" role="group" aria-label={t('workspace.mcpAccess.tools.filterLabel')}>
                    {FILTERS.map((id) => (
                      <Button
                        key={id}
                        size="2"
                        radius="full"
                        variant={filter === id ? 'soft' : 'outline'}
                        color={filter === id ? undefined : 'gray'}
                        aria-pressed={filter === id}
                        onClick={() => {
                          setFilter(id);
                          setOpenGroups(openWhileSearching(query, id));
                        }}
                      >
                        {t(`workspace.mcpAccess.tools.filters.${id}`)}
                      </Button>
                    ))}
                  </Flex>
                </Flex>

                {/* Always mounted: a live region added together with its text is often not announced. */}
                <Box role="status">
                  {shownGroups.length === 0 && (
                    <Text as="p" size="2" style={{ color: 'var(--slate-11)', padding: 'var(--space-4)', textAlign: 'center' }}>
                      {t('workspace.mcpAccess.tools.noMatch')}
                    </Text>
                  )}
                </Box>

                {shownGroups.map(({ group, shown, all, offered, switchable, allOn }) => (
                  <McpToolGroup
                    key={group}
                    group={group}
                    offered={offered}
                    total={all.length}
                    allOn={allOn}
                    switchDisabled={!form.enabled || switchable.length === 0}
                    open={openGroups.has(group)}
                    onOpenChange={(open) =>
                      setOpenGroups((current) => {
                        const next = new Set(current);
                        if (open) next.add(group);
                        else next.delete(group);
                        return next;
                      })
                    }
                    onAllChange={(on) => setToolsOn(switchable.map((tool) => tool.name), on)}
                  >
                    {(searching ? shown : all).map((tool) => {
                      const blocked = blockReason(tool, form);
                      return (
                        <McpToolRow
                          key={tool.name}
                          tool={tool}
                          summary={summaryOf(tool.name)}
                          availability={availabilityOf(tool, group)}
                          checked={isOffered(tool, form)}
                          blocked={blocked}
                          disabled={!!blocked}
                          open={openTools.has(tool.name)}
                          onOpenChange={(open) =>
                            setOpenTools((current) => {
                              const next = new Set(current);
                              if (open) next.add(tool.name);
                              else next.delete(tool.name);
                              return next;
                            })
                          }
                          onCheckedChange={(on) => setToolsOn([tool.name], on)}
                        />
                      );
                    })}
                  </McpToolGroup>
                ))}
              </Flex>
            )}

            <Text size="1" style={{ color: 'var(--slate-11)' }}>
              {t('workspace.mcpAccess.appliesNote')}
            </Text>
          </Flex>
        )}
      </SettingsPageContainer>

      <SettingsSaveBar
        visible={dirty}
        isSaving={isSaving}
        onDiscard={() => saved && setForm(saved)}
        onSave={handleSave}
      />
    </Box>
  );
}
