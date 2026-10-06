'use client';

import React, { useState } from 'react';
import { Button, Checkbox, Flex, Switch, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { DraftKnowledge, DraftToolset, DraftUnresolved, DraftWebSearch } from '../../types';
import { toolDisplayName, toolTickKey } from './agent-draft-model';
import { KnowledgeIcon, ToolsetIcon } from './agent-draft-icons';

const hint = { color: 'var(--slate-11)' } as const;
const ROW: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 'var(--space-2)',
  minHeight: 40,
  minWidth: 0,
};
const GROW: React.CSSProperties = { flex: 1, minWidth: 0, overflowWrap: 'anywhere' };
/** A note under a checkbox row lines up with its label: the checkbox plus the row's gap. */
export const underLabel = { paddingLeft: 'calc(var(--space-4) + var(--space-2))' } as const;
const COLLAPSED_TOOLS = 5;

export const SectionHeader = ({ children, action }: { children: React.ReactNode; action?: React.ReactNode }) => (
  <Flex align="center" justify="between" gap="2" wrap="wrap">
    <Text size="1" weight="medium" style={hint}>{children}</Text>
    {action}
  </Flex>
);

export function AddButton({ label, testId, disabled, onClick }: { label: string; testId: string; disabled: boolean; onClick: () => void }) {
  return (
    <Button type="button" size="2" variant="soft" color="gray" data-testid={testId} disabled={disabled} onClick={onClick} style={{ minHeight: 40 }}>
      <MaterialIcon name="add" size={16} />
      {label}
    </Button>
  );
}

interface KnowledgeSectionProps {
  items: DraftKnowledge[];
  names: Record<string, string>;
  ticked: ReadonlySet<string>;
  rejected: readonly string[];
  readOnly: boolean;
  onToggle: (id: string, on: boolean) => void;
}

export function KnowledgeSection({ items, names, ticked, rejected, readOnly, onToggle }: KnowledgeSectionProps) {
  const { t } = useTranslation();
  if (items.length === 0) return null;
  return (
    <Flex direction="column" role="group" aria-label={t('chat.agentDraft.knowledge')}>
      <SectionHeader>{t('chat.agentDraft.knowledge')}</SectionHeader>
      {items.map((item) => {
        const gone = rejected.includes(item.id);
        const label = item.name || names[item.id] || t('chat.agentDraft.knowledgeFallback', { id: item.id.length > 12 ? `${item.id.slice(0, 8)}…` : item.id });
        return (
          <Flex key={item.id} direction="column" data-testid={`agent-draft-knowledge-row-${item.id}`}>
            <Text as="label" size="2" style={ROW}>
              <Checkbox
                data-testid={`agent-draft-knowledge-${item.id}`}
                checked={ticked.has(item.id)}
                disabled={readOnly || gone}
                onCheckedChange={(on) => onToggle(item.id, on === true)}
              />
              <KnowledgeIcon source={item} />
              <span style={GROW}>{label}</span>
            </Text>
            {gone ? <Text size="1" role="alert" style={{ ...underLabel, color: 'var(--red-11)' }}>{t('chat.agentDraft.unavailable')}</Text> : null}
          </Flex>
        );
      })}
    </Flex>
  );
}

interface ActionsSectionProps {
  groups: DraftToolset[];
  ticked: ReadonlySet<string>;
  rejectedInstances: readonly string[];
  readOnly: boolean;
  onToggleMany: (keys: string[], on: boolean) => void;
}

function ToolsetGroup({ group, ticked, gone, readOnly, onToggleMany }: {
  group: DraftToolset;
  ticked: ReadonlySet<string>;
  gone: boolean;
  readOnly: boolean;
  onToggleMany: (keys: string[], on: boolean) => void;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const keys = group.tools.map((tool) => toolTickKey(group.instanceId, tool.fullName));
  const on = keys.filter((k) => ticked.has(k)).length;
  const shown = expanded ? group.tools : group.tools.slice(0, COLLAPSED_TOOLS);
  const title = group.instanceName && group.instanceName !== group.displayName
    ? `${group.displayName} · ${group.instanceName}`
    : group.displayName;
  return (
    <Flex direction="column" role="group" aria-label={title} data-testid={`agent-draft-toolset-row-${group.instanceId}`}>
      <Text as="label" size="2" weight="medium" style={ROW}>
        <Checkbox
          data-testid={`agent-draft-toolset-${group.instanceId}`}
          aria-label={t('chat.agentDraft.allActionsOf', { name: title })}
          checked={on === keys.length && keys.length > 0 ? true : on > 0 ? 'indeterminate' : false}
          disabled={readOnly || gone}
          onCheckedChange={(next) => onToggleMany(keys, next === true)}
        />
        <ToolsetIcon toolset={group} />
        <span style={GROW}>{title}</span>
      </Text>
      {gone ? <Text size="1" role="alert" style={{ ...underLabel, color: 'var(--red-11)' }}>{t('chat.agentDraft.unavailable')}</Text> : null}
      {shown.map((tool) => {
        const key = toolTickKey(group.instanceId, tool.fullName);
        return (
          <Text as="label" size="2" key={tool.fullName} style={{ ...ROW, paddingLeft: 'var(--space-5)' }} title={tool.description || undefined}>
            <Checkbox
              data-testid={`agent-draft-tool-${tool.fullName}`}
              checked={ticked.has(key)}
              disabled={readOnly || gone}
              onCheckedChange={(next) => onToggleMany([key], next === true)}
            />
            <span style={GROW}>{toolDisplayName(tool.name)}</span>
          </Text>
        );
      })}
      {group.tools.length > COLLAPSED_TOOLS ? (
        <Button
          type="button"
          size="2"
          variant="ghost"
          color="gray"
          aria-expanded={expanded}
          data-testid={`agent-draft-toolset-more-${group.instanceId}`}
          style={{ alignSelf: 'flex-start', minHeight: 40, marginLeft: 'var(--space-5)' }}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? t('chat.agentDraft.showFewer') : t('chat.agentDraft.showAll', { count: group.tools.length })}
        </Button>
      ) : null}
    </Flex>
  );
}

