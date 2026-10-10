'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Box, Button, Checkbox, Dialog, Flex, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { AgentsApi } from '@/app/(main)/agents/api';
import { ToolsetsApi } from '@/app/(main)/toolsets/api';
import type { DraftKnowledge, DraftToolset } from '../../types';
import { toDraftToolset, toolDisplayName, toolTickKey } from './agent-draft-model';
import { KnowledgeIcon, ToolsetIcon } from './agent-draft-icons';

type Load<T> = { state: 'loading' } | { state: 'error' } | { state: 'ready'; items: T[] };

const ROW_STYLE: React.CSSProperties = { minHeight: 40, padding: '0 var(--space-2)', borderRadius: 'var(--radius-2)' };
const LIST_HEIGHT = 'min(320px, 50dvh)';

function useLoad<T>(fetcher: () => Promise<T[]>): { load: Load<T>; retry: () => void } {
  const [load, setLoad] = useState<Load<T>>({ state: 'loading' });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setLoad({ state: 'loading' });
    fetcher()
      .then((items) => live && setLoad({ state: 'ready', items }))
      .catch(() => live && setLoad({ state: 'error' }));
    return () => {
      live = false;
    };
  }, [attempt]);
  return { load, retry: () => setAttempt((n) => n + 1) };
}

async function fetchKnowledge(): Promise<DraftKnowledge[]> {
  const [kbs, apps] = await Promise.allSettled([
    AgentsApi.getKnowledgeBasesForBuilder({ quiet: true }),
    AgentsApi.getKnowledgeHubAppNodes({ limit: 100, flattened: false, quiet: true }),
  ]);
  if (kbs.status === 'rejected' && apps.status === 'rejected') throw kbs.reason;
  const collections: DraftKnowledge[] =
    kbs.status === 'fulfilled' ? kbs.value.knowledgeBases.map((kb) => ({ id: kb.id, name: kb.name, kind: 'collection' })) : [];
  const connectors: DraftKnowledge[] =
    apps.status === 'fulfilled'
      ? apps.value.nodes.map((n) => ({ id: n.id, name: n.name, kind: 'connector', connectorType: n.connector }))
      : [];
  return [...collections, ...connectors];
}

async function fetchActions(): Promise<DraftToolset[]> {
  const { toolsets } = await ToolsetsApi.getAllMyToolsets({ includeRegistry: false, quiet: true });
  return toolsets
    .filter((ts) => ts.isConfigured && ts.isAuthenticated)
    .map(toDraftToolset)
    .filter((ts): ts is DraftToolset => ts !== null && ts.tools.length > 0);
}

