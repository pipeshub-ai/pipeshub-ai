'use client';

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { McpMyServerEntry } from '../../types';
import { MCP_TRANSPORT_LABELS } from '../../types';

interface McpPersonalServerCardProps {
  instance: McpMyServerEntry;
  isBusy: boolean;
  onAuthenticate: () => void;
  onReauthenticate: () => void;
  onRemoveCredentials: () => void;
}

export function McpPersonalServerCard({
  instance,
  isBusy,
  onAuthenticate,
  onReauthenticate,
  onRemoveCredentials,
}: McpPersonalServerCardProps) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);

  const managedByAdmin = instance.useAdminAuth;
  const needsAuth = instance.authMode !== 'none' && !managedByAdmin;

  return (
    <Flex
      direction="column"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      gap="3"
      style={{
        width: '100%',
        height: '100%',
        minWidth: 0,
        backgroundColor: isHovered ? 'var(--olive-3)' : 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 'var(--space-3)',
        transition: 'background-color 150ms ease',
      }}
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
        <StatusBadge instance={instance} />
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
        {instance.isAuthenticated && instance.tools.length > 0 && (
          <Text size="1" style={{ color: 'var(--gray-9)' }}>
            {t('workspace.mcpServers.toolsCount', { count: instance.tools.length })}
          </Text>
        )}
        {instance.toolsError && (
          <Text size="1" style={{ color: 'var(--red-11)' }}>
            {instance.toolsError}
          </Text>
        )}
      </Flex>

      {needsAuth && (
        <Flex
          align="center"
          gap="2"
          wrap="wrap"
          style={{ width: '100%', minWidth: 0, marginTop: 'auto', flexShrink: 0 }}
        >
          {instance.isAuthenticated ? (
            <>
              <ActionButton
                icon="autorenew"
                label={t('workspace.mcpServers.cta.reauthenticate')}
                onClick={onReauthenticate}
                disabled={isBusy}
              />
              <ActionButton
                icon="link_off"
                label={t('workspace.mcpServers.cta.disconnect')}
                danger
                onClick={onRemoveCredentials}
                disabled={isBusy}
              />
            </>
          ) : (
            <ActionButton
              icon="link"
              label={t('workspace.mcpServers.cta.connect')}
              onClick={onAuthenticate}
              disabled={isBusy}
            />
          )}
        </Flex>
      )}
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
  disabled,
}: {
  icon: string;
  label: string;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
}) {
  const [isHovered, setIsHovered] = useState(false);
  const color = danger ? 'var(--red-11)' : 'var(--accent-11)';
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
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
        flex: '1 1 8rem',
        minWidth: 0,
        height: 'var(--space-6)',
        padding: '0 var(--space-2)',
        overflow: 'hidden',
        borderRadius: 'var(--radius-2)',
        opacity: disabled ? 0.6 : 1,
        backgroundColor: isHovered && !disabled
          ? danger
            ? 'var(--red-a4)'
            : 'var(--accent-a4)'
          : danger
            ? 'var(--red-a3)'
            : 'var(--accent-a3)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <MaterialIcon name={icon} size={16} color={color} style={{ flexShrink: 0 }} />
      <span
        style={{
          fontSize: 14,
          fontWeight: 500,
          lineHeight: '20px',
          color,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          minWidth: 0,
        }}
      >
        {label}
      </span>
    </button>
  );
}
