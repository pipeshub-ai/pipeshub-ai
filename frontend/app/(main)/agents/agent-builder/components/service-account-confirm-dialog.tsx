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
  Text,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';

const LIST: React.CSSProperties = {
  margin: 0,
  marginTop: 4,
  paddingLeft: '1rem',
  listStyleType: 'disc',
  color: 'var(--olive-12)',
};

const LI: React.CSSProperties = {
  marginBottom: 6,
  paddingLeft: 2,
  lineHeight: 1.45,
};

const LI_LAST: React.CSSProperties = {
  ...LI,
  marginBottom: 0,
};

const BODY: React.CSSProperties = {
  color: 'var(--olive-12)',
  lineHeight: 1.45,
};

/** Stacked info card — same visual language as original dialog, single column for readability. */
function InfoCard({
  icon,
  iconColor,
  iconBg,
  iconBorder,
  title,
  children,
}: {
  icon: string;
  iconColor: string;
  iconBg: string;
  iconBorder: string;
  title: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Box
      style={{
        background: 'var(--olive-2)',
        border: '1px solid var(--olive-4)',
        borderRadius: 'var(--radius-2)',
        padding: 'var(--space-2) var(--space-3)',
        boxSizing: 'border-box',
        marginBottom: 'var(--space-2)',
      }}
    >
      <Flex align="start" gap="2">
        <Box
          style={{
            width: 28,
            height: 28,
            minWidth: 28,
            borderRadius: 'var(--radius-1)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: iconBg,
            border: `1px solid ${iconBorder}`,
          }}
        >
          <MaterialIcon name={icon} size={16} style={{ color: iconColor }} />
        </Box>
        <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
          <Text size="2" weight="bold" style={{ color: 'var(--olive-12)', lineHeight: 1.35 }}>
            {title}
          </Text>
          {children}
        </Flex>
      </Flex>
    </Box>
  );
}

export interface ServiceAccountConfirmDialogProps {
  open: boolean;
  agentName: string;
  creating: boolean;
  error: string | null;
  isConverting?: boolean;
  onClose: () => void;
  onConfirm: () => void | Promise<void>;
}