function ListState({ load, retry, empty, noMatch, children }: {
  load: Load<unknown>;
  retry: () => void;
  empty: boolean;
  noMatch: boolean;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  const note = (text: string) => (
    <Flex align="center" justify="center" direction="column" gap="2" style={{ height: '100%', textAlign: 'center', padding: 'var(--space-3)' }}>
      <Text size="2" style={{ color: 'var(--slate-11)' }}>{text}</Text>
    </Flex>
  );
  if (load.state === 'loading') return <div role="status" style={{ height: '100%' }}>{note(t('chat.agentDraft.pickerLoading'))}</div>;
  if (load.state === 'error') {
    return (
      <Flex align="center" justify="center" direction="column" gap="2" role="alert" style={{ height: '100%', padding: 'var(--space-3)' }}>
        <Text size="2" style={{ color: 'var(--slate-11)' }}>{t('chat.agentDraft.pickerError')}</Text>
        <Button type="button" size="2" variant="soft" onClick={retry}>{t('chat.agentDraft.pickerRetry')}</Button>
      </Flex>
    );
  }
  if (empty) return note(t('chat.agentDraft.pickerEmpty'));
  if (noMatch) return note(t('chat.agentDraft.pickerNoMatch'));
  return <>{children}</>;
}

function PickerShell({ title, query, onQuery, canAdd, addCount, onAdd, children }: {
  title: string;
  query: string;
  onQuery: (q: string) => void;
  canAdd: boolean;
  addCount: number;
  onAdd: () => void;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <>
      <Dialog.Title size="3" mb="2">{title}</Dialog.Title>
      <Dialog.Description size="1" style={{ color: 'var(--slate-11)' }} mb="3">{t('chat.agentDraft.pickerHint')}</Dialog.Description>
      <TextField.Root
        size="3"
        value={query}
        placeholder={t('chat.agentDraft.pickerSearch')}
        aria-label={t('chat.agentDraft.pickerSearch')}
        data-testid="agent-draft-picker-search"
        onChange={(e) => onQuery(e.target.value)}
      >
        <TextField.Slot><MaterialIcon name="search" size={16} /></TextField.Slot>
      </TextField.Root>
      <Box mt="2" style={{ height: LIST_HEIGHT, overflowY: 'auto', border: '1px solid var(--slate-a5)', borderRadius: 'var(--radius-2)' }}>
        {children}
      </Box>
      <Flex justify="end" gap="2" mt="3">
        <Dialog.Close><Button type="button" size="2" variant="soft" color="gray">{t('chat.agentDraft.pickerCancel')}</Button></Dialog.Close>
        <Button type="button" size="2" data-testid="agent-draft-picker-add" disabled={!canAdd} onClick={onAdd}>
          {addCount > 0 ? t('chat.agentDraft.pickerAddCount', { count: addCount }) : t('chat.agentDraft.pickerAdd')}
        </Button>
      </Flex>
    </>
  );
}

const matches = (query: string, ...texts: (string | null | undefined)[]): boolean => {
  const q = query.trim().toLowerCase();
  return !q || texts.some((x) => x?.toLowerCase().includes(q));
};

function KnowledgePicker({ existing, onAdd, onClose }: {
  existing: ReadonlySet<string>;
  onAdd: (items: DraftKnowledge[]) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { load, retry } = useLoad(fetchKnowledge);
  const [query, setQuery] = useState('');
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  const items = load.state === 'ready' ? load.items : [];
  const visible = items.filter((k) => matches(query, k.name, k.connectorType));
  const toggle = (id: string, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  const add = () => {
    onAdd(items.filter((k) => picked.has(k.id)));
    onClose();
  };
  return (
    <PickerShell title={t('chat.agentDraft.pickerKnowledgeTitle')} query={query} onQuery={setQuery} canAdd={picked.size > 0} addCount={picked.size} onAdd={add}>
      <ListState load={load} retry={retry} empty={items.length === 0} noMatch={visible.length === 0}>
        <Flex direction="column" role="group" aria-label={t('chat.agentDraft.knowledge')} p="1">
          {visible.map((k) => {
            const added = existing.has(k.id);
            return (
              <Text as="label" size="2" key={k.id} style={{ ...ROW_STYLE, display: 'flex', alignItems: 'center', gap: 'var(--space-2)', opacity: added ? 0.6 : 1 }}>
                <Checkbox
                  data-testid={`agent-draft-picker-knowledge-${k.id}`}
                  checked={added || picked.has(k.id)}
                  disabled={added}
                  onCheckedChange={(on) => toggle(k.id, on === true)}
                />
                <KnowledgeIcon source={k} />
                <span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>{k.name}</span>
                <Text size="1" style={{ color: 'var(--slate-11)', flexShrink: 0 }}>
                  {added ? t('chat.agentDraft.pickerAdded') : k.kind === 'collection' ? t('chat.agentDraft.kindCollection') : t('chat.agentDraft.kindConnector')}
                </Text>
              </Text>
            );
          })}
        </Flex>
      </ListState>
    </PickerShell>
  );
}

function ActionsPicker({ existing, onAdd, onClose }: {
  existing: ReadonlySet<string>;
  onAdd: (items: DraftToolset[]) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { load, retry } = useLoad(fetchActions);
  const [query, setQuery] = useState('');
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  const groups = useMemo(() => (load.state === 'ready' ? load.items : []), [load]);
  const visible = groups
    .map((g) => (matches(query, g.displayName, g.instanceName) ? g : { ...g, tools: g.tools.filter((x) => matches(query, x.name, x.description)) }))
    .filter((g) => g.tools.length > 0);
  const setMany = useCallback((keys: string[], on: boolean) => {
    setPicked((prev) => {
      const next = new Set(prev);
      keys.forEach((k) => (on ? next.add(k) : next.delete(k)));
      return next;
    });
  }, []);
  const add = () => {
    const chosen = groups
      .map((g) => ({ ...g, tools: g.tools.filter((x) => picked.has(toolTickKey(g.instanceId, x.fullName))) }))
      .filter((g) => g.tools.length > 0);
    onAdd(chosen);
    onClose();
  };
  return (
    <PickerShell title={t('chat.agentDraft.pickerActionsTitle')} query={query} onQuery={setQuery} canAdd={picked.size > 0} addCount={picked.size} onAdd={add}>
      <ListState load={load} retry={retry} empty={groups.length === 0} noMatch={visible.length === 0}>
        <Flex direction="column" p="1" gap="2">
          {visible.map((g) => {
            const keys = g.tools.map((x) => toolTickKey(g.instanceId, x.fullName));
            const selectable = keys.filter((k) => !existing.has(k));
            const on = selectable.filter((k) => picked.has(k)).length;
            const all = selectable.length > 0 && on === selectable.length;
            return (
              <Flex key={g.instanceId} direction="column" role="group" aria-label={g.displayName}>
                <Text as="label" size="2" weight="medium" style={{ ...ROW_STYLE, display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                  <Checkbox
                    aria-label={t('chat.agentDraft.allActionsOf', { name: g.displayName })}
                    checked={all ? true : on > 0 ? 'indeterminate' : false}
                    disabled={selectable.length === 0}
                    onCheckedChange={(next) => setMany(selectable, next === true)}
                  />
                  <ToolsetIcon toolset={g} />
                  <span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>
                    {g.displayName}
                    {g.instanceName && g.instanceName !== g.displayName ? ` · ${g.instanceName}` : ''}
                  </span>
                </Text>
                {g.tools.map((x) => {
                  const key = toolTickKey(g.instanceId, x.fullName);
                  const added = existing.has(key);
                  return (
                    <Text as="label" size="2" key={key} style={{ ...ROW_STYLE, paddingLeft: 'var(--space-6)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)', opacity: added ? 0.6 : 1 }}>
                      <Checkbox
                        data-testid={`agent-draft-picker-tool-${x.fullName}`}
                        checked={added || picked.has(key)}
                        disabled={added}
                        onCheckedChange={(next) => setMany([key], next === true)}
                      />
                      <span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>{toolDisplayName(x.name)}</span>
                      {added ? <Text size="1" style={{ color: 'var(--slate-11)' }}>{t('chat.agentDraft.pickerAdded')}</Text> : null}
                    </Text>
                  );
                })}
              </Flex>
            );
          })}
        </Flex>
      </ListState>
    </PickerShell>
  );
}

export type PickerMode = 'knowledge' | 'actions';

export interface AgentDraftPickerProps {
  mode: PickerMode | null;
  onClose: () => void;
  /** Collection and connector ids already in the draft. */
  existingKnowledge: ReadonlySet<string>;
  /** Tick keys (`instanceId|fullName`) of tools already in the draft. */
  existingTools: ReadonlySet<string>;
  onAddKnowledge: (items: DraftKnowledge[]) => void;
  onAddActions: (items: DraftToolset[]) => void;
}

/** Search-and-add dialog for the lists the requester can actually use. Content mounts on open, so lists load lazily. */
export function AgentDraftPicker({ mode, onClose, existingKnowledge, existingTools, onAddKnowledge, onAddActions }: AgentDraftPickerProps) {
  return (
    <Dialog.Root open={mode !== null} onOpenChange={(open) => !open && onClose()}>
      <Dialog.Content
        data-testid="agent-draft-picker"
        style={{ width: 'calc(100vw - 32px)', maxWidth: 480, padding: 'var(--space-4)' }}
      >
        {mode === 'knowledge' ? <KnowledgePicker existing={existingKnowledge} onAdd={onAddKnowledge} onClose={onClose} /> : null}
        {mode === 'actions' ? <ActionsPicker existing={existingTools} onAdd={onAddActions} onClose={onClose} /> : null}
      </Dialog.Content>
    </Dialog.Root>
  );
}
