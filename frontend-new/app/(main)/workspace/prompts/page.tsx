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
import {
  PromptsApi,
  DEFAULT_SYSTEM_PROMPT,
  DEFAULT_WEB_SEARCH_PROMPT,
} from './api';
import { LottieLoader } from '@/app/components/ui/lottie-loader';

// ============================================================
// Sub-components
// ============================================================

interface PromptSectionCardProps {
  iconName: string;
  title: string;
  description: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}

function PromptSectionCard({
  iconName,
  title,
  description,
  children,
  action,
}: PromptSectionCardProps) {
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
        style={{ padding: 'var(--space-3) var(--space-4)' }}
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
          <MaterialIcon name={iconName} size={20} color="var(--accent-9)" />
        </Flex>
        <Flex direction="column" gap="1" style={{ flex: 1 }}>
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {title}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-9)', fontWeight: 300, lineHeight: '16px' }}>
            {description}
          </Text>
        </Flex>
        {action}
      </Flex>

      {/* Divider */}
          <Box px="4">
            <Box style={{ height: 1, background: 'var(--olive-3)' }} />
          </Box>
      {/* Content */}
      <Flex direction="column" gap="3" style={{ padding: 'var(--space-4)' }}>
        {children}
      </Flex>
    </Flex>
  );
}

interface PromptEditorProps {
  label: string;
  value: string;
  placeholder: string;
  helperText: string;
  onChange: (value: string) => void;
  onUseDefault: () => void;
}

function PromptEditor({
  label,
  value,
  placeholder,
  helperText,
  onChange,
  onUseDefault,
}: PromptEditorProps) {
  return (
    <>
      <Flex align="center" justify="between">
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {label}
        </Text>
        <Button
          variant="outline"
          color="gray"
          size="2"
          onClick={onUseDefault}
        >
          Use Default Prompt
        </Button>
      </Flex>

      <TextArea
        rows={6}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{ resize: 'vertical' }}
      />

      <Text size="1" style={{ color: 'var(--slate-10)', lineHeight: '16px', fontWeight: 300 }}>
        {helperText}
      </Text>
    </>
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
        padding: 'var(--space-3) var(--space-4)',
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

  // Current editor state
  const [customPrompt, setCustomPrompt] = useState('');
  const [customPromptWebSearch, setCustomPromptWebSearch] = useState('');

  // Saved snapshot for dirty-state detection
  const [savedPrompt, setSavedPrompt] = useState('');
  const [savedPromptWebSearch, setSavedPromptWebSearch] = useState('');

  const isDirty =
    customPrompt !== savedPrompt || customPromptWebSearch !== savedPromptWebSearch;

  // ── Load on mount ──────────────────────────────────────────
  useEffect(() => {
    const load = async () => {
      try {
        const prompts = await PromptsApi.getSystemPrompts();
        setCustomPrompt(prompts.customSystemPrompt);
        setCustomPromptWebSearch(prompts.customSystemPromptWebSearch);
        setSavedPrompt(prompts.customSystemPrompt);
        setSavedPromptWebSearch(prompts.customSystemPromptWebSearch);
      } finally {
        setIsLoading(false);
      }
    };
    load();
  }, []);

  // ── Handlers ───────────────────────────────────────────────
  const handleUseDefaultInternal = useCallback(() => {
    setCustomPrompt(DEFAULT_SYSTEM_PROMPT);
  }, []);

  const handleUseDefaultWebSearch = useCallback(() => {
    setCustomPromptWebSearch(DEFAULT_WEB_SEARCH_PROMPT);
  }, []);

  const handleSave = useCallback(async () => {
    setIsSaving(true);
    try {
      await PromptsApi.saveSystemPrompts({
        customSystemPrompt: customPrompt,
        customSystemPromptWebSearch: customPromptWebSearch,
      });
      setSavedPrompt(customPrompt);
      setSavedPromptWebSearch(customPromptWebSearch);
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
  }, [customPrompt, customPromptWebSearch, addToast]);

  const handleDiscard = useCallback(() => {
    setCustomPrompt(savedPrompt);
    setCustomPromptWebSearch(savedPromptWebSearch);
    addToast({
      variant: 'success',
      title: t('workspace.prompts.toasts.discarded'),
      description: t('workspace.prompts.toasts.discardedDescription'),
    });
  }, [savedPrompt, savedPromptWebSearch, addToast]);

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
        <Flex align="center" justify="between" style={{ marginBottom: 'var(--space-6)' }}>
          <Box>
            <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
              Custom System Prompts
            </Heading>
            <Text size="2" style={{ color: 'var(--slate-10)', marginTop: 4, display: 'block' }}>
              Configure separate system prompts for internal search and web search modes
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

        {/* ── Internal Search Prompt Section ── */}
        <Box style={{ marginBottom: 20 }}>
          <PromptSectionCard
            iconName="edit_note"
            title="Internal Search"
            description="System prompt used when answering from your internal knowledge base"
          >
            <PromptEditor
              label=""
              value={customPrompt}
              placeholder="Enter your custom system prompt for internal search here"
              helperText="This prompt guides the AI when answering from internal documents. Changes take effect immediately for new conversations."
              onChange={setCustomPrompt}
              onUseDefault={handleUseDefaultInternal}
            />
          </PromptSectionCard>
        </Box>

        {/* ── Web Search Prompt Section ── */}
        <Box style={{ marginBottom: 20 }}>
          <PromptSectionCard
            iconName="travel_explore"
            title="Web Search"
            description="System prompt used when answering with live web search results"
          >
            <PromptEditor
              label=""
              value={customPromptWebSearch}
              placeholder="Enter your custom system prompt for web search here"
              helperText="This prompt guides the AI when answering using web search results. Changes take effect immediately for new conversations."
              onChange={setCustomPromptWebSearch}
              onUseDefault={handleUseDefaultWebSearch}
            />
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
