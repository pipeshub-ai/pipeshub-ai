'use client';

import React, { useState, useEffect, useCallback, useContext } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text, TextField, Select, Button, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { WorkspaceRightPanel, WorkspaceRightPanelBodyPortalContext } from '@/app/(main)/workspace/components/workspace-right-panel';
import { FormField } from '@/app/(main)/workspace/components/form-field';
import { DestructiveTypedConfirmationDialog } from '@/app/(main)/workspace/components/destructive-typed-confirmation-dialog';
import { toast } from '@/lib/store/toast-store';
import { useBotsStore } from '../store';
import { BotsApi } from '../api';
import type { BotType, BotTypeInfo, SlackBotConfig } from '../types';

const DEFAULT_ASSISTANT_ID = '__default_assistant__';

// ========================================
// Component
// ========================================

export function BotConfigPanel() {
  const { t } = useTranslation();
  const {
    panelOpen,
    panelView,
    editingBotId,
    slackBotConfigs,
    agents,
    closePanel,
    setPanelView,
    setConfigs,
  } = useBotsStore();

  const editingConfig = editingBotId
    ? slackBotConfigs.find((c) => c.id === editingBotId) ?? null
    : null;

  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const isPanelTypeSelector = panelView === 'type-selector';

  const handleSaved = useCallback(async () => {
    try {
      const configs = await BotsApi.getSlackBotConfigs();
      setConfigs(configs);
    } catch {
      // Silently fail refresh — data was already saved
    }
    closePanel();
  }, [setConfigs, closePanel]);

  const handleDelete = useCallback(async () => {
    if (!editingConfig || isDeleting) return;

    setIsDeleting(true);
    try {
      await BotsApi.deleteSlackBotConfig(editingConfig.id);
      toast.success(t('workspace.bots.toasts.deleted'), {
        description: t('workspace.bots.toasts.deletedDescription', { name: editingConfig.name }),
      });
      await handleSaved();
    } catch {
      toast.error(t('workspace.bots.toasts.deleteError'));
    } finally {
      setIsDeleting(false);
      setShowDeleteDialog(false);
    }
  }, [editingConfig, isDeleting, handleSaved, t]);

  const headerIcon = (
    <MaterialIcon name="smart_toy" size={20} color="var(--slate-12)" />
  );

  const documentationAction = (
    <Button
      variant="outline"
      color="gray"
      size="1"
      style={{ cursor: 'pointer', gap: 4 }}
      onClick={() => window.open('https://docs.pipeshub.com/integrations', '_blank')}
    >
      <MaterialIcon name="open_in_new" size={14} color="var(--slate-11)" />
      {t('workspace.bots.documentation')}
    </Button>
  );

  return (
    <>
      <WorkspaceRightPanel
        open={panelOpen}
        onOpenChange={(open) => { if (!open) closePanel(); }}
        title={t('workspace.bots.configPanelTitle')}
        icon={headerIcon}
        headerActions={documentationAction}
        hideFooter
      >
        {isPanelTypeSelector ? (
          <TypeSelectorView
            onSelectType={(type) => {
              if (type === 'slack') {
                setPanelView('slack-form');
              }
            }}
          />
        ) : (
          <SlackBotFormView
            editingConfig={editingConfig}
            agents={agents}
            onClose={closePanel}
            onSaved={handleSaved}
            onRequestDelete={() => setShowDeleteDialog(true)}
          />
        )}
      </WorkspaceRightPanel>

      <DestructiveTypedConfirmationDialog
        open={showDeleteDialog}
        onOpenChange={setShowDeleteDialog}
        heading={t('workspace.bots.configPanel.deleteBot')}
        body={
          <>
            <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
              {t('workspace.bots.form.deleteBotBodyLine1', { name: editingConfig?.name })}
            </Text>
            <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: '20px' }}>
              {t('workspace.bots.form.deleteBotBodyLine2')}
            </Text>
          </>
        }
        confirmationKeyword={editingConfig?.name ?? ''}
        confirmInputLabel={t('workspace.bots.form.typeNameToConfirm', { name: editingConfig?.name })}
        primaryButtonText={t('workspace.bots.configPanel.deleteBot')}
        cancelLabel={t('action.cancel')}
        onConfirm={handleDelete}
        isLoading={isDeleting}
        confirmLoadingLabel={t('workspace.bots.deleting')}
      />
    </>
  );
}

// ========================================
// Type Selector View
// ========================================

