'use client';

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { isMcpInstanceReadOnly, McpInheritedBadge } from '@/config';
import type { McpMyServerEntry } from '../../types';
import { MCP_AUTH_MODE_LABELS, MCP_TRANSPORT_LABELS } from '../../types';

interface McpInstanceCardProps {
  instance: McpMyServerEntry;
  onEdit: () => void;
  onDelete: () => void;
}

export function McpInstanceCard({ instance, onEdit, onDelete }: McpInstanceCardProps) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Flex
      direction="column"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      gap="3"
      style={{
        width: '100%',
        backgroundColor: isHovered ? 'var(--olive-3)' : 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 'var(--space-3)',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
      onClick={onEdit}
    >
      <Flex align="center" justify="between" style={{ width: '100%' }}>
        <Flex
          align="center"
          justify="center"
          style={{
            width: 'var(--space-8)',
            height: 'var(--space-8)',
            padding: 'var(--space-2)',
            backgroundColor: 'var(--gray-a2)',
            borderRadius: 'var(--radius-1)',
            flexShrink: 0,
          }}
        >
          <MaterialIcon name={instance.isCustom ? 'dns' : 'hub'} size={16} color="var(--gray-10)" />
        </Flex>
        <Flex gap="1">
          {instance.isCustom && (
            <Badge color="gray" size="1">
              {t('workspace.mcpServers.custom')}
            </Badge>
          )}
          <McpInheritedBadge instance={instance} />
          <StatusBadge instance={instance} />
        </Flex>
      </Flex>

      <Flex direction="column" gap="1" style={{ width: '100%' }}>
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {instance.name}
        </Text>
        <Text
          size="1"
          style={{
            color: 'var(--gray-11)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            minHeight: 32,
          }}
        >
          {instance.description || MCP_TRANSPORT_LABELS[instance.transport]}
        </Text>
        <Flex align="center" gap="2" wrap="wrap" style={{ marginTop: 4 }}>
          <Text size="1" style={{ color: 'var(--gray-9)' }}>{MCP_TRANSPORT_LABELS[instance.transport]}</Text>
          <Text size="1" style={{ color: 'var(--gray-9)' }}>· {MCP_AUTH_MODE_LABELS[instance.authMode]}</Text>
          {instance.useAdminAuth && (
            <Text size="1" style={{ color: 'var(--gray-9)' }}>· {t('workspace.mcpServers.sharedAuth')}</Text>
          )}
        </Flex>
      </Flex>

      <Flex align="center" gap="2" style={{ width: '100%' }}>
        <ActionButton icon="settings" label={t('workspace.mcpServers.cta.manage')} onClick={onEdit} />
        {!isMcpInstanceReadOnly(instance) && (
          <ActionButton icon="delete" label={t('workspace.mcpServers.cta.delete')} danger onClick={onDelete} />
        )}
      </Flex>
    </Flex>
  );
}

function StatusBadge({ instance }: { instance: McpMyServerEntry }) {
  const { t } = useTranslation();
  if (instance.authMode === 'none' || instance.useAdminAuth || instance.isAuthenticated) {
    return (
      <Badge color="green" size="1">
        {t('workspace.mcpServers.status.ready')}
      </Badge>
    );
  }
  return (
    <Badge color="amber" size="1">
      {t('workspace.mcpServers.status.notConnected')}
    </Badge>
  );
}

function ActionButton({
  icon,
  label,
  onClick,
  danger,
}: {
  icon: string;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  const [isHovered, setIsHovered] = useState(false);
  const color = danger ? 'var(--red-11)' : 'var(--accent-11)';
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        appearance: 'none',
        margin: 0,
        font: 'inherit',
        outline: 'none',
        border: `1px solid ${danger ? 'var(--red-a6)' : 'var(--accent-a6)'}`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 'var(--space-2)',
        flex: 1,
        height: 'var(--space-6)',
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered
          ? danger
            ? 'var(--red-a4)'
            : 'var(--accent-a4)'
          : danger
            ? 'var(--red-a3)'
            : 'var(--accent-a3)',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <MaterialIcon name={icon} size={16} color={color} />
      <span style={{ fontSize: 14, fontWeight: 500, lineHeight: '20px', color }}>{label}</span>
    </button>
  );
}
