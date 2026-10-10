'use client';

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Flex, Text } from '@radix-ui/themes';
import { ConnectorIcon, resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { EntityRowActionMenu, type RowAction } from '../../../components';
import type { McpMyServerEntry } from '../../types';
import { MCP_TRANSPORT_LABELS, isPersonalMcpInstance } from '../../types';
import { isMcpInstanceDisabled, McpDisabledBadge } from '../../components';
import { McpStatusBadge, mcpConnectionState, usesSharedCredential } from '../../connection-state';

interface McpPersonalServerCardProps {
  instance: McpMyServerEntry;
  isBusy: boolean;
  onAuthenticate: () => void;
  onReauthenticate: () => void;
  onRemoveCredentials: () => void;
  /** The person's own approval rules for this server's tools (their assistant chats). */
  onToolApprovals?: () => void;
  /** Set for the caller's own personal servers only. */
  onEdit?: () => void;
  onDelete?: () => void;
}

export function McpPersonalServerCard({
  instance,
  isBusy,
  onAuthenticate,
  onReauthenticate,
  onRemoveCredentials,
  onToolApprovals,
  onEdit,
  onDelete,
}: McpPersonalServerCardProps) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);

  const needsAuth = instance.authMode !== 'none' && !usesSharedCredential(instance);
  const connectorType = resolveConnectorType(instance.typeId || instance.name);
  const state = mcpConnectionState(instance);
  const signInAction =
    state === 'needs_connect'
      ? { icon: 'link', label: t('workspace.mcpServers.cta.connect'), onClick: onAuthenticate }
      : state === 'needs_reconnect'
        ? { icon: 'autorenew', label: t('workspace.mcpServers.cta.reconnect'), onClick: onReauthenticate }
        : null;

  const actions: RowAction[] = [];
  if (needsAuth && instance.isAuthenticated) {
    actions.push(
      {
        icon: 'autorenew',
        label: t('workspace.mcpServers.cta.reauthenticate'),
        onClick: onReauthenticate,
        disabled: isBusy,
      },
      {
        icon: 'link_off',
        label: t('workspace.mcpServers.cta.disconnect'),
        variant: 'danger',
        onClick: onRemoveCredentials,
        disabled: isBusy,
      }
    );
  }
  if (onToolApprovals) {
    actions.push({ icon: 'rule', label: t('workspace.mcpServers.toolRules.open'), onClick: onToolApprovals, disabled: isBusy });
  }
  if (onEdit) {
    actions.push({ icon: 'edit', label: t('workspace.mcpServers.cta.edit'), onClick: onEdit, disabled: isBusy });
  }
  if (onDelete) {
    actions.push({
      icon: 'delete',
      label: t('workspace.mcpServers.cta.delete'),
      variant: 'danger',
      onClick: onDelete,
      disabled: isBusy,
    });
  }

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
          <ConnectorIcon type={connectorType} size={16} color="var(--gray-10)" />
        </Flex>
        <Flex align="center" gap="1" flexShrink="0">
          {isMcpInstanceDisabled(instance) ? (
            <McpDisabledBadge instance={instance} />
          ) : (
            <McpStatusBadge state={state} />
          )}
          {actions.length > 0 && <EntityRowActionMenu actions={actions} />}
        </Flex>
      </Flex>

      <Flex direction="column" gap="1" style={{ width: '100%' }}>
        <Flex align="center" gap="2" style={{ minWidth: 0 }}>
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {instance.name}
          </Text>
          {isPersonalMcpInstance(instance) && (
            <Badge size="1" color="gray" variant="soft" title={t('workspace.mcpServers.personal.yourServersDescription')} style={{ flexShrink: 0 }}>
              {t('workspace.mcpServers.personal.yours')}
            </Badge>
          )}
        </Flex>
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
          <Text
            size="1"
            style={{ color: 'var(--gray-9)' }}
            title={
              instance.toolsCachedAt
                ? t('workspace.mcpServers.toolsCachedAt', { time: new Date(instance.toolsCachedAt).toLocaleString() })
                : undefined
            }
          >
            {t('workspace.mcpServers.toolsCount', { count: instance.tools.length })}
          </Text>
        )}
        {instance.toolsError && (
          <Text
            size="1"
            title={instance.toolsError}
            style={{
              // A slow server still works in chat, so it isn't shown as a failure.
              color: state === 'slow' ? 'var(--gray-10)' : 'var(--red-11)',
              overflow: 'hidden',
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              wordBreak: 'break-word',
            }}
          >
            {instance.toolsError}
          </Text>
        )}
      </Flex>

      {signInAction && (
        <Flex align="center" style={{ width: '100%', minWidth: 0, marginTop: 'auto', flexShrink: 0 }}>
          <ActionButton
            icon={signInAction.icon}
            label={signInAction.label}
            onClick={signInAction.onClick}
            disabled={isBusy}
          />
        </Flex>
      )}
    </Flex>
  );
}

function ActionButton({
  icon,
  label,
  onClick,
  disabled,
}: {
  icon: string;
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  const [isHovered, setIsHovered] = useState(false);
  const color = 'var(--accent-11)';
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
        border: '1px solid var(--accent-a6)',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 6,
        maxWidth: '100%',
        height: 24,
        padding: '0 10px',
        overflow: 'hidden',
        borderRadius: 'var(--radius-2)',
        opacity: disabled ? 0.6 : 1,
        backgroundColor: isHovered && !disabled ? 'var(--accent-a4)' : 'var(--accent-a3)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <MaterialIcon name={icon} size={14} color={color} style={{ flexShrink: 0 }} />
      <span
        style={{
          fontSize: 12,
          fontWeight: 500,
          lineHeight: '16px',
          color,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {label}
      </span>
    </button>
  );
}