function TypeSelectorView({ onSelectType }: { onSelectType: (type: BotType) => void }) {
  const { t } = useTranslation();

  const BOT_TYPES: BotTypeInfo[] = [
    { type: 'slack', label: t('workspace.bots.typeLabels.slack'), icon: '/icons/connectors/slack.svg', enabled: true },
    { type: 'discord', label: t('workspace.bots.typeLabels.discord'), icon: '/icons/connectors/discord.svg', enabled: false },
    { type: 'telegram', label: t('workspace.bots.typeLabels.telegram'), icon: '/icons/connectors/telegram.svg', enabled: false },
    { type: 'github', label: t('workspace.bots.typeLabels.github'), icon: '/icons/connectors/github.svg', enabled: false },
  ];

  return (
    <Flex direction="column" gap="3">
      <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
        {t('workspace.bots.selectBotSetup')}
      </Text>

      <Flex direction="column" gap="1">
        {BOT_TYPES.map((bot) => (
          <BotTypeRow
            key={bot.type}
            bot={bot}
            onClick={() => bot.enabled && onSelectType(bot.type)}
          />
        ))}
      </Flex>
    </Flex>
  );
}

function BotTypeRow({ bot, onClick }: { bot: BotTypeInfo; onClick: () => void }) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);
  const [iconError, setIconError] = useState(false);

  return (
    <Flex
      align="center"
      gap="3"
      onClick={bot.enabled ? onClick : undefined}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        padding: '10px 12px',
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered && bot.enabled ? 'var(--olive-3)' : 'transparent',
        cursor: bot.enabled ? 'pointer' : 'default',
        opacity: bot.enabled ? 1 : 0.5,
        transition: 'background-color 150ms ease',
      }}
    >
      {/* Icon */}
      <Flex
        align="center"
        justify="center"
        style={{ width: 24, height: 24, flexShrink: 0 }}
      >
        {iconError ? (
          <MaterialIcon name="smart_toy" size={20} color="var(--gray-9)" />
        ) : (
          <img
            src={bot.icon}
            alt={bot.label}
            width={20}
            height={20}
            onError={() => setIconError(true)}
            style={{ display: 'block', objectFit: 'contain' }}
          />
        )}
      </Flex>

      {/* Label */}
      <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', flex: 1 }}>
        {bot.label}
      </Text>

      {/* Chevron or "Coming soon" */}
      {bot.enabled ? (
        <MaterialIcon name="chevron_right" size={18} color="var(--slate-9)" />
      ) : (
        <Text size="1" style={{ color: 'var(--slate-9)' }}>{t('workspace.bots.comingSoon')}</Text>
      )}
    </Flex>
  );
}

// ========================================
// Slack Bot Form View
// ========================================

interface SlackBotFormViewProps {
  editingConfig: SlackBotConfig | null;
  agents: { id: string; name: string }[];
  onClose: () => void;
  onSaved: () => void;
  onRequestDelete: () => void;
}