export function ActionsSection({ groups, ticked, rejectedInstances, readOnly, onToggleMany }: ActionsSectionProps) {
  const { t } = useTranslation();
  if (groups.length === 0) return null;
  return (
    <Flex direction="column" gap="1" role="group" aria-label={t('chat.agentDraft.actions')}>
      <SectionHeader>{t('chat.agentDraft.actions')}</SectionHeader>
      {groups.map((group) => (
        <ToolsetGroup
          key={group.instanceId}
          group={group}
          ticked={ticked}
          gone={rejectedInstances.includes(group.instanceId)}
          readOnly={readOnly}
          onToggleMany={onToggleMany}
        />
      ))}
    </Flex>
  );
}

export function WebSearchRow({ webSearch, on, readOnly, onChange }: {
  webSearch: DraftWebSearch;
  on: boolean;
  readOnly: boolean;
  onChange: (on: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <Flex align="center" justify="between" gap="3" style={{ minHeight: 40 }} data-testid="agent-draft-websearch-row">
      <Flex direction="column" style={{ minWidth: 0 }}>
        <Text as="label" size="2" htmlFor="agent-draft-websearch-switch">{t('chat.agentDraft.webSearch')}</Text>
        <Text size="1" style={hint}>{t('chat.agentDraft.webSearchProvider', { provider: webSearch.providerLabel })}</Text>
      </Flex>
      <Switch
        id="agent-draft-websearch-switch"
        data-testid="agent-draft-websearch"
        checked={on}
        disabled={readOnly}
        onCheckedChange={onChange}
      />
    </Flex>
  );
}

const TOOLSETS_SETTINGS_ROUTE = '/workspace/actions/personal/';

export function UnresolvedSection({ items }: { items: DraftUnresolved[] }) {
  const { t } = useTranslation();
  if (items.length === 0) return null;
  const sentence = (item: DraftUnresolved): string => {
    const query = item.query;
    if (item.reason === 'ambiguous') return t('chat.agentDraft.unresolvedAmbiguous', { query, candidates: (item.candidates ?? []).join(', ') });
    if (item.reason === 'not_connected') return t('chat.agentDraft.unresolvedNotConnected', { query });
    if (item.reason === 'unavailable') return t('chat.agentDraft.unresolvedUnavailable', { query });
    return t('chat.agentDraft.unresolvedNotFound', { query });
  };
  return (
    <Flex
      direction="column"
      gap="1"
      role="group"
      aria-label={t('chat.agentDraft.unresolvedTitle')}
      data-testid="agent-draft-unresolved"
      style={{ background: 'var(--slate-3)', borderRadius: 'var(--radius-2)', padding: 'var(--space-2)' }}
    >
      <Flex align="center" gap="1">
        <MaterialIcon name="info" size={16} color="var(--slate-11)" />
        <Text size="2" weight="medium" style={hint}>{t('chat.agentDraft.unresolvedTitle')}</Text>
      </Flex>
      {items.map((item, i) => (
        <Flex key={`${item.kind}-${item.query}-${i}`} align="center" justify="between" gap="2" wrap="wrap" style={{ paddingLeft: 'var(--space-5)' }}>
          <Text size="2" style={{ ...hint, minWidth: 0, overflowWrap: 'anywhere' }}>{sentence(item)}</Text>
          {item.reason === 'not_connected' ? (
            <Button asChild size="2" variant="ghost" style={{ minHeight: 40 }}>
              <a href={TOOLSETS_SETTINGS_ROUTE} target="_blank" rel="noopener noreferrer" data-testid="agent-draft-connect">
                {t('chat.agentDraft.connect')}
              </a>
            </Button>
          ) : null}
        </Flex>
      ))}
    </Flex>
  );
}
