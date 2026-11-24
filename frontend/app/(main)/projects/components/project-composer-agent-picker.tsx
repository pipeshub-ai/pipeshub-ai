'use client';

import React from 'react';
import useSWR from 'swr';
import { Button, DropdownMenu, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { AgentsApi } from '@/app/(main)/agents/api';
import { SWR_NS, SWR_POLICY } from '@/lib/api/swr-cache';
import type { AgentListRecord } from '@/app/(main)/agents/types';

/** How many agents the picker offers; a workspace with more falls back to search on /agents. */
const AGENT_PICKER_FETCH_LIMIT = 50;

function agentRouteId(agent: AgentListRecord): string | null {
  return agent.id || agent._key || null;
}

interface ProjectComposerAgentPickerProps {
  /** The selected agent's route id, or `null` for the general assistant (the default). */
  value: string | null;
  onChange: (agentId: string | null) => void;
}

/**
 * Agent chooser for the project workspace composer: pick one of the agents
 * you can use for a new chat in this project, or stay on the general
 * assistant. The chosen agent rides along in the hand-off URL
 * (`/chat/?projectId=…&agentId=…`), so the conversation is created with both
 * scopes — the project narrows what the agent sees.
 */
export function ProjectComposerAgentPicker({ value, onChange }: ProjectComposerAgentPickerProps) {
  const { t } = useTranslation();
  const { data, isLoading } = useSWR(
    [SWR_NS.agentLists, 'project-composer', { limit: AGENT_PICKER_FETCH_LIMIT }] as const,
    // The picker degrades to the assistant; a background refresh must not toast.
    ([, , { limit }]) => AgentsApi.getAgents({ page: 1, limit }, { suppressErrorToast: true }),
    SWR_POLICY.list60s,
  );
  const agents: AgentListRecord[] = data?.agents ?? [];
  const selected = value ? agents.find((agent) => agentRouteId(agent) === value) : undefined;

  const assistantLabel = t('chat.projects.workspace.assistantDefault', {
    defaultValue: 'General assistant',
  });

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger disabled={isLoading}>
        <Button
          type="button"
          variant="soft"
          color="gray"
          size="1"
          style={{ border: '1px solid var(--olive-3)', borderRadius: 'var(--radius-2)', background: 'var(--olive-2)' }}
        >
          <Flex align="center" gap="2" style={{ maxWidth: 260 }}>
            <MaterialIcon name="smart_toy" size={14} color="var(--slate-10)" />
            <Text
              size="1"
              weight="medium"
              style={{
                color: 'var(--slate-11)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {selected ? (selected.name?.trim() || assistantLabel) : assistantLabel}
            </Text>
            <MaterialIcon name="expand_more" size={14} color="var(--slate-10)" />
          </Flex>
        </Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Content variant="soft" style={{ maxHeight: 320, overflowY: 'auto' }}>
        <DropdownMenu.Item onSelect={() => onChange(null)}>
          <Flex align="center" gap="2">
            <MaterialIcon name="chat_bubble_outline" size={16} color={value ? 'var(--slate-10)' : 'var(--accent-9)'} />
            <Text size="2">{assistantLabel}</Text>
          </Flex>
        </DropdownMenu.Item>
        {agents.length > 0 && <DropdownMenu.Separator />}
        {agents.map((agent) => {
          const id = agentRouteId(agent);
          if (!id) return null;
          const isSelected = value === id;
          return (
            <DropdownMenu.Item key={id} onSelect={() => onChange(id)}>
              <Flex align="center" gap="2" style={{ minWidth: 0 }}>
                <MaterialIcon name="smart_toy" size={16} color={isSelected ? 'var(--accent-9)' : 'var(--slate-10)'} />
                <Text
                  size="2"
                  style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                >
                  {agent.name?.trim() || id}
                </Text>
              </Flex>
            </DropdownMenu.Item>
          );
        })}
        {agents.length === 0 && !isLoading && (
          <DropdownMenu.Item disabled>
            <Text size="2" style={{ color: 'var(--slate-10)' }}>
              {t('chat.projects.workspace.noAgentsAvailable', { defaultValue: 'No agents available' })}
            </Text>
          </DropdownMenu.Item>
        )}
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
