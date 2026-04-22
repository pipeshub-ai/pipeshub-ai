'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Button,
  Flex,
  Heading,
  IconButton,
  Text,
  TextArea,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SettingsSaveBar } from '../components';
import { useToastStore } from '@/lib/store/toast-store';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { PromptsApi, DEFAULT_SYSTEM_PROMPT } from './api';
import { LottieLoader } from '@/app/components/ui/lottie-loader';

// ============================================================
// Sub-components
// ============================================================

interface PromptSectionCardProps {
  children: React.ReactNode;
  action?: React.ReactNode;
}

function PromptSectionCard({ children, action }: PromptSectionCardProps) {
  const { t } = useTranslation();
  return (
    <Flex
      direction="column"
      style={{
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        overflow: 'hidden',
        backgroundColor: 'var(--olive-2)',
        backdropFilter: 'blur(25px)',
      }}
    >
      {/* Card header row */}
      <Flex
        align="center"
        justify="between"
        gap="3"
        style={{ padding: '12px 16px' }}
      >
        <Flex
          align="center"
          justify="center"
          style={{
            width: 36,
            height: 36,
            borderRadius: 'var(--radius-1)',
            backgroundColor: 'var(--accent-2)',
            flexShrink: 0,
          }}
        >
          <MaterialIcon name="edit_note" size={20} color="var(--accent-9)" />
        </Flex>
        <Flex direction="column" gap="1" style={{ flex: 1 }}>
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('workspace.prompts.systemPrompt')}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-9)', fontWeight: 300, lineHeight: '16px' }}>
            {t('workspace.prompts.systemPromptDescription')}
          </Text>
        </Flex>
        {action}
      </Flex>

      {/* Divider */}
          <Box px="4">
            <Box style={{ height: 1, background: 'var(--olive-3)' }} />
          </Box>
      {/* Content */}
      <Flex direction="column" gap="3" style={{ padding: 16 }}>
        {children}
      </Flex>
    </Flex>
  );
}

function PromptConfigCallout() {
  const { t } = useTranslation();
  return (
    <Flex
      align="center"
      gap="3"
      style={{
        background: 'var(--accent-a2)',
        padding: '12px 16px',
      }}
    >
       <IconButton variant="soft" size="2" style={{ flexShrink: 0, cursor: 'default', background: 'var(--slate-a2)' }} tabIndex={-1}>
        <MaterialIcon name="info" size={16} color="var(--accent-11)" />
      </IconButton>
      <Flex direction="column" gap="1">
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('workspace.prompts.configCalloutTitle')}
        </Text>
        <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px', fontWeight: 300 }}>
          {t('workspace.prompts.configCalloutDescription')}
        </Text>
      </Flex>
    </Flex>
  );
}

// ============================================================
// Main Page
// ============================================================

export default function PromptsPage() {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);

  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);

  // The prompt currently in the textarea
  const [customPrompt, setCustomPrompt] = useState('');
  // Snapshot of what is saved on the server (to detect dirty state)
  const [savedPrompt, setSavedPrompt] = useState('');

  const isDirty = customPrompt !== savedPrompt;

  // ── Load on mount ──────────────────────────────────────────
  useEffect(() => {
    const load = async () => {
      try {
        const prompt = await PromptsApi.getSystemPrompt();
        setCustomPrompt(prompt);
        setSavedPrompt(prompt);
      } finally {
        setIsLoading(false);
      }
    };
    load();
  }, []);

  // ── Handlers ───────────────────────────────────────────────
  const handleUseDefault = useCallback(() => {
    setCustomPrompt(DEFAULT_SYSTEM_PROMPT);
  }, []);

  const handleSave = useCallback(async () => {
    setIsSaving(true);
    try {
      await PromptsApi.saveSystemPrompt(customPrompt);
      setSavedPrompt(customPrompt);
      addToast({
        variant: 'success',
        title: t('workspace.prompts.toasts.saved'),
        description: t('workspace.prompts.toasts.savedDescription'),
      });
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.prompts.toasts.saveError'),
        description: t('workspace.prompts.toasts.saveErrorDescription'),
        action: { label: t('action.tryAgain'), onClick: handleSave },
      });
    } finally {
      setIsSaving(false);
    }
  }, [customPrompt, addToast]);

  const handleDiscard = useCallback(() => {
    setCustomPrompt(savedPrompt);
    addToast({
      variant: 'success',
      title: t('workspace.prompts.toasts.discarded'),
      description: t('workspace.prompts.toasts.discardedDescription'),
    });
  }, [savedPrompt, addToast]);

  // ── Loading state ──────────────────────────────────────────
  if (isLoading) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', width: '100%' }}>
        <LottieLoader variant="loader" size={48} showLabel />
      </Flex>
    );
  }

  return (
    <ServiceGate services={['query']}>
    <Box style={{ height: '100%', overflowY: 'auto' }}>
      <Box style={{ padding: '64px 100px', paddingBottom: 80 }}>

        {/* ── Page Header ── */}
        <Flex align="center" justify="between" style={{ marginBottom: 24 }}>
          <Box>
            <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('workspace.prompts.heading')}
            </Heading>
            <Text size="2" style={{ color: 'var(--slate-10)', marginTop: 4, display: 'block' }}>
              {t('workspace.prompts.subtitle')}
            </Text>
          </Box>
          <Button
            variant="outline"
            color="gray"
            size="2"
            onClick={() =>
              window.open('https://docs.pipeshub.com/workspace/prompts', '_blank')
            }
          >
            <MaterialIcon name="open_in_new" size={14} />
            {t('workspace.bots.documentation')}
          </Button>
        </Flex>

        {/* ── System Prompt Section ── */}
        <Box style={{ marginBottom: 20 }}>
          <PromptSectionCard>
            {/* Label row */}
            <Flex align="center" justify="between">
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {t('workspace.prompts.heading')}
              </Text>
              <Button
                variant="outline"
                color="gray"
                size="2"
                onClick={handleUseDefault}
              >
                {t('workspace.prompts.useDefault')}
              </Button>
            </Flex>

            {/* Textarea */}
            <TextArea
              rows={6}
              placeholder={t('workspace.prompts.placeholder')}
              value={customPrompt}
              onChange={(e) => setCustomPrompt(e.target.value)}
              style={{ resize: 'vertical' }}
            />

            {/* Helper text */}
            <Text size="1" style={{ color: 'var(--slate-10)', lineHeight: '16px', fontWeight: 300 }}>
              {t('workspace.prompts.helperText')}
            </Text>
          </PromptSectionCard>
        </Box>

        {/* ── Prompt Configuration Callout ── */}
        <PromptConfigCallout />

      </Box>

      {/* ── Settings Save Bar ── */}
      <SettingsSaveBar
        visible={isDirty}
        onDiscard={handleDiscard}
        onSave={handleSave}
        isSaving={isSaving}
      />
    </Box>
    </ServiceGate>
  );
}
