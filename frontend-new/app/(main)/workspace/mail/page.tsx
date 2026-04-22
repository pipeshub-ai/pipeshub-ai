'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import {
  Box,
  Flex,
  Text,
  Heading,
  Button,
  Badge,
  IconButton,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { useToastStore } from '@/lib/store/toast-store';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { SmtpApi } from './api';
import { SmtpConfigurePanel } from './components/smtp-configure-panel';
import type { SmtpConfig } from './types';

// ============================================================
// Page
// ============================================================

export default function MailPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const addToast = useToastStore((s) => s.addToast);
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  const [isConfigured, setIsConfigured] = useState(false);
  const [smtpConfig, setSmtpConfig] = useState<SmtpConfig | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [panelOpen, setPanelOpen] = useState(false);

  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

  // ── Load SMTP status ──────────────────────────────────────
  const loadSmtpConfig = useCallback(async () => {
    setIsLoading(true);
    try {
      const config = await SmtpApi.getSmtpConfig();
      setSmtpConfig(config);
      setIsConfigured(!!config?.host && !!config?.fromEmail);
    } catch {
      setIsConfigured(false);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isProfileInitialized || isAdmin !== true) return;
    loadSmtpConfig();
  }, [isProfileInitialized, isAdmin, loadSmtpConfig]);

  // ── Save SMTP config ──────────────────────────────────────
  const handleSave = useCallback(
    async (config: SmtpConfig) => {
      await SmtpApi.saveSmtpConfig(config);
    },
    [],
  );

  // ── After successful save ─────────────────────────────────
  const handleSaveSuccess = useCallback(async () => {
    // Refresh config status
    const config = await SmtpApi.getSmtpConfig();
    setSmtpConfig(config);
    setIsConfigured(!!config?.host && !!config?.fromEmail);
    addToast({
      variant: 'success',
      title: t('workspace.mail.toasts.saved'),
      description: t('workspace.mail.toasts.savedDescription'),
    });
  }, [addToast]);

  // Prevent rendering while profile is unresolved or for non-admin (redirect above).
  if (!isProfileInitialized || isAdmin === false) {
    return null;
  }

  // ── Loading state ─────────────────────────────────────────
  if (isLoading) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', width: '100%' }}>
        <LottieLoader variant="loader" size={48} showLabel />
      </Flex>
    );
  }

  // ── Render ────────────────────────────────────────────────
  return (
    <Box style={{ height: '100%', overflowY: 'auto' }}>
      <Box style={{ padding: '64px 100px 80px' }}>
        {/* ── Page header ── */}
        <Flex align="start" justify="between" style={{ marginBottom: 24 }}>
          <Box>
            <Heading size="6" style={{ color: 'var(--slate-12)' }}>
              {t('workspace.mail.title')}
            </Heading>
            <Text size="2" style={{ color: 'var(--slate-10)', marginTop: 4, display: 'block' }}>
              {t('workspace.mail.subtitle')}
            </Text>
          </Box>

          <Button
            variant="outline"
            color="gray"
            size="2"
            onClick={() => window.open('https://docs.pipeshub.com/smtp', '_blank')}
            style={{ cursor: 'pointer', flexShrink: 0, gap: 6 }}
          >
            <span className="material-icons-outlined" style={{ fontSize: 15 }}>
              open_in_new
            </span>
            {t('workspace.bots.documentation')}
          </Button>
        </Flex>

        {/* ── Server Configuration Section ── */}
        <Flex
          direction="column"
          style={{
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-1)',
            background: 'var(--olive-2)',
            marginBottom: 20,
          }}
        >
          {/* Section header */}
          <Box style={{ padding: '14px 16px' }}>
            <Text size="3" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
              {t('workspace.mail.serverConfig')}
            </Text>
            <Text
              size="1"
              style={{ color: 'var(--slate-10)', display: 'block', marginTop: 2, fontWeight: 300 }}
            >
              {t('workspace.mail.serverConfigDescription')}
            </Text>
          </Box>
          <Box px="4">
            <Box style={{ height: 1, background: 'var(--olive-3)' }} />
          </Box>

          {/* SMTP row */}
          <Box style={{ padding: '12px 14px' }}>
              <Flex
                align="center"
                gap="3"
                style={{
                  padding: '12px 14px',
                  border: '1px solid var(--slate-3)',
                  borderRadius: 'var(--radius-1)',
                  background: 'var(--olive-2)',
                  backdropFilter: 'blur(25px)',
                }}
              >
                {/* Mail icon box */}
                <Flex
                  align="center"
                  justify="center"
                  style={{
                    width: 36,
                    height: 36,
                    borderRadius: 'var(--radius-2)',
                    backgroundColor: 'var(--slate-3)',
                    flexShrink: 0,
                  }}
                >
                  <MaterialIcon name="mail" size={18} color="var(--slate-11)" />
                </Flex>

                {/* Label + description */}
                <Box style={{ flex: 1, minWidth: 0 }}>
                  <Text
                    size="2"
                    weight="medium"
                    style={{ color: 'var(--slate-12)', display: 'block' }}
                  >
                    {t('workspace.mail.smtp')}
                  </Text>
                  <Text
                    size="1"
                    style={{
                      color: 'var(--slate-10)',
                      display: 'block',
                      marginTop: 2,
                      fontWeight: 300,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {t('workspace.mail.subtitle')}
                  </Text>
                </Box>

                {/* Configured / Not Configured badge */}
                <Badge
                  color={isConfigured ? 'green' : 'orange'}
                  variant="soft"
                  size="1"
                  style={{ flexShrink: 0 }}
                >
                  {isConfigured ? t('workspace.mail.configured') : t('workspace.mail.notConfigured')}
                </Badge>

                {/* Settings / configure button */}
                <IconButton
                  variant="ghost"
                  color="gray"
                  size="2"
                  onClick={() => setPanelOpen(true)}
                  style={{ cursor: 'pointer', flexShrink: 0 }}
                >
                  <MaterialIcon name="settings" size={18} color="var(--slate-10)" />
                </IconButton>
              </Flex>
          </Box>
        </Flex>
      </Box>

      {/* ── SMTP Configure Panel ── */}
      <SmtpConfigurePanel
        open={panelOpen}
        isConfigured={isConfigured}
        initialConfig={smtpConfig}
        onClose={() => setPanelOpen(false)}
        onSaveSuccess={handleSaveSuccess}
        onSave={handleSave}
      />
    </Box>
  );
}