function SlackBotFormView({ editingConfig, agents, onClose, onSaved, onRequestDelete }: SlackBotFormViewProps) {
  const { t } = useTranslation();
  const panelBodyPortal = useContext(WorkspaceRightPanelBodyPortalContext);
  const isEditMode = !!editingConfig;

  const [name, setName] = useState('');
  const [botToken, setBotToken] = useState('');
  const [signingSecret, setSigningSecret] = useState('');
  const [agentId, setAgentId] = useState<string>(DEFAULT_ASSISTANT_ID);
  const [showBotToken, setShowBotToken] = useState(false);
  const [showSigningSecret, setShowSigningSecret] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  // Pre-fill when editing
  useEffect(() => {
    if (editingConfig) {
      setName(editingConfig.name);
      setBotToken(editingConfig.botToken);
      setSigningSecret(editingConfig.signingSecret);
      setAgentId(editingConfig.agentId || DEFAULT_ASSISTANT_ID);
    } else {
      setName('');
      setBotToken('');
      setSigningSecret('');
      setAgentId(DEFAULT_ASSISTANT_ID);
    }
  }, [editingConfig]);

  const isValid = name.trim().length > 0 && botToken.trim().length > 0 && signingSecret.trim().length > 0;

  const handleSubmit = useCallback(async () => {
    if (!isValid || isSaving) return;

    setIsSaving(true);
    try {
      const isDefaultAssistant = agentId === DEFAULT_ASSISTANT_ID;
      const payload = {
        name: name.trim(),
        botToken: botToken.trim(),
        signingSecret: signingSecret.trim(),
        ...(!isDefaultAssistant ? { agentId } : {}),
      };

      if (isEditMode && editingConfig) {
        await BotsApi.updateSlackBotConfig(editingConfig.id, payload);
        toast.success(t('workspace.bots.toasts.updated'), {
          description: t('workspace.bots.toasts.updatedDescription', { name }),
        });
      } else {
        await BotsApi.createSlackBotConfig(payload);
        toast.success(t('workspace.bots.toasts.created'), {
          description: t('workspace.bots.toasts.createdDescription', { name }),
        });
      }

      onSaved();
    } catch {
      toast.error(isEditMode ? t('workspace.bots.toasts.updateError') : t('workspace.bots.toasts.createError'), {
        description: t('workspace.bots.toasts.credentialsError'),
      });
    } finally {
      setIsSaving(false);
    }
  }, [isValid, isSaving, name, botToken, signingSecret, agentId, isEditMode, editingConfig, onSaved]);

  return (
    <Flex direction="column" style={{ height: '100%' }}>
      {/* ── Form fields ── */}
      <Flex direction="column" gap="4" style={{ flex: 1 }}>
        <Flex align="center" gap="2" style={{ marginBottom: 4 }}>
          <img
            src="/icons/connectors/slack.svg"
            alt="Slack"
            width={20}
            height={20}
            style={{ display: 'block' }}
          />
          <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {isEditMode ? t('workspace.bots.form.editTitle') : t('workspace.bots.form.newTitle')}
          </Text>
        </Flex>

        <FormField label={t('form.name')}>
          <TextField.Root
            size="2"
            placeholder={t('workspace.bots.form.namePlaceholder')}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </FormField>

        <FormField label={t('workspace.bots.form.botToken')}>
          <TextField.Root
            size="2"
            type={showBotToken ? 'text' : 'password'}
            placeholder="xoxb-..."
            value={botToken}
            onChange={(e) => setBotToken(e.target.value)}
          >
            <TextField.Slot side="right">
              <IconButton
                variant="ghost"
                color="gray"
                size="1"
                onClick={() => setShowBotToken((v) => !v)}
                style={{ cursor: 'pointer' }}
              >
                <MaterialIcon
                  name={showBotToken ? 'visibility_off' : 'visibility'}
                  size={16}
                  color="var(--gray-10)"
                />
              </IconButton>
            </TextField.Slot>
          </TextField.Root>
        </FormField>

        <FormField label={t('workspace.bots.form.signingSecret')}>
          <TextField.Root
            size="2"
            type={showSigningSecret ? 'text' : 'password'}
            placeholder={t('workspace.bots.form.signingSecretPlaceholder')}
            value={signingSecret}
            onChange={(e) => setSigningSecret(e.target.value)}
          >
            <TextField.Slot side="right">
              <IconButton
                variant="ghost"
                color="gray"
                size="1"
                onClick={() => setShowSigningSecret((v) => !v)}
                style={{ cursor: 'pointer' }}
              >
                <MaterialIcon
                  name={showSigningSecret ? 'visibility_off' : 'visibility'}
                  size={16}
                  color="var(--gray-10)"
                />
              </IconButton>
            </TextField.Slot>
          </TextField.Root>
        </FormField>

        <FormField label={t('workspace.bots.form.agent')}>
          <Select.Root
            size="2"
            value={agentId}
            onValueChange={setAgentId}
          >
            <Select.Trigger style={{ width: '100%', height: 32 }} placeholder={t('workspace.bots.form.agentPlaceholder')} />
            <Select.Content
              position="popper"
              container={panelBodyPortal ?? undefined}
            >
              <Select.Item value={DEFAULT_ASSISTANT_ID}>{t('workspace.bots.defaultAssistant')}</Select.Item>
              {agents.map((agent) => (
                <Select.Item key={agent.id} value={agent.id}>
                  {agent.name}
                </Select.Item>
              ))}
            </Select.Content>
          </Select.Root>
        </FormField>

        {/* ── Delete zone (edit mode only) ── */}
        {isEditMode && (
          <Flex
            direction="column"
            gap="2"
            style={{
              marginTop: 16,
              paddingTop: 16,
              borderTop: '1px solid var(--olive-3)',
            }}
          >
            <Button
              variant="outline"
              color="red"
              size="2"
              onClick={onRequestDelete}
              style={{ cursor: 'pointer', alignSelf: 'flex-start' }}
            >
              <MaterialIcon name="delete" size={16} color="var(--red-a11)" />
              {t('workspace.bots.configPanel.deleteBot')}
            </Button>
          </Flex>
        )}
      </Flex>

      {/* ── Footer ── */}
      <Flex
        align="center"
        justify="end"
        gap="2"
        style={{
          paddingTop: 16,
          marginTop: 16,
          borderTop: '1px solid var(--olive-3)',
        }}
      >
        <Button
          variant="outline"
          color="gray"
          size="2"
          onClick={onClose}
          disabled={isSaving}
          style={{ cursor: isSaving ? 'not-allowed' : 'pointer' }}
        >
          {t('action.cancel')}
        </Button>
        <LoadingButton
          variant="solid"
          size="2"
          onClick={handleSubmit}
          disabled={!isValid}
          loading={isSaving}
          loadingLabel={t('workspace.bots.saving')}
          style={{
            backgroundColor: !isValid ? 'var(--slate-6)' : 'var(--emerald-9)',
          }}
        >
          {isEditMode ? t('action.save') : t('action.create')}
        </LoadingButton>
      </Flex>
    </Flex>
  );
}