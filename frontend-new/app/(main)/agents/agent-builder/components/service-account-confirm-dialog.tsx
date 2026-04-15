'use client';

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Button,
  Callout,
  Checkbox,
  Dialog,
  Flex,
  IconButton,
  Separator,
  Text,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

export interface ServiceAccountConfirmDialogProps {
  open: boolean;
  agentName: string;
  creating: boolean;
  error: string | null;
  hasUserToolsets: boolean;
  isConverting?: boolean;
  onClose: () => void;
  onConfirm: () => void | Promise<void>;
}

export function ServiceAccountConfirmDialog({
  open,
  agentName,
  creating,
  error,
  hasUserToolsets,
  isConverting = false,
  onClose,
  onConfirm,
}: ServiceAccountConfirmDialogProps) {
  const { t } = useTranslation();

  const title = isConverting ? t('agentBuilder.svcAcctConvertTitle') : t('agentBuilder.svcAcctCreateTitle');
  const description = isConverting
    ? t('agentBuilder.svcAcctConvertDesc')
    : t('agentBuilder.svcAcctCreateDesc');
  const confirmLabel = isConverting ? t('agentBuilder.svcAcctConvertLabel') : t('agentBuilder.svcAcctCreateLabel');
  const busyLabel = isConverting ? t('agentBuilder.svcAcctConvertBusy') : t('agentBuilder.svcAcctCreateBusy');
  const nextSteps = isConverting
    ? t('agentBuilder.svcAcctConvertNextSteps')
    : t('agentBuilder.svcAcctCreateNextSteps');

  const [ackKnowledge, setAckKnowledge] = useState(false);
  const [ackToolsets, setAckToolsets] = useState(false);
  const [ackOrg, setAckOrg] = useState(false);

  useEffect(() => {
    if (!open) return;
    setAckKnowledge(false);
    setAckToolsets(false);
    setAckOrg(false);
  }, [open]);

  const allAcknowledged = ackKnowledge && ackToolsets && ackOrg;
  const confirmDisabled = creating || hasUserToolsets || !allAcknowledged;

  const handleOpenChange = (next: boolean) => {
    if (!next && !creating) onClose();
  };

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      {open ? (
        <Box
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'rgba(28, 32, 36, 0.1)',
            zIndex: 999,
            cursor: creating ? 'not-allowed' : 'pointer',
          }}
          onClick={() => !creating && onClose()}
        />
      ) : null}
      <Dialog.Content
        style={{
          maxWidth: 'min(68rem, calc(100vw - 3rem))',
          width: '100%',
          padding: 'var(--space-5)',
          zIndex: 1000,
          backgroundColor: 'var(--color-panel-solid)',
          borderRadius: 'var(--radius-5)',
          border: '1px solid var(--olive-a3)',
          boxShadow:
            '0 16px 36px -20px rgba(0, 6, 46, 0.2), 0 16px 64px rgba(0, 0, 85, 0.02), 0 12px 60px rgba(0, 0, 0, 0.15)',
        }}
      >
        <Flex align="start" justify="between" gap="3" mb="4">
          <Flex align="center" gap="3" style={{ minWidth: 0 }}>
            <Box
              style={{
                width: 44,
                height: 44,
                borderRadius: 'var(--radius-3)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                background: 'var(--accent-3)',
                border: '1px solid var(--accent-6)',
                flexShrink: 0,
              }}
            >
              <MaterialIcon name="admin_panel_settings" size={24} style={{ color: 'var(--accent-11)' }} />
            </Box>
            <Box style={{ minWidth: 0 }}>
              <Dialog.Title style={{ marginBottom: 6 }}>{title}</Dialog.Title>
              <Text size="2" style={{ color: 'var(--slate-11)' }}>
                {t('agentBuilder.svcAcctAgentLabel')}{' '}
                <Text weight="medium" style={{ color: 'var(--slate-12)' }}>
                  {agentName.trim() || t('agentBuilder.svcAcctUnnamed')}
                </Text>
              </Text>
            </Box>
          </Flex>
          <IconButton
            type="button"
            variant="ghost"
            color="gray"
            size="2"
            onClick={() => !creating && onClose()}
            aria-label={t('common.close')}
          >
            <MaterialIcon name="close" size={20} />
          </IconButton>
        </Flex>

        <Flex direction="column" gap="3">
          <Dialog.Description size="2" style={{ color: 'var(--slate-11)', margin: 0, lineHeight: 1.5 }}>
            {`${description} ${t('agentBuilder.svcAcctDescSuffix')}`}
          </Dialog.Description>

          <Callout.Root color="red" variant="outline" size="2">
            <Callout.Icon>
              <MaterialIcon name="error" size={20} style={{ color: 'var(--red-11)' }} />
            </Callout.Icon>
            <Flex direction="column" gap="2" style={{ flex: 1, minWidth: 0 }}>
              <Text size="2" weight="bold" style={{ color: 'var(--red-11)' }}>
                {t('agentBuilder.svcAcctKnowledgeTitle')}
              </Text>
              <ul
                style={{
                  margin: 0,
                  paddingLeft: '1.25rem',
                  listStyleType: 'disc',
                  color: 'var(--slate-12)',
                }}
              >
                <li style={{ marginBottom: 'var(--space-2)', paddingLeft: 'var(--space-1)', lineHeight: 1.5 }}>
                  <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: 1.5 }}>
                    {t('agentBuilder.svcAcctKnowledgeNote1')}
                  </Text>
                </li>
                <li style={{ marginBottom: 'var(--space-2)', paddingLeft: 'var(--space-1)', lineHeight: 1.5 }}>
                  <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: 1.5 }}>
                    {t('agentBuilder.svcAcctKnowledgeNote2')}
                  </Text>
                </li>
                <li style={{ paddingLeft: 'var(--space-1)', lineHeight: 1.5 }}>
                  <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: 1.5 }}>
                    {t('agentBuilder.svcAcctKnowledgeNote3')}
                  </Text>
                </li>
              </ul>
            </Flex>
          </Callout.Root>

          <Callout.Root color="jade" variant="surface" size="2">
            <Callout.Icon>
              <MaterialIcon name="vpn_key" size={18} style={{ color: 'var(--accent-11)' }} />
            </Callout.Icon>
            <Flex direction="column" gap="2" style={{ flex: 1, minWidth: 0 }}>
              <Text size="2" weight="bold" style={{ color: 'var(--slate-12)' }}>
                {t('agentBuilder.svcAcctToolsetsTitle')}
              </Text>
              <Text size="2" style={{ color: 'var(--slate-11)', lineHeight: 1.5 }}>
                {t('agentBuilder.svcAcctToolsetsDesc')}
              </Text>
              <ul
                style={{
                  margin: 0,
                  paddingLeft: '1.25rem',
                  listStyleType: 'disc',
                  color: 'var(--slate-12)',
                }}
              >
                <li style={{ marginBottom: 'var(--space-2)', paddingLeft: 'var(--space-1)', lineHeight: 1.5 }}>
                  <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: 1.5 }}>
                    {t('agentBuilder.svcAcctToolsetsNote1')}
                  </Text>
                </li>
                <li style={{ paddingLeft: 'var(--space-1)', lineHeight: 1.5 }}>
                  <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: 1.5 }}>
                    {isConverting
                      ? t('agentBuilder.svcAcctToolsetsNote2Convert')
                      : t('agentBuilder.svcAcctToolsetsNote2Create')}
                  </Text>
                </li>
              </ul>
            </Flex>
          </Callout.Root>

          <Callout.Root color="red" variant="outline" size="2">
            <Callout.Icon>
              <MaterialIcon name="error" size={20} style={{ color: 'var(--red-11)' }} />
            </Callout.Icon>
            <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
              <Text size="2" weight="bold" style={{ color: 'var(--red-11)' }}>
                {t('agentBuilder.svcAcctOrgTitle')}
              </Text>
              <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: 1.5 }}>
                {t('agentBuilder.svcAcctOrgDesc')}
              </Text>
            </Flex>
          </Callout.Root>

          {hasUserToolsets ? (
            <Callout.Root color="red" variant="surface" size="1">
              <Callout.Icon>
                <MaterialIcon name="error" size={16} />
              </Callout.Icon>
              <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
                <Callout.Text weight="bold">{t('agentBuilder.svcAcctRemoveToolsetsTitle')}</Callout.Text>
                <Callout.Text size="1" style={{ color: 'var(--slate-11)' }}>
                  {t('agentBuilder.svcAcctRemoveToolsetsDesc', {
                    action: isConverting
                      ? t('agentBuilder.svcAcctConvertAction')
                      : t('agentBuilder.svcAcctCreateAction'),
                  })}
                </Callout.Text>
              </Flex>
            </Callout.Root>
          ) : null}

          <Callout.Root color="jade" variant="surface" size="1">
            <Callout.Icon>
              <MaterialIcon name="lightbulb" size={16} />
            </Callout.Icon>
            <Callout.Text size="1" style={{ color: 'var(--slate-11)' }}>
              <Text weight="bold" size="1" style={{ color: 'var(--slate-12)' }}>
                {t('agentBuilder.svcAcctNextStepsLabel')}{' '}
              </Text>
              {nextSteps}
            </Callout.Text>
          </Callout.Root>

          {error ? (
            <Callout.Root color="red" variant="surface" size="1">
              <Callout.Icon>
                <MaterialIcon name="error" size={16} />
              </Callout.Icon>
              <Callout.Text style={{ flex: 1, minWidth: 0 }}>{error}</Callout.Text>
            </Callout.Root>
          ) : null}

          <Box
            p="3"
            style={{
              borderRadius: 'var(--radius-3)',
              border: '1px solid var(--gray-6)',
              background: 'var(--gray-2)',
            }}
          >
            <Text size="2" weight="medium" mb="3" style={{ color: 'var(--slate-12)' }}>
              {t('agentBuilder.svcAcctConfirmTitle')}
            </Text>
            <Flex direction="column" gap="3">
              <label style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-2)', cursor: 'pointer' }}>
                <Checkbox
                  size="2"
                  checked={ackKnowledge}
                  onCheckedChange={(v) => setAckKnowledge(v === true)}
                  disabled={creating}
                  style={{ marginTop: 2 }}
                />
                <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: 1.45 }}>
                  {t('agentBuilder.svcAcctAckKnowledge')}
                </Text>
              </label>
              <label style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-2)', cursor: 'pointer' }}>
                <Checkbox
                  size="2"
                  checked={ackToolsets}
                  onCheckedChange={(v) => setAckToolsets(v === true)}
                  disabled={creating}
                  style={{ marginTop: 2 }}
                />
                <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: 1.45 }}>
                  {t('agentBuilder.svcAcctAckToolsets')}
                </Text>
              </label>
              <label style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-2)', cursor: 'pointer' }}>
                <Checkbox
                  size="2"
                  checked={ackOrg}
                  onCheckedChange={(v) => setAckOrg(v === true)}
                  disabled={creating}
                  style={{ marginTop: 2 }}
                />
                <Text size="2" style={{ color: 'var(--slate-12)', lineHeight: 1.45 }}>
                  {t('agentBuilder.svcAcctAckOrg')}
                </Text>
              </label>
            </Flex>
            {!allAcknowledged && !hasUserToolsets ? (
              <Text size="1" mt="3" style={{ color: 'var(--slate-11)' }}>
                {isConverting
                  ? t('agentBuilder.svcAcctCheckboxHintConvert')
                  : t('agentBuilder.svcAcctCheckboxHintCreate')}
              </Text>
            ) : null}
          </Box>

          <Separator size="4" style={{ background: 'var(--gray-5)' }} />

          <Flex gap="2" justify="end" wrap="wrap">
            <Button
              type="button"
              variant="outline"
              color="gray"
              size="2"
              onClick={onClose}
              disabled={creating}
            >
              {t('action.cancel')}
            </Button>
            <Button
              type="button"
              size="2"
              color="jade"
              onClick={() => void onConfirm()}
              disabled={confirmDisabled}
            >
              <Flex align="center" gap="2">
                {creating ? <MaterialIcon name="hourglass_empty" size={18} /> : null}
                {creating ? busyLabel : confirmLabel}
              </Flex>
            </Button>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
