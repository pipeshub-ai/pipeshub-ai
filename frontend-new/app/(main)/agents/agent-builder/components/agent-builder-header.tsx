'use client';

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Badge,
  Box,
  Flex,
  Text,
  TextField,
  Button,
  Switch,
  IconButton,
  Separator,
  Tooltip,
  DropdownMenu,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useTranslation } from 'react-i18next';

export function AgentBuilderHeader(props: {
  agentName: string;
  onAgentNameChange: (v: string) => void;
  saving: boolean;
  onSave: () => void;
  shareWithOrg: boolean;
  onShareWithOrgChange: (v: boolean) => void;
  /** When true, the flow and palette structure are read-only (e.g. existing agent with `can_edit` false). */
  isFlowStructureLocked: boolean;
  /** False when the opened agent exists and `can_edit` is false (save / convert disabled). */
  canPersist: boolean;
  isServiceAccount: boolean;
  editing: boolean;
  /** Open service-account confirmation (create or convert). */
  onEnableServiceAccount?: () => void;
  /** When editing, show meatball → delete (opens confirmation in parent). */
  canDeleteAgent?: boolean;
  onRequestDeleteAgent?: () => void;
}) {
  const router = useRouter();
  const { t } = useTranslation();
  const {
    agentName,
    onAgentNameChange,
    saving,
    onSave,
    shareWithOrg,
    onShareWithOrgChange,
    isFlowStructureLocked,
    canPersist,
    isServiceAccount,
    editing,
    onEnableServiceAccount,
    canDeleteAgent = false,
    onRequestDeleteAgent,
  } = props;

  const [agentMenuTriggerHovered, setAgentMenuTriggerHovered] = useState(false);

  return (
    <Flex
      align="center"
      justify="between"
      gap="4"
      px="4"
      py="3"
      style={{
        flexShrink: 0,
        borderBottom: '1px solid var(--gray-5)',
        background: 'var(--color-panel)',
        boxShadow: 'var(--shadow-1)',
      }}
    >
      <Flex align="center" gap="3" style={{ minWidth: 0, flex: 1 }}>
        <IconButton
          type="button"
          variant="ghost"
          color="gray"
          size="2"
          onClick={() => router.push('/chat')}
          aria-label={t('agentBuilder.goBack')}
        >
          <MaterialIcon name="chevron_left" size={22} color="var(--olive-11)" />
        </IconButton>
        <Separator orientation="vertical" size="2" style={{ height: 28 }} />
        <Box style={{ minWidth: 0, flex: 1, maxWidth: 560 }}>
          <Text
            size="1"
            weight="medium"
            mb="1"
            style={{ color: 'var(--olive-11)', textTransform: 'uppercase', letterSpacing: '0.05em' }}
          >
            {t('agentBuilder.agentName')}
          </Text>
          <Flex align="center" gap="3" style={{ width: '100%' }}>
            <Box style={{ flex: 1, minWidth: 0 }}>
              <TextField.Root
                value={agentName}
                onChange={(e) => onAgentNameChange(e.target.value)}
                placeholder={t('agentBuilder.agentNamePlaceholder')}
                disabled={isFlowStructureLocked}
                size="2"
                style={{ width: '100%' }}
              >
                <TextField.Slot side="left">
                  <MaterialIcon name="smart_toy" size={18} color="var(--olive-11)" />
                </TextField.Slot>
              </TextField.Root>
            </Box>
            {isServiceAccount ? (
              <Tooltip content={t('agentBuilder.serviceAccountBadgeTooltip')}>
                <Badge
                  size="2"
                  color="jade"
                  variant="soft"
                  style={{
                    flexShrink: 0,
                    cursor: 'default',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    whiteSpace: 'nowrap',
                  }}
                >
                  <MaterialIcon name="admin_panel_settings" size={16} style={{ color: 'var(--accent-11)' }} />
                  {t('agentBuilder.serviceAccountBadge')}
                </Badge>
              </Tooltip>
            ) : onEnableServiceAccount ? (
              <Tooltip
                content={
                  editing
                    ? t('agentBuilder.convertToServiceAccountTooltip')
                    : t('agentBuilder.createAsServiceAccountTooltip')
                }
              >
                <Button
                  type="button"
                  size="2"
                  variant="soft"
                  color="jade"
                  disabled={saving}
                  onClick={onEnableServiceAccount}
                  style={{ flexShrink: 0 }}
                >
                  <Flex align="center" gap="2">
                    <MaterialIcon name="smart_toy" size={18} />
                    {editing ? t('agentBuilder.convertToServiceAccount') : t('agentBuilder.createAsServiceAccount')}
                  </Flex>
                </Button>
              </Tooltip>
            ) : null}
          </Flex>
        </Box>
      </Flex>
      <Flex align="center" gap="4" style={{ flexShrink: 0 }}>
        {editing && canDeleteAgent && onRequestDeleteAgent ? (
          <DropdownMenu.Root modal={false}>
            <DropdownMenu.Trigger>
              <button
                type="button"
                aria-label={t('chat.agentListRowMenuAria')}
                onMouseEnter={() => setAgentMenuTriggerHovered(true)}
                onMouseLeave={() => setAgentMenuTriggerHovered(false)}
                style={{
                  appearance: 'none',
                  border: 'none',
                  background: agentMenuTriggerHovered ? 'var(--olive-5)' : 'transparent',
                  borderRadius: 'var(--radius-1)',
                  padding: 6,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer',
                  flexShrink: 0,
                }}
              >
                <MaterialIcon name="more_horiz" size={22} color="var(--olive-11)" />
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Content side="bottom" align="end" sideOffset={4} style={{ minWidth: 160 }}>
              <DropdownMenu.Item
                color="red"
                onClick={(e) => {
                  e.stopPropagation();
                  onRequestDeleteAgent();
                }}
              >
                <Flex align="center" gap="2">
                  <MaterialIcon name="delete" size={16} color="var(--red-11)" />
                  <Text size="2" style={{ color: 'var(--red-11)' }}>
                    {t('chat.deleteAgent')}
                  </Text>
                </Flex>
              </DropdownMenu.Item>
            </DropdownMenu.Content>
          </DropdownMenu.Root>
        ) : null}
        <Tooltip
          content={
            isServiceAccount
              ? t('agentBuilder.serviceAccountShareTooltip')
              : t('agentBuilder.shareWithOrgTooltip')
          }
        >
          <Flex
            align="center"
            gap="2"
            px="2"
            py="1"
            style={{
              borderRadius: 'var(--radius-2)',
              border: '1px solid var(--gray-5)',
              background: 'var(--gray-2)',
            }}
          >
            <MaterialIcon name="groups" size={18} color="var(--olive-11)" />
            <Text size="2" style={{ color: 'var(--olive-12)' }}>
              {t('agentBuilder.shareWithOrg')}
            </Text>
            <Switch
              checked={shareWithOrg || isServiceAccount}
              onCheckedChange={onShareWithOrgChange}
              disabled={isFlowStructureLocked || isServiceAccount}
            />
          </Flex>
        </Tooltip>
        <Button
          size="2"
          onClick={onSave}
          disabled={saving || !canPersist || !agentName.trim()}
          style={{ minWidth: 132 }}
        >
          <Flex align="center" gap="2">
            {saving ? (
              <MaterialIcon name="hourglass_empty" size={18} />
            ) : (
              <MaterialIcon name="save" size={18} />
            )}
            {saving ? t('agentBuilder.saving') : editing ? t('agentBuilder.saveChanges') : t('agentBuilder.createAgent')}
          </Flex>
        </Button>
      </Flex>
    </Flex>
  );
}
