'use client';

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { DropdownMenu, Flex, IconButton, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { connectionCta, connectionStatus, kindCounts, type ConnectionStatus } from '../toolset-form';
import type { ApiToolset } from '../types';

const STATUS_STYLE: Record<ConnectionStatus, { icon: string; color: string; background: string }> = {
  connected: { icon: 'check_circle', color: 'var(--green-a11)', background: 'var(--green-a3)' },
  open: { icon: 'public', color: 'var(--gray-11)', background: 'var(--gray-a3)' },
  notConnected: { icon: 'vpn_key', color: 'var(--amber-11)', background: 'var(--amber-a3)' },
};

interface ApiActionCardProps {
  toolset: ApiToolset;
  isAdmin: boolean;
  /** Opens the connection panel: the card body and its button. */
  onOpen: () => void;
  /** Admin only: the card gets a menu with these. */
  onEdit?: () => void;
  onDelete?: () => void;
}

/** An API action in the Actions catalog, laid out like the app action cards beside it. */
export function ApiActionCard({ toolset, isAdmin, onOpen, onEdit, onDelete }: ApiActionCardProps) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);
  const status = connectionStatus(toolset);
  const statusStyle = STATUS_STYLE[status];
  const counts = kindCounts(toolset.operations);

  return (
    <Flex
      direction="column"
      role="group"
      aria-label={toolset.name}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onClick={onOpen}
      style={{
        width: '100%',
        backgroundColor: isHovered ? 'var(--olive-3)' : 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 12,
        gap: 24,
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <Flex direction="column" gap="3" style={{ width: '100%', flex: 1 }}>
        <Flex align="start" justify="between" gap="2">
          <Flex
            align="center"
            justify="center"
            style={{
              width: 32,
              height: 32,
              backgroundColor: 'var(--accent-a3)',
              borderRadius: 'var(--radius-1)',
              flexShrink: 0,
            }}
          >
            <MaterialIcon name="api" size={18} color="var(--accent-11)" />
          </Flex>
          <Flex align="center" gap="1">
            <Flex
              align="center"
              gap="1"
              style={{ padding: '2px 8px', borderRadius: 'var(--radius-2)', backgroundColor: statusStyle.background }}
            >
              <MaterialIcon name={statusStyle.icon} size={12} color={statusStyle.color} />
              <Text size="1" weight="medium" style={{ color: statusStyle.color, whiteSpace: 'nowrap' }}>
                {t(`workspace.apiToolsets.status.${status}`)}
              </Text>
            </Flex>
            {onEdit || onDelete ? (
              <DropdownMenu.Root>
                <DropdownMenu.Trigger>
                  <IconButton
                    size="1"
                    variant="ghost"
                    color="gray"
                    aria-label={t('workspace.apiToolsets.cta.more', { name: toolset.name })}
                    onClick={(e) => e.stopPropagation()}
                    style={{ cursor: 'pointer', margin: 0 }}
                  >
                    <MaterialIcon name="more_horiz" size={18} color="var(--gray-11)" />
                  </IconButton>
                </DropdownMenu.Trigger>
                <DropdownMenu.Content size="1" align="end" onClick={(e) => e.stopPropagation()}>
                  {onEdit ? (
                    <DropdownMenu.Item onSelect={onEdit}>
                      <MaterialIcon name="edit" size={14} />
                      {t('workspace.apiToolsets.cta.edit')}
                    </DropdownMenu.Item>
                  ) : null}
                  {onDelete ? (
                    <DropdownMenu.Item color="red" onSelect={onDelete}>
                      <MaterialIcon name="delete" size={14} />
                      {t('workspace.apiToolsets.cta.delete')}
                    </DropdownMenu.Item>
                  ) : null}
                </DropdownMenu.Content>
              </DropdownMenu.Root>
            ) : null}
          </Flex>
        </Flex>

        <Flex direction="column" gap="1">
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)', overflowWrap: 'anywhere' }}>
            {toolset.name}
          </Text>
          <Text
            size="2"
            style={{
              color: 'var(--gray-11)',
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {toolset.description || (toolset.specTitle !== toolset.name ? toolset.specTitle : t('workspace.apiToolsets.card.fallbackDescription'))}
          </Text>
        </Flex>

        <Flex align="center" gap="3" wrap="wrap">
          <Text size="1" style={{ color: 'var(--gray-10)' }}>
            {t(`workspace.apiToolsets.authType.${toolset.authType}`)}
          </Text>
          <Text size="1" style={{ color: 'var(--gray-10)' }}>
            {(['read', 'write', 'destructive'] as const)
              .filter((kind) => counts[kind] > 0)
              .map((kind) => t(`workspace.apiToolsets.kindCount.${kind}`, { count: counts[kind] }))
              .join(' · ')}
          </Text>
        </Flex>
      </Flex>

      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onOpen();
        }}
        style={{
          appearance: 'none',
          margin: 0,
          width: '100%',
          height: 32,
          borderRadius: 'var(--radius-2)',
          border: '1px solid var(--gray-a6)',
          backgroundColor: 'var(--gray-a3)',
          color: 'var(--gray-12)',
          fontSize: 14,
          fontWeight: 500,
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 6,
        }}
      >
        <MaterialIcon name={status === 'notConnected' ? 'link' : 'tune'} size={16} color="currentColor" />
        {t(connectionCta(toolset, isAdmin))}
      </button>
    </Flex>
  );
}
