'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { Flex, Text, TextField, Select, Button, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { WorkspaceRightPanel } from '@/app/(main)/workspace/components/workspace-right-panel';
import { FormField } from '@/app/(main)/workspace/components/form-field';
import { toast } from '@/lib/store/toast-store';
import { useBotsStore } from '../store';
import { BotsApi } from '../api';
import type { BotType, BotTypeInfo, SlackBotConfig } from '../types';

const DEFAULT_ASSISTANT_ID = '__default_assistant__';

// ========================================
// Bot type registry
// ========================================

const BOT_TYPES: BotTypeInfo[] = [
  { type: 'slack', label: 'Slack Bot Setup', icon: '/icons/connectors/slack.svg', enabled: true },
  { type: 'discord', label: 'Discord Bot Setup', icon: '/icons/connectors/discord.svg', enabled: false },
  { type: 'telegram', label: 'Telegram Bot Setup', icon: '/icons/connectors/telegram.svg', enabled: false },
  { type: 'github', label: 'GitHub Bot Setup', icon: '/icons/connectors/github.svg', enabled: false },
];

// ========================================
// Component
// ========================================

export function BotConfigPanel() {
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

  const _isEditMode = !!editingBotId;

  // Determine panel title and footer based on view
  const isPanelTypeSelector = panelView === 'type-selector';

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
      Documentation
    </Button>
  );

  return (
    <WorkspaceRightPanel
      open={panelOpen}
      onOpenChange={(open) => { if (!open) closePanel(); }}
      title="Bot Configuration"
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
          onSaved={async () => {
            try {
              const configs = await BotsApi.getSlackBotConfigs();
              setConfigs(configs);
            } catch {
              // Silently fail refresh — data was already saved
            }
            closePanel();
          }}
        />
      )}
    </WorkspaceRightPanel>
  );
}

// ========================================
// Type Selector View
// ========================================

function TypeSelectorView({ onSelectType }: { onSelectType: (type: BotType) => void }) {
  return (
    <Flex direction="column" gap="3">
      <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
        Select Bot Setup
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
        <Text size="1" style={{ color: 'var(--slate-9)' }}>Coming soon</Text>
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
}

function SlackBotFormView({ editingConfig, agents, onClose, onSaved }: SlackBotFormViewProps) {
  const isEditMode = !!editingConfig;

  const [name, setName] = useState('');
  const [botToken, setBotToken] = useState('');
  const [signingSecret, setSigningSecret] = useState('');
  const [agentId, setAgentId] = useState<string>(DEFAULT_ASSISTANT_ID);
  const [showBotToken, setShowBotToken] = useState(false);
  const [showSigningSecret, setShowSigningSecret] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

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
        toast.success('Slack Bot updated', {
          description: `Your slack bot ${name} has been updated.`,
        });
      } else {
        await BotsApi.createSlackBotConfig(payload);
        toast.success('Slack Bot created', {
          description: `Your slack bot ${name} is ready!`,
        });
      }

      onSaved();
    } catch {
      toast.error(isEditMode ? 'Failed to update bot' : 'Failed to create bot', {
        description: 'Please check your credentials and try again.',
      });
    } finally {
      setIsSaving(false);
    }
  }, [isValid, isSaving, name, botToken, signingSecret, agentId, isEditMode, editingConfig, onSaved]);

  const handleDelete = useCallback(async () => {
    if (!editingConfig || isDeleting) return;

    setIsDeleting(true);
    try {
      await BotsApi.deleteSlackBotConfig(editingConfig.id);
      toast.success('Slack Bot deleted', {
        description: `${editingConfig.name} has been removed.`,
      });
      onSaved();
    } catch {
      toast.error('Failed to delete bot');
    } finally {
      setIsDeleting(false);
      setShowDeleteConfirm(false);
    }
  }, [editingConfig, isDeleting, onSaved]);

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
            {isEditMode ? 'Edit Slack Bot' : 'New Slack Bot'}
          </Text>
        </Flex>

        <FormField label="Name">
          <TextField.Root
            size="2"
            placeholder="e.g. My Slack Bot"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </FormField>

        <FormField label="Bot Token">
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

        <FormField label="Signing Secret">
          <TextField.Root
            size="2"
            type={showSigningSecret ? 'text' : 'password'}
            placeholder="Enter signing secret"
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

        <FormField label="Agent">
          <Select.Root
            size="2"
            value={agentId}
            onValueChange={setAgentId}
          >
            <Select.Trigger placeholder="Select an agent" />
            <Select.Content>
              <Select.Item value={DEFAULT_ASSISTANT_ID}>Default Assistant</Select.Item>
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
            {showDeleteConfirm ? (
              <Flex direction="column" gap="2">
                <Text size="2" style={{ color: 'var(--red-a11)' }}>
                  Are you sure? This action cannot be undone.
                </Text>
                <Flex gap="2">
                  <Button
                    variant="solid"
                    color="red"
                    size="2"
                    onClick={handleDelete}
                    disabled={isDeleting}
                    style={{ cursor: isDeleting ? 'not-allowed' : 'pointer' }}
                  >
                    {isDeleting ? 'Deleting…' : 'Confirm Delete'}
                  </Button>
                  <Button
                    variant="outline"
                    color="gray"
                    size="2"
                    onClick={() => setShowDeleteConfirm(false)}
                    disabled={isDeleting}
                    style={{ cursor: isDeleting ? 'not-allowed' : 'pointer' }}
                  >
                    Cancel
                  </Button>
                </Flex>
              </Flex>
            ) : (
              <Button
                variant="outline"
                color="red"
                size="2"
                onClick={() => setShowDeleteConfirm(true)}
                style={{ cursor: 'pointer', alignSelf: 'flex-start' }}
              >
                <MaterialIcon name="delete" size={16} color="var(--red-a11)" />
                Delete Bot
              </Button>
            )}
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
          Cancel
        </Button>
        <Button
          variant="solid"
          size="2"
          onClick={handleSubmit}
          disabled={!isValid || isSaving}
          style={{
            cursor: !isValid || isSaving ? 'not-allowed' : 'pointer',
            backgroundColor: !isValid || isSaving ? 'var(--slate-6)' : 'var(--emerald-9)',
          }}
        >
          {isSaving ? 'Saving…' : isEditMode ? 'Save' : 'Create'}
        </Button>
      </Flex>
    </Flex>
  );
}
