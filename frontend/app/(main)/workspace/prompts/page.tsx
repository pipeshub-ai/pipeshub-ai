'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import {
  Box,
  Button,
  Flex,
  Heading,
  IconButton,
  Switch,
  Text,
  TextArea,
} from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SettingsSaveBar } from '../components';
import { useToastStore } from '@/lib/store/toast-store';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { PromptsApi } from './api';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { SettingsPageContainer } from '@/app/(main)/workspace/components/settings-page-container';

// ============================================================
// Sub-components
// ============================================================

interface PromptSectionCardProps {
  iconName: string;
  title: string;
  description: string;
  children: React.ReactNode;
  action?: React.ReactNode;
  testId?: string;
}

function PromptSectionCard({
  iconName,
  title,
  description,
  children,
  action,
  testId,
}: PromptSectionCardProps) {
  return (
    <Flex
      direction="column"
      data-testid={testId}
      style={{
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        overflow: 'hidden',
        backgroundColor: 'var(--olive-2)',
        backdropFilter: 'var(--frost-filter)',
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
  onClear: () => void;
}

function PromptEditor({
  label,
  value,
  placeholder,
  helperText,
  onChange,
  onClear,
}: PromptEditorProps) {
  const { t } = useTranslation();
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
          disabled={!value}
          onClick={onClear}
        >
          {t('workspace.prompts.resetToDefault')}
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

interface McpPromptToggleProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  length: number;
  maxChars: number;
}

/** Sends the Agent prompt to MCP clients too; they keep only so much of it. */
function McpPromptToggle({ checked, onCheckedChange, length, maxChars }: McpPromptToggleProps) {
  const { t } = useTranslation();
  const tooLong = checked && length > maxChars;
  return (
    <Flex direction="column" gap="2" data-testid="prompt-mcp-toggle">
      <Flex align="center" justify="between" gap="4">
        <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
          <Text as="label" htmlFor="prompt-use-in-mcp" size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('workspace.prompts.mcpToggleLabel')}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-10)', lineHeight: '16px', fontWeight: 300 }}>
            {t('workspace.prompts.mcpToggleDescription', { max: maxChars })}
          </Text>
        </Flex>
        <Switch
          id="prompt-use-in-mcp"
          checked={checked}
          onCheckedChange={onCheckedChange}
          style={{ flexShrink: 0, cursor: 'pointer' }}
        />
      </Flex>
      {checked && (
        <Text
          size="1"
          role={tooLong ? 'alert' : undefined}
          data-testid="prompt-mcp-count"
          style={{ color: tooLong ? 'var(--red-11)' : 'var(--slate-10)', lineHeight: '16px' }}
        >
          {tooLong
            ? t('workspace.prompts.mcpTooLong', { length, max: maxChars })
            : t('workspace.prompts.mcpCharCount', { length, max: maxChars })}
        </Text>
      )}
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
  const router = useRouter();
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  const [isLoading, setIsLoading] = useState(true);
  // Saving over prompts that never loaded would replace them with blanks.
  const [loadFailed, setLoadFailed] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  // Current editor state
  const [customPrompt, setCustomPrompt] = useState('');
  const [customPromptWebSearch, setCustomPromptWebSearch] = useState('');
  const [customPromptAgent, setCustomPromptAgent] = useState('');
  const [useInMcp, setUseInMcp] = useState(false);
  const [mcpPromptMaxChars, setMcpPromptMaxChars] = useState(500);

  // Saved snapshot for dirty-state detection
  const [savedPrompt, setSavedPrompt] = useState('');
  const [savedPromptWebSearch, setSavedPromptWebSearch] = useState('');
  const [savedPromptAgent, setSavedPromptAgent] = useState('');
  const [savedUseInMcp, setSavedUseInMcp] = useState(false);

  const isDirty =
    customPrompt !== savedPrompt ||
    customPromptWebSearch !== savedPromptWebSearch ||
    customPromptAgent !== savedPromptAgent ||
    useInMcp !== savedUseInMcp;

  // The server sends the prompt without its leading and trailing space, and refuses a longer one.
  const agentPromptLength = customPromptAgent.trim().length;
  const agentPromptTooLongForMcp = useInMcp && agentPromptLength > mcpPromptMaxChars;
  const cannotSave = loadFailed || agentPromptTooLongForMcp;

  // The sidebar hides this page from members, but the URL still opens it. A profile that failed
  // to load leaves isAdmin null; General is open to everyone, so that goes there too.
  useEffect(() => {
    if (isProfileInitialized && isAdmin !== true) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

  // ── Load on mount ──────────────────────────────────────────
  useEffect(() => {
    if (!isProfileInitialized || isAdmin !== true) return;
    const load = async () => {
      try {
        const prompts = await PromptsApi.getSystemPrompts();
        if (!prompts) {
          setLoadFailed(true);
          return;
        }
        setLoadFailed(false);
        setCustomPrompt(prompts.customSystemPrompt);
        setCustomPromptWebSearch(prompts.customSystemPromptWebSearch);
        setCustomPromptAgent(prompts.customSystemPromptAgent);
        setUseInMcp(prompts.useInMcp);
        setMcpPromptMaxChars(prompts.mcpPromptMaxChars);
        setSavedPrompt(prompts.customSystemPrompt);
        setSavedPromptWebSearch(prompts.customSystemPromptWebSearch);
        setSavedPromptAgent(prompts.customSystemPromptAgent);
        setSavedUseInMcp(prompts.useInMcp);
      } finally {
        setIsLoading(false);
      }
    };
    load();
  }, [isProfileInitialized, isAdmin]);

  // ── Handlers ───────────────────────────────────────────────
  // "Reset to Default" means removing the org override entirely — an empty
  // custom instructions field, not a hardcoded placeholder string. The
  // backend already treats a blank/missing value as "no custom instructions
  // section" (see `PipesHubPromptBuilder`), so clearing here IS the default.
  const handleUseDefaultInternal = useCallback(() => {
    setCustomPrompt('');
  }, []);

  const handleUseDefaultWebSearch = useCallback(() => {
    setCustomPromptWebSearch('');
  }, []);

  const handleUseDefaultAgent = useCallback(() => {
    setCustomPromptAgent('');
  }, []);

  const handleSave = useCallback(async () => {
    if (cannotSave) return;
    setIsSaving(true);
    try {
      await PromptsApi.saveSystemPrompts({
        customSystemPrompt: customPrompt,
        customSystemPromptWebSearch: customPromptWebSearch,
        customSystemPromptAgent: customPromptAgent,
        useInMcp,
      });
      setSavedPrompt(customPrompt);
      setSavedPromptWebSearch(customPromptWebSearch);
      setSavedPromptAgent(customPromptAgent);
      setSavedUseInMcp(useInMcp);
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
  }, [customPrompt, customPromptWebSearch, customPromptAgent, useInMcp, cannotSave, addToast, t]);

  const handleDiscard = useCallback(() => {
    setCustomPrompt(savedPrompt);
    setCustomPromptWebSearch(savedPromptWebSearch);
    setCustomPromptAgent(savedPromptAgent);
    setUseInMcp(savedUseInMcp);
    addToast({
      variant: 'success',
      title: t('workspace.prompts.toasts.discarded'),
      description: t('workspace.prompts.toasts.discardedDescription'),
    });
  }, [savedPrompt, savedPromptWebSearch, savedPromptAgent, savedUseInMcp, addToast, t]);

  if (!isProfileInitialized || isAdmin !== true) {
    return null;
  }

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
      <SettingsPageContainer>

        {/* ── Page Header ── */}
        <Flex align="center" justify="between" wrap="wrap" gap="3" style={{ marginBottom: 'var(--space-6)' }}>
          <Box style={{ flex: '1 1 240px', minWidth: 0 }}>
            <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('workspace.prompts.customInstructionsTitle')}
            </Heading>
            <Text size="2" style={{ color: 'var(--slate-10)', marginTop: 4, display: 'block' }}>
              {t('workspace.prompts.customInstructionsDescription')}
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

        {loadFailed && (
          <Text
            as="p"
            size="2"
            role="alert"
            data-testid="prompts-load-error"
            style={{ color: 'var(--red-11)', marginBottom: 20 }}
          >
            {t('workspace.prompts.loadError')}
          </Text>
        )}

        {/* ── Scope Callout: Chat Assistant (and the MCP server, if on), not Agent Builder ── */}
        <Flex
          align="center"
          gap="3"
          style={{
            background: 'var(--slate-a2)',
            padding: 'var(--space-3) var(--space-4)',
            marginBottom: 20,
            borderRadius: 'var(--radius-1)',
          }}
        >
          <IconButton variant="soft" size="2" style={{ flexShrink: 0, cursor: 'default', background: 'var(--slate-a3)' }} tabIndex={-1}>
            <MaterialIcon name="info" size={16} color="var(--slate-11)" />
          </IconButton>
          <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px', fontWeight: 300 }}>
            <Trans i18nKey="workspace.prompts.scopeHint" components={{ bold: <strong /> }} />
          </Text>
        </Flex>

        {/* ── Agent Mode Prompt Section ── */}
        <Box style={{ marginBottom: 20 }}>
          <PromptSectionCard
            iconName="smart_toy"
            testId="prompt-section-agent"
            title={t('workspace.prompts.agentTitle')}
            description={t('workspace.prompts.agentDescription')}
          >
            <PromptEditor
              label=""
              value={customPromptAgent}
              placeholder={t('workspace.prompts.agentPlaceholder')}
              helperText={t('workspace.prompts.agentHelperText')}
              onChange={setCustomPromptAgent}
              onClear={handleUseDefaultAgent}
            />
            <McpPromptToggle
              checked={useInMcp}
              onCheckedChange={setUseInMcp}
              length={agentPromptLength}
              maxChars={mcpPromptMaxChars}
            />
          </PromptSectionCard>
        </Box>

        {/* ── Prompt Configuration Callout ── */}
        <PromptConfigCallout />

      </SettingsPageContainer>

      {/* ── Settings Save Bar ── */}
      <SettingsSaveBar
        visible={isDirty}
        onDiscard={handleDiscard}
        onSave={handleSave}
        isSaving={isSaving}
        saveDisabled={cannotSave}
      />
    </Box>
    </ServiceGate>
  );
}
