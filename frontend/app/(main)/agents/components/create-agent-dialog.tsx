'use client';

import React, { useState, useCallback, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import {
  Box,
  Button,
  Callout,
  Dialog,
  Flex,
  RadioGroup,
  Text,
  TextField,
  VisuallyHidden,
} from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { AgentsApi } from '@/app/(main)/agents/api';
import { ChatApi } from '@/app/(main)/chat/api';
import type { AvailableLlmModel } from '@/app/(main)/chat/types';
import { ServiceAccountConfirmDialog } from '@/app/(main)/agents/agent-builder/components/service-account-confirm-dialog';

type AgentType = 'user' | 'service';

export interface CreateAgentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function buildModels(model: AvailableLlmModel | null) {
  if (!model) return [];
  return [
    {
      provider: model.provider,
      modelName: model.modelName,
      modelKey: model.modelKey,
      isReasoning: model.isReasoning,
    },
  ];
}

function extractApiError(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail.trim();
  if (e instanceof Error && e.message) return e.message;
  return fallback;
}

export function CreateAgentDialog({ open, onOpenChange }: CreateAgentDialogProps) {
  const router = useRouter();
  const { t } = useTranslation();

  const [agentName, setAgentName] = useState('');
  const [agentType, setAgentType] = useState<AgentType>('user');
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nameError, setNameError] = useState(false);

  const [showServiceConfirm, setShowServiceConfirm] = useState(false);
  const [serviceCreating, setServiceCreating] = useState(false);
  const [serviceError, setServiceError] = useState<string | null>(null);

  const [defaultModel, setDefaultModel] = useState<AvailableLlmModel | null>(null);

  useEffect(() => {
    if (!open) {
      setAgentName('');
      setAgentType('user');
      setCreating(false);
      setError(null);
      setNameError(false);
      setShowServiceConfirm(false);
      setServiceCreating(false);
      setServiceError(null);
      return;
    }
    ChatApi.fetchAvailableLlms()
      .then((models) => {
        const def = models.find((m) => m.isDefault) ?? models[0] ?? null;
        setDefaultModel(def);
      })
      .catch(() => setDefaultModel(null));
  }, [open]);

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next && (creating || serviceCreating)) return;
      onOpenChange(next);
    },
    [creating, serviceCreating, onOpenChange]
  );

  const handleCreate = useCallback(async () => {
    const trimmedName = agentName.trim();
    if (!trimmedName) {
      setNameError(true);
      return;
    }
    setNameError(false);
    setError(null);

    if (agentType === 'service') {
      setShowServiceConfirm(true);
      return;
    }

    setCreating(true);
    try {
      const created = await AgentsApi.createAgent({
        name: trimmedName,
        description: '',
        startMessage: '',
        systemPrompt: '',
        models: buildModels(defaultModel),
        tags: [],
        shareWithOrg: false,
        isServiceAccount: false,
      });
      router.replace(`/agents/edit?agentKey=${encodeURIComponent(created._key)}`);
    } catch (e: unknown) {
      setError(extractApiError(e, t('agentBuilder.saveFailed')));
    } finally {
      setCreating(false);
    }
  }, [agentName, agentType, defaultModel, onOpenChange, router, t]);

  const handleServiceAccountConfirm = useCallback(async () => {
    const trimmedName = agentName.trim();
    if (!trimmedName) return;

    setServiceCreating(true);
    setServiceError(null);
    try {
      const created = await AgentsApi.createAgent({
        name: trimmedName,
        description: '',
        startMessage: '',
        systemPrompt: '',
        models: buildModels(defaultModel),
        tags: [],
        shareWithOrg: true,
        isServiceAccount: true,
      });
      router.replace(`/agents/edit?agentKey=${encodeURIComponent(created._key)}&sa=1`);
    } catch (e: unknown) {
      setServiceError(extractApiError(e, t('agentBuilder.svcAcctEnableFailed')));
    } finally {
      setServiceCreating(false);
    }
  }, [agentName, defaultModel, router, t]);

  const mainDialogOpen = open && !showServiceConfirm;

  return (
    <>
      <Dialog.Root open={mainDialogOpen} onOpenChange={handleOpenChange}>
        {mainDialogOpen && (
          <Box
            style={{
              position: 'fixed',
              inset: 0,
              backgroundColor: 'rgba(28, 32, 36, 0.5)',
              zIndex: 999,
              cursor: 'default',
            }}
          />
        )}
        <Dialog.Content
          style={{
            maxWidth: '28rem',
            width: '100%',
            padding: 'var(--space-5)',
            zIndex: 1000,
          }}
          onInteractOutside={(e) => e.preventDefault()}
          onEscapeKeyDown={(e) => e.preventDefault()}
        >
          <VisuallyHidden>
            <Dialog.Title>{t('agentBuilder.createAgent')}</Dialog.Title>
          </VisuallyHidden>

          <Flex direction="column" gap="4">
            {/* Header */}
            <Flex align="center" gap="3">
              <Box
                style={{
                  width: 36,
                  height: 36,
                  borderRadius: 'var(--radius-2)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  background: 'var(--accent-3)',
                  border: '1px solid var(--accent-6)',
                  flexShrink: 0,
                }}
              >
                <MaterialIcon name="smart_toy" size={20} style={{ color: 'var(--accent-11)' }} />
              </Box>
              <Box>
                <Text
                  size="4"
                  weight="bold"
                  style={{ color: 'var(--olive-12)', lineHeight: 1.3, display: 'block' }}
                >
                  {t('agentBuilder.createAgent')}
                </Text>
                <Text size="2" style={{ color: 'var(--olive-11)', lineHeight: 1.4, display: 'block' }}>
                  {t('agentBuilder.createAgentSubtitle')}
                </Text>
              </Box>
            </Flex>

            {/* Agent name field */}
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--olive-12)' }}>
                {t('agentBuilder.agentName')}
              </Text>
              <TextField.Root
                placeholder={t('agentBuilder.agentNamePlaceholder')}
                value={agentName}
                onChange={(e) => {
                  setAgentName(e.target.value);
                  if (nameError) setNameError(false);
                  if (error) setError(null);
                }}
                disabled={creating}
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void handleCreate();
                }}
                style={nameError ? { outline: '2px solid var(--red-8)' } : {}}
              />
              {nameError && (
                <Text size="1" style={{ color: 'var(--red-11)' }}>
                  {t('agentBuilder.nameRequired')}
                </Text>
              )}
            </Flex>

            {/* Agent type radio group */}
            <Flex direction="column" gap="2">
              <Text size="2" weight="medium" style={{ color: 'var(--olive-12)' }}>
                {t('agentBuilder.agentType')}
              </Text>
              <RadioGroup.Root
                value={agentType}
                onValueChange={(v) => setAgentType(v as AgentType)}
                disabled={creating}
              >
                <Flex direction="column" gap="3">
                  <Box
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 10,
                      cursor: creating ? 'not-allowed' : 'pointer',
                      padding: 'var(--space-3)',
                      borderRadius: 'var(--radius-2)',
                      border: `1px solid ${agentType === 'user' ? 'var(--accent-7)' : 'var(--olive-4)'}`,
                      background: agentType === 'user' ? 'var(--accent-2)' : 'var(--olive-2)',
                      transition: 'border-color 0.15s, background 0.15s',
                    }}
                    onClick={() => !creating && setAgentType('user')}
                  >
                    <RadioGroup.Item value="user" style={{ marginTop: 2, flexShrink: 0 }} />
                    <Box
                      style={{
                        width: 32,
                        height: 32,
                        borderRadius: 'var(--radius-2)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        background: agentType === 'user' ? 'var(--accent-4)' : 'var(--olive-4)',
                        flexShrink: 0,
                      }}
                    >
                      <MaterialIcon name="person" size={18} style={{ color: agentType === 'user' ? 'var(--accent-11)' : 'var(--olive-11)' }} />
                    </Box>
                    <Flex direction="column" gap="1">
                      <Text size="2" weight="medium" style={{ color: 'var(--olive-12)', lineHeight: 1.35 }}>
                        {t('agentBuilder.userAgent')}
                      </Text>
                      <Text size="1" style={{ color: 'var(--olive-11)', lineHeight: 1.45 }}>
                        {t('agentBuilder.userAgentDesc')}
                      </Text>
                    </Flex>
                  </Box>

                  <Box
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 10,
                      cursor: creating ? 'not-allowed' : 'pointer',
                      padding: 'var(--space-3)',
                      borderRadius: 'var(--radius-2)',
                      border: `1px solid ${agentType === 'service' ? 'var(--accent-7)' : 'var(--olive-4)'}`,
                      background: agentType === 'service' ? 'var(--accent-2)' : 'var(--olive-2)',
                      transition: 'border-color 0.15s, background 0.15s',
                    }}
                    onClick={() => !creating && setAgentType('service')}
                  >
                    <RadioGroup.Item value="service" style={{ marginTop: 2, flexShrink: 0 }} />
                    <Box
                      style={{
                        width: 32,
                        height: 32,
                        borderRadius: 'var(--radius-2)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        background: agentType === 'service' ? 'var(--accent-4)' : 'var(--olive-4)',
                        flexShrink: 0,
                      }}
                    >
                      <MaterialIcon name="admin_panel_settings" size={18} style={{ color: agentType === 'service' ? 'var(--accent-11)' : 'var(--olive-11)' }} />
                    </Box>
                    <Flex direction="column" gap="1">
                      <Text size="2" weight="medium" style={{ color: 'var(--olive-12)', lineHeight: 1.35 }}>
                        {t('agentBuilder.serviceAgent')}
                      </Text>
                      <Text size="1" style={{ color: 'var(--olive-11)', lineHeight: 1.45 }}>
                        {t('agentBuilder.serviceAgentDesc')}
                      </Text>
                    </Flex>
                  </Box>
                </Flex>
              </RadioGroup.Root>
            </Flex>

            {/* Error callout */}
            {error && (
              <Callout.Root color="red" variant="soft" size="2">
                <Callout.Icon>
                  <MaterialIcon name="error" size={16} />
                </Callout.Icon>
                <Callout.Text size="2">{error}</Callout.Text>
              </Callout.Root>
            )}

            {/* Action buttons */}
            <Flex gap="2" justify="end">
              <Button
                type="button"
                variant="soft"
                color="gray"
                size="2"
                onClick={() => onOpenChange(false)}
                disabled={creating}
              >
                {t('action.cancel')}
              </Button>
              <LoadingButton
                type="button"
                size="2"
                color="jade"
                onClick={() => void handleCreate()}
                loading={creating}
                loadingLabel={t('action.creating')}
              >
                {agentType === 'service' ? t('common.continue') : t('agentBuilder.createAgent')}
              </LoadingButton>
            </Flex>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>

      <ServiceAccountConfirmDialog
        open={showServiceConfirm}
        agentName={agentName}
        creating={serviceCreating}
        error={serviceError}
        onClose={() => {
          setShowServiceConfirm(false);
          setServiceError(null);
        }}
        onConfirm={handleServiceAccountConfirm}
      />
    </>
  );
}