export function ServiceAccountConfirmDialog({
  open,
  agentName,
  creating,
  error,
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

  const [ackKnowledge, setAckKnowledge] = useState(false);
  const [ackToolsets, setAckToolsets] = useState(false);
  const [ackOrg, setAckOrg] = useState(false);

  useEffect(() => {
    if (!open) return;
    setAckKnowledge(false);
    setAckToolsets(false);
    setAckOrg(false);

    const main = document.querySelector<HTMLElement>('[data-app-main-scroll]');
    const prevOverflow = main ? main.style.overflow : '';
    if (main) main.style.overflow = 'hidden';
    return () => {
      if (main) main.style.overflow = prevOverflow;
    };
  }, [open]);

  const allAcknowledged = ackKnowledge && ackToolsets && ackOrg;
  const confirmDisabled = creating || !allAcknowledged;

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
            backgroundColor: 'rgba(15, 23, 42, 0.12)',
            zIndex: 999,
            cursor: creating ? 'not-allowed' : 'pointer',
          }}
          onClick={() => !creating && onClose()}
        />
      ) : null}
      <Dialog.Content
        className="service-account-confirm-dialog"
        style={{
          maxWidth: 'min(71rem, calc(100vw - 1.5rem))',
          width: '100%',
          maxHeight: 'min(88dvh, calc(100svh - 2.5rem), calc(100vh - 2.5rem))',
          padding: 'var(--space-3)',
          paddingBottom: 'max(var(--space-3), env(safe-area-inset-bottom, 0px))',
          zIndex: 1000,
          backgroundColor: 'var(--color-panel-solid)',
          borderRadius: 'var(--radius-3)',
          border: '1px solid var(--olive-4)',
          boxShadow:
            '0 4px 24px -8px rgba(0, 6, 46, 0.1), 0 16px 40px -20px rgba(0, 0, 0, 0.12)',
          display: 'flex',
          flexDirection: 'column',
          gap: 0,
          overflow: 'hidden',
          boxSizing: 'border-box',
        }}
      >
        <Flex
          align="start"
          justify="between"
          gap="2"
          pb="2"
          mb="2"
          style={{ flexShrink: 0, borderBottom: '1px solid var(--olive-4)' }}
        >
          <Flex align="center" gap="2" style={{ minWidth: 0 }}>
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
              <MaterialIcon name="admin_panel_settings" size={20} style={{ color: 'var(--accent-11)' }} />
            </Box>
            <Box style={{ minWidth: 0 }}>
              <Dialog.Title
                style={{
                  marginBottom: 2,
                  fontSize: '1rem',
                  fontWeight: 600,
                  lineHeight: 1.35,
                  color: 'var(--olive-12)',
                }}
              >
                {title}
              </Dialog.Title>
              <Text size="2" style={{ color: 'var(--olive-11)', lineHeight: 1.4 }}>
                {t('agentBuilder.svcAcctAgentLabel')}{' '}
                <Text weight="medium" size="2" style={{ color: 'var(--olive-12)' }}>
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
            style={{ flexShrink: 0 }}
          >
            <MaterialIcon name="close" size={20} />
          </IconButton>
        </Flex>

        <Box className="service-account-confirm-dialog__body">
          <Flex direction="column" gap="2">
            <Dialog.Description size="2" style={{ color: 'var(--olive-11)', margin: 0, lineHeight: 1.5 }}>
              {description}
            </Dialog.Description>

            <InfoCard
              icon="shield"
              iconColor="var(--red-11)"
              iconBg="var(--red-3)"
              iconBorder="var(--red-6)"
              title={t('agentBuilder.svcAcctKnowledgeTitle')}
            >
              <ul style={LIST}>
                <li style={LI}>
                  <Text size="2" style={BODY}>
                    {t('agentBuilder.svcAcctKnowledgeNote1')}
                  </Text>
                </li>
                <li style={LI_LAST}>
                  <Text size="2" style={BODY}>
                    {t('agentBuilder.svcAcctKnowledgeNote2')}
                  </Text>
                </li>
              </ul>
            </InfoCard>

            <InfoCard
              icon="vpn_key"
              iconColor="var(--accent-11)"
              iconBg="var(--accent-3)"
              iconBorder="var(--accent-6)"
              title={t('agentBuilder.svcAcctToolsetsTitle')}
            >
              <ul style={LIST}>
                <li style={isConverting ? LI : LI_LAST}>
                  <Text size="2" style={BODY}>
                    {t('agentBuilder.svcAcctToolsetsNote1')}
                  </Text>
                </li>
                {isConverting ? (
                  <li style={LI_LAST}>
                    <Text size="2" style={BODY}>
                      {t('agentBuilder.svcAcctToolsetsNote2Convert')}
                    </Text>
                  </li>
                ) : null}
              </ul>
            </InfoCard>

            <InfoCard
              icon="groups"
              iconColor="var(--red-11)"
              iconBg="var(--red-3)"
              iconBorder="var(--red-6)"
              title={t('agentBuilder.svcAcctOrgTitle')}
            >
              <Text size="2" style={{ color: 'var(--olive-12)', lineHeight: 1.45 }}>
                {t('agentBuilder.svcAcctOrgDesc')}
              </Text>
            </InfoCard>

            {error ? (
              <Callout.Root color="red" variant="soft" size="2">
                <Callout.Icon>
                  <MaterialIcon name="error" size={16} />
                </Callout.Icon>
                <Callout.Text size="2" style={{ flex: 1, minWidth: 0, lineHeight: 1.45 }}>
                  {error}
                </Callout.Text>
              </Callout.Root>
            ) : null}

            <Box
              p="3"
              style={{
                borderRadius: 'var(--radius-2)',
                border: '1px solid var(--olive-4)',
                background: 'var(--olive-2)',
              }}
            >
              <Text size="2" weight="bold" mb="2" style={{ color: 'var(--olive-12)', lineHeight: 1.35 }}>
                {t('agentBuilder.svcAcctConfirmTitle')}
              </Text>
              <Flex direction="column" gap="2">
                <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, cursor: 'pointer' }}>
                  <Checkbox
                    size="2"
                    checked={ackKnowledge}
                    onCheckedChange={(v) => setAckKnowledge(v === true)}
                    disabled={creating}
                    style={{ marginTop: 2 }}
                  />
                  <Text size="2" style={{ color: 'var(--olive-12)', lineHeight: 1.45 }}>
                    {t('agentBuilder.svcAcctAckKnowledge')}
                  </Text>
                </label>
                <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, cursor: 'pointer' }}>
                  <Checkbox
                    size="2"
                    checked={ackToolsets}
                    onCheckedChange={(v) => setAckToolsets(v === true)}
                    disabled={creating}
                    style={{ marginTop: 2 }}
                  />
                  <Text size="2" style={{ color: 'var(--olive-12)', lineHeight: 1.45 }}>
                    {t('agentBuilder.svcAcctAckToolsets')}
                  </Text>
                </label>
                <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, cursor: 'pointer' }}>
                  <Checkbox
                    size="2"
                    checked={ackOrg}
                    onCheckedChange={(v) => setAckOrg(v === true)}
                    disabled={creating}
                    style={{ marginTop: 2 }}
                  />
                  <Text size="2" style={{ color: 'var(--olive-12)', lineHeight: 1.45 }}>
                    {t('agentBuilder.svcAcctAckOrg')}
                  </Text>
                </label>
              </Flex>
              {!allAcknowledged ? (
                <Text size="1" mt="2" style={{ color: 'var(--olive-11)', lineHeight: 1.4 }}>
                  {isConverting
                    ? t('agentBuilder.svcAcctCheckboxHintConvert')
                    : t('agentBuilder.svcAcctCheckboxHintCreate')}
                </Text>
              ) : null}
            </Box>
          </Flex>
        </Box>

        <Box
          style={{
            flexShrink: 0,
            paddingTop: 'var(--space-2)',
            marginTop: 'var(--space-2)',
            borderTop: '1px solid var(--olive-4)',
          }}
        >
          <Flex gap="2" justify="end" wrap="wrap">
            <Button type="button" variant="soft" color="gray" size="2" onClick={onClose} disabled={creating}>
              {t('action.cancel')}
            </Button>
            <LoadingButton
              type="button"
              size="2"
              color="jade"
              onClick={() => void onConfirm()}
              disabled={confirmDisabled && !creating}
              loading={creating}
              loadingLabel={busyLabel}
            >
              {confirmLabel}
            </LoadingButton>
          </Flex>
        </Box>
      </Dialog.Content>
    </Dialog.Root>
  );
}
