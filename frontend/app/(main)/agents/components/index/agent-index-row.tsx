'use client';

import type { CSSProperties, KeyboardEvent, MouseEvent } from 'react';
import { Box, Flex, IconButton, Text } from '@radix-ui/themes';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { AgentRowMenu, useAgentRowActions } from '@/app/(main)/chat/sidebar/agent-row-menu';
import { buildChatHref } from '@/chat/build-chat-url';
import { formatRelativeTime } from '@/lib/utils/formatters';
import type { AgentListRecord } from '../../types';

/**
 * The index's columns: Agent, Owner, Updated, then the ⋯. The Agent column stops growing at 640px and
 * the ⋯ column takes what is left, so on a wide page the owner and date stay beside the name and the
 * ⋯ stays at the row's end. Phones drop the date, as the other index pages do. `minWidth` is the
 * grid's floor (columns plus the row's inline padding); below it the table scrolls sideways as one piece.
 */
export const agentIndexGrid = (phone: boolean) =>
  phone
    ? { columns: 'minmax(160px, 1fr) 110px var(--space-7)', minWidth: 160 + 110 + 40 + 16 }
    : {
        columns: 'minmax(160px, calc(var(--space-9) * 10)) 110px 96px minmax(var(--space-7), 1fr)',
        minWidth: 160 + 110 + 96 + 40 + 16,
      };

export const agentKeyOf = (agent: AgentListRecord): string => agent.id || agent._key;

const CELL: CSSProperties = {
  padding: '0 var(--space-2)',
  display: 'flex',
  alignItems: 'center',
  minWidth: 0,
};
const CLIP: CSSProperties = { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '100%' };

interface AgentIndexRowProps {
  agent: AgentListRecord;
  /** "You", the owner's name, or empty when the owner is unknown. */
  owner: string;
  onDeleted: (agentId: string) => void;
}

/**
 * One agent in the index. The whole row opens the agent's chat, as its sidebar row does, and it
 * holds its own ⋯ menu, so it is a role="button" grid row rather than a <button>.
 */
export function AgentIndexRow({ agent, owner, onDeleted }: AgentIndexRowProps) {
  const router = useRouter();
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const id = agentKeyOf(agent);
  const name = agent.name?.trim() || id;
  const description = agent.description?.trim();
  // The handle leads the second line: it is how the agent is used, and two agents may share a name.
  const handle = agent.handle?.trim();
  const detail = [handle ? `@${handle}` : '', description ?? ''].filter(Boolean).join(' · ');
  const actions = useAgentRowActions({ agent, label: name, onDeleted });

  const open = () => router.push(buildChatHref({ agentId: id }));

  const handleClick = (e: MouseEvent<HTMLDivElement>) => {
    const target = e.target as Element;
    // The menu is portalled: its clicks reach this row through the React tree, from outside its DOM.
    if (!e.currentTarget.contains(target)) return;
    if (target.closest('button, [role="menuitem"], input')) return;
    open();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    // Keys pressed on the ⋯ or inside its menu belong to them.
    if (e.target !== e.currentTarget) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      open();
    }
  };

  return (
    <>
      <Box
        role="button"
        tabIndex={0}
        className="sidebar-row-hoverable"
        onClick={handleClick}
        onKeyDown={handleKeyDown}
        style={{
          display: 'grid',
          gridTemplateColumns: agentIndexGrid(isMobile).columns,
          alignItems: 'center',
          padding: 'var(--space-3) var(--space-2)',
          borderBottom: '1px solid var(--border-subtle)',
          cursor: 'pointer',
        }}
      >
        {/* The agent glyph before the name, as an artifact's file glyph leads its row: 20px, 12px before
            the text, centred on both lines. Decoration, so the row's name stays the agent's. */}
        <Flex align="center" gap="3" style={CELL}>
          <span aria-hidden="true" style={{ display: 'inline-flex', flexShrink: 0 }}>
            <MaterialIcon name="smart_toy" size={20} color="var(--text-tertiary)" />
          </span>
          {/* Two lines tall with or without a description, so every row is the same height. */}
          <Flex
            direction="column"
            justify="center"
            style={{
              minWidth: 0,
              alignItems: 'flex-start',
              rowGap: 'calc(var(--space-1) / 2)',
              minHeight: 'calc(var(--line-height-2) + var(--line-height-1) + var(--space-1) / 2)',
              overflow: 'hidden',
            }}
          >
            <Text size="2" weight="medium" style={{ ...CLIP, color: 'var(--text-primary)' }}>
              {name}
            </Text>
            {detail && (
              // The column stops at 640px, so a long description can be read in full on hover.
              <Text size="1" title={detail} style={{ ...CLIP, color: 'var(--text-secondary)' }}>
                {detail}
              </Text>
            )}
          </Flex>
        </Flex>

        {/* Facts about the agent, all in the secondary role: "You" reads like anyone else's name. */}
        <Box style={CELL}>
          <Text size="1" style={{ ...CLIP, color: 'var(--text-secondary)' }}>
            {owner}
          </Text>
        </Box>

        {!isMobile && (
          <Box style={CELL}>
            <Text size="1" style={{ ...CLIP, color: 'var(--text-secondary)' }}>
              {agent.updatedAtTimestamp ? formatRelativeTime(agent.updatedAtTimestamp) : ''}
            </Text>
          </Box>
        )}

        <Box style={{ ...CELL, justifyContent: 'flex-end' }}>
          <AgentRowMenu
            actions={actions}
            modal
            trigger={
              <IconButton
                size="1"
                variant="ghost"
                color="gray"
                className="ghost-box"
                aria-label={t('agents.index.rowActions', { name })}
                style={{ borderRadius: 'var(--radius-2)' }}
              >
                <MaterialIcon name="more_horiz" size={16} color="var(--text-secondary)" />
              </IconButton>
            }
          />
        </Box>
      </Box>
      {/* Outside the row, so typing DELETE and pressing Enter never opens the agent. */}
      {actions.dialog}
    </>
  );
}
