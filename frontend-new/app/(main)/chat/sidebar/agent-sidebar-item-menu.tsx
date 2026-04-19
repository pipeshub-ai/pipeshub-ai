'use client';

import { useState } from 'react';
import { DropdownMenu, Flex, Text, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useTranslation } from 'react-i18next';
import type { ViewAgentTooltipVariant } from './agent-sidebar-row-access';

interface AgentSidebarItemMenuProps {
  isParentHovered: boolean;
  onOpenChange?: (open: boolean) => void;
  canEdit: boolean;
  canDelete: boolean;
  showViewAgent: boolean;
  viewAgentTooltipVariant?: ViewAgentTooltipVariant;
  onEdit: () => void;
  onView: () => void;
  onDelete: () => void;
}

/**
 * Meatball menu for an agent row in chat sidebars — View (read-only / locked builder), Edit, Delete.
 */
export function AgentSidebarItemMenu({
  isParentHovered,
  onOpenChange: onOpenChangeProp,
  canEdit,
  canDelete,
  showViewAgent,
  viewAgentTooltipVariant,
  onEdit,
  onView,
  onDelete,
}: AgentSidebarItemMenuProps) {
  const { t } = useTranslation();
  const viewTooltip = showViewAgent
    ? viewAgentTooltipVariant === 'service_account'
      ? t('chat.viewAgentTooltipServiceAccount')
      : t('chat.viewAgentTooltipIndividual')
    : '';
  const [isOpen, setIsOpen] = useState(false);
  const [isMeatballHovered, setIsMeatballHovered] = useState(false);
  const visible = isParentHovered || isOpen;

  const handleOpenChange = (open: boolean) => {
    setIsOpen(open);
    onOpenChangeProp?.(open);
  };

  if (!canEdit && !canDelete && !showViewAgent) return null;
  if (!visible) return null;

  return (
    <DropdownMenu.Root open={isOpen} onOpenChange={handleOpenChange} modal={false}>
      <DropdownMenu.Trigger>
        <button
          type="button"
          onMouseEnter={() => setIsMeatballHovered(true)}
          onMouseLeave={() => setIsMeatballHovered(false)}
          onClick={(e) => {
            e.stopPropagation();
          }}
          aria-label={t('chat.agentListRowMenuAria')}
          style={{
            appearance: 'none',
            border: 'none',
            background: isMeatballHovered ? 'var(--olive-5)' : 'transparent',
            borderRadius: 'var(--radius-1)',
            padding: 2,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            flexShrink: 0,
          }}
        >
          <MaterialIcon name="more_horiz" size={18} color="var(--slate-11)" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Content side="bottom" align="start" sideOffset={4} style={{ minWidth: 140 }}>
        {showViewAgent && (
          <DropdownMenu.Item
            onClick={(e) => {
              e.stopPropagation();
              onView();
            }}
          >
            <Tooltip content={viewTooltip} delayDuration={400}>
              <Flex align="center" gap="2" style={{ maxWidth: 280 }}>
                <MaterialIcon name="visibility" size={16} color="var(--slate-11)" />
                <Text size="2">{t('chat.viewAgent')}</Text>
              </Flex>
            </Tooltip>
          </DropdownMenu.Item>
        )}
        {canEdit && (
          <DropdownMenu.Item
            onClick={(e) => {
              e.stopPropagation();
              onEdit();
            }}
          >
            <Flex align="center" gap="2">
              <MaterialIcon name="edit" size={16} color="var(--slate-11)" />
              <Text size="2">{t('chat.editAgent')}</Text>
            </Flex>
          </DropdownMenu.Item>
        )}
        {canDelete && (
          <DropdownMenu.Item
            color="red"
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
          >
            <Flex align="center" gap="2">
              <MaterialIcon name="delete" size={16} color="var(--red-11)" />
              <Text size="2" style={{ color: 'var(--red-11)' }}>
                {t('chat.deleteAgent')}
              </Text>
            </Flex>
          </DropdownMenu.Item>
        )}
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
