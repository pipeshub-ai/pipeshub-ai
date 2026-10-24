'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Avatar,
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
import { PermissionLockIcon } from '@/config';
import { useTranslation } from 'react-i18next';
import { UsersApi } from '@/app/(main)/workspace/users/api';
import { useIsBelowDesktop, useIsMobile } from '@/lib/hooks/use-is-mobile';
import { touchTarget } from '@/app/components/ui/responsive';

export function AgentBuilderHeader(props: {
  agentName: string;
  onAgentNameChange: (v: string) => void;
  /** Inline validation message for the name field (e.g. empty on save). */
  agentNameError?: string | null;
  agentNameInputRef?: React.RefObject<HTMLInputElement | null>;
  saving: boolean;
  onSave: () => void;
  /** Called when the back chevron is clicked; parent handles dirty-check logic. */
  onGoBack?: () => void;
  /** True when there are unsaved changes; disables the save button when false (edit mode only). */
  isDirty?: boolean;
  shareWithOrg: boolean;
  onShareWithOrgChange: (v: boolean) => void;
  /** When true, the flow and palette structure are read-only (e.g. existing agent with `can_edit` false). */
  isFlowStructureLocked: boolean;
  /** False when the opened agent exists and `can_edit` is false (save / convert disabled). */
  canPersist: boolean;
  /** True while deprecated tools remain on the flow canvas (blocks save). */
  saveBlockedByDeprecatedTools?: boolean;
  /** Any other reason save is blocked, shown as the save button's tooltip. */
  saveBlockedReason?: string;
  isServiceAccount: boolean;
  editing: boolean;
  /** An existing agent is still loading or failed to load: name, share, convert and save are off. */
  loading?: boolean;
  /** Open service-account confirmation (create or convert). */
  onEnableServiceAccount?: () => void;
  serviceAccountPermissionDenied?: boolean;
  /** When editing, show meatball → delete (opens confirmation in parent). */
  canDeleteAgent?: boolean;
  deletePermissionDenied?: boolean;
  onRequestDeleteAgent?: () => void;
  sharePermissionDenied?: boolean;
  /** MongoDB user ID from agent.createdBy — resolved via by-ids for display. */
  createdBy?: string | null;
  onShare?: () => void;
  hideShareControls?: boolean;
}) {
  const router = useRouter();
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const isBelowDesktop = useIsBelowDesktop();
  const {
    agentName,
    onAgentNameChange,
    agentNameError = null,
    agentNameInputRef,
    saving,
    onSave,
    onGoBack,
    isDirty = true,
    shareWithOrg,
    onShareWithOrgChange,
    isFlowStructureLocked,
    canPersist,
    saveBlockedByDeprecatedTools = false,
    saveBlockedReason,
    isServiceAccount,
    editing,
    loading = false,
    onEnableServiceAccount,
    serviceAccountPermissionDenied = false,
    canDeleteAgent = false,
    deletePermissionDenied = false,
    onRequestDeleteAgent,
    createdBy = null,
    onShare,
    hideShareControls = false,
    sharePermissionDenied = false,
  } = props;

  const [agentMenuTriggerHovered, setAgentMenuTriggerHovered] = useState(false);
  const [creatorName, setCreatorName] = useState<string | null>(null);
  const [creatorAvatarUrl, setCreatorAvatarUrl] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (!createdBy) {
      setCreatorName(null);
      setCreatorAvatarUrl(undefined);
      return;
    }
    let cancelled = false;
    UsersApi.getUsersByIds([createdBy])
      .then((users) => {
        if (cancelled) return;
        const user = users[0];
        setCreatorName(user?.name?.trim() || user?.email?.trim() || null);
        setCreatorAvatarUrl(user?.profilePicture ?? undefined);
      })
      .catch(() => {
        if (!cancelled) {
          setCreatorName(null);
          setCreatorAvatarUrl(undefined);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [createdBy]);

  const showDeleteOption = Boolean(editing && canDeleteAgent && onRequestDeleteAgent);
  const showConvertOption = Boolean(!isServiceAccount && onEnableServiceAccount);
  const convertLabel = editing
    ? t('agentBuilder.agentMenuConvertToServiceAgent')
    : t('agentBuilder.agentMenuCreateAsServiceAgent');
  // On phones the share control lives in the … menu; below desktop the service-agent action does too,
  // since the header row is too narrow for it beside the name field.
  const showShareInMenu = isMobile && !hideShareControls;
  const showConvertInMenu = isBelowDesktop && showConvertOption;
  const convertDisabled = saving || loading || serviceAccountPermissionDenied;
  const showAgentMenu = showDeleteOption || showShareInMenu || showConvertInMenu;

  return (
    <Flex
      align="center"
      justify="between"
      gap={isMobile ? '3' : '4'}
      wrap="wrap"
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
          onClick={() => (onGoBack ? onGoBack() : router.push('/chat'))}
          aria-label={t('agentBuilder.goBack')}
        >
          <MaterialIcon name="chevron_left" size={22} color="var(--olive-11)" />
        </IconButton>
        <Separator orientation="vertical" size="2" style={{ height: 28 }} />
        <Box style={isMobile ? { minWidth: 0, flex: 1 } : { flexShrink: 0 }}>
          <Text
            size="1"
            weight="medium"
            mb="1"
            style={{ color: 'var(--olive-11)', textTransform: 'uppercase', letterSpacing: '0.05em' }}
          >
            {t('agentBuilder.agentName')}
          </Text>
          <Flex align="center" gap="3">
            <Box style={{ minWidth: 0, flex: isMobile ? 1 : undefined }}>
              <TextField.Root
                ref={agentNameInputRef}
                value={agentName}
                onChange={(e) => onAgentNameChange(e.target.value)}
                placeholder={t('agentBuilder.agentNamePlaceholder')}
                disabled={isFlowStructureLocked || loading}
                size="2"
                aria-invalid={agentNameError ? true : undefined}
                color={agentNameError ? 'red' : undefined}
                style={{ width: isMobile ? '100%' : 200 }}
              >
                <TextField.Slot side="left">
                  <MaterialIcon name="smart_toy" size={18} color="var(--olive-11)" />
                </TextField.Slot>
              </TextField.Root>
              {agentNameError ? (
                <Text size="1" mt="1" style={{ color: 'var(--red-11)' }}>
                  {agentNameError}
                </Text>
              ) : null}
              {creatorName ? (
                <Flex align="center" gap="1" mt="1">
                  <Text size="1" style={{ color: 'var(--olive-10)' }}>
                    {t('agentBuilder.createdBy')}
                  </Text>
                  <Avatar
                    size="1"
                    fallback={creatorName.charAt(0).toUpperCase()}
                    src={creatorAvatarUrl}
                    radius="full"
                    style={{ width: 16, height: 16, flexShrink: 0 }}
                  />
                  <Text
                    size="1"
                    style={{
                      color: 'var(--olive-11)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      maxWidth: 160,
                    }}
                  >
                    {creatorName}
                  </Text>
                </Flex>
              ) : null}
            </Box>
            {isServiceAccount ? (
              <Tooltip content={t('agentBuilder.serviceAgentBadgeTooltip')}>
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
                  {t('agentBuilder.serviceAgentBadge')}
                </Badge>
              </Tooltip>
            ) : null}
          </Flex>
        </Box>
      </Flex>
      <Flex align="center" gap="4" style={{ flexShrink: 0, marginLeft: 'auto' }}>
        {showAgentMenu ? (
          <DropdownMenu.Root modal={false}>
            <DropdownMenu.Trigger>
              <button
                type="button"
                aria-label={t('agentBuilder.agentActionsMenuAria')}
                onClick={(e) => e.stopPropagation()}
                onMouseEnter={() => setAgentMenuTriggerHovered(true)}
                onMouseLeave={() => setAgentMenuTriggerHovered(false)}
                className="touch-hit"
                style={{
                  appearance: 'none',
                  border: 'none',
                  background: agentMenuTriggerHovered ? 'var(--olive-5)' : 'transparent',
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
            <DropdownMenu.Content side="bottom" align="end" sideOffset={4} style={{ minWidth: 140 }}>
              {showConvertInMenu ? (
                <DropdownMenu.Item
                  disabled={convertDisabled}
                  onClick={() => onEnableServiceAccount?.()}
                >
                  <Flex align="center" gap="2">
                    <MaterialIcon name="admin_panel_settings" size={16} />
                    <Text size="2">{convertLabel}</Text>
                    {serviceAccountPermissionDenied && <PermissionLockIcon />}
                  </Flex>
                </DropdownMenu.Item>
              ) : null}
              {showShareInMenu && onShare ? (
                <DropdownMenu.Item disabled={sharePermissionDenied} onClick={() => onShare()}>
                  <Flex align="center" gap="2">
                    <MaterialIcon name="person_add" size={16} />
                    <Text size="2">{t('agentBuilder.share')}</Text>
                    {sharePermissionDenied && <PermissionLockIcon />}
                  </Flex>
                </DropdownMenu.Item>
              ) : null}
              {showShareInMenu && !onShare ? (
                <DropdownMenu.CheckboxItem
                  checked={shareWithOrg || isServiceAccount}
                  onCheckedChange={onShareWithOrgChange}
                  disabled={isFlowStructureLocked || loading || isServiceAccount || sharePermissionDenied}
                >
                  <Flex align="center" gap="2">
                    <MaterialIcon name="groups" size={16} />
                    <Text size="2">{t('agentBuilder.shareWithOrg')}</Text>
                  </Flex>
                </DropdownMenu.CheckboxItem>
              ) : null}
              {showDeleteOption ? (
                <DropdownMenu.Item
                  color="red"
                  onClick={(e) => {
                    e.stopPropagation();
                    if (deletePermissionDenied) return;
                    onRequestDeleteAgent?.();
                  }}
                  disabled={deletePermissionDenied}
                >
                  <Flex align="center" gap="2">
                    <MaterialIcon name="delete" size={16} color="var(--red-11)" />
                    <Text size="2" style={{ color: 'var(--red-11)' }}>
                      {t('chat.deleteAgent')}
                    </Text>
                    {deletePermissionDenied && <PermissionLockIcon />}
                  </Flex>
                </DropdownMenu.Item>
              ) : null}
            </DropdownMenu.Content>
          </DropdownMenu.Root>
        ) : null}
        {!hideShareControls && !isMobile && (onShare ? (
          <Button
            variant="outline"
            size="2"
            disabled={sharePermissionDenied}
            onClick={() => {
              if (sharePermissionDenied) return;
              onShare();
            }}
            style={{ cursor: sharePermissionDenied ? 'not-allowed' : 'pointer' }}
          >
            <Flex align="center" gap="2">
              <MaterialIcon name="person_add" size={16} />
              <Text size="2">{t('agentBuilder.share')}</Text>
              {sharePermissionDenied && <PermissionLockIcon />}
            </Flex>
          </Button>
        ) : (
          <Tooltip
            content={
              isServiceAccount
                ? t('agentBuilder.serviceAgentShareTooltip')
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
                disabled={isFlowStructureLocked || loading || isServiceAccount || sharePermissionDenied}
              />
            </Flex>
          </Tooltip>
        ))}
        {showConvertOption && !showConvertInMenu ? (
          <Tooltip
            content={
              editing
                ? t('agentBuilder.convertToServiceAgentTooltip')
                : t('agentBuilder.convertToServiceAgentTooltipNewAgent')
            }
          >
            <Button
              type="button"
              variant="soft"
              color="jade"
              size="2"
              disabled={convertDisabled}
              onClick={() => {
                if (serviceAccountPermissionDenied) return;
                onEnableServiceAccount?.();
              }}
              style={{ flexShrink: 0, whiteSpace: 'nowrap' }}
            >
              <Flex align="center" gap="2">
                <MaterialIcon name="admin_panel_settings" size={16} />
                {convertLabel}
                {serviceAccountPermissionDenied && <PermissionLockIcon />}
              </Flex>
            </Button>
          </Tooltip>
        ) : null}
        {(() => {
          const isSaveDisabled =
            saving || loading || !canPersist || saveBlockedByDeprecatedTools || Boolean(saveBlockedReason) || (editing && !isDirty);
          const saveLabel = saving
            ? t('agentBuilder.saving')
            : editing
              ? t('agentBuilder.saveChanges')
              : t('agentBuilder.createAgent');
          const saveButton = (
            <Button
              size="2"
              onClick={onSave}
              disabled={isSaveDisabled}
              aria-label={isMobile ? saveLabel : undefined}
              style={isMobile ? touchTarget(true) : { minWidth: 132 }}
            >
              <Flex align="center" gap="2">
                {saving ? (
                  <MaterialIcon name="hourglass_empty" size={18} />
                ) : (
                  <MaterialIcon name="save" size={18} />
                )}
                {isMobile ? null : saveLabel}
              </Flex>
            </Button>
          );
          if (!saveBlockedByDeprecatedTools && !saveBlockedReason) return saveButton;
          return (
            <Tooltip content={saveBlockedByDeprecatedTools ? t('agentBuilder.removeDeprecatedTools') : saveBlockedReason}>
              <span
                style={{
                  display: 'inline-flex',
                  cursor: 'not-allowed',
                }}
              >
                {saveButton}
              </span>
            </Tooltip>
          );
        })()}
      </Flex>
    </Flex>
  );
}
