'use client';

import React, { useCallback, useEffect, useState } from 'react';
import {
  Box,
  Button,
  Flex,
  Heading,
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
}

function PromptSectionCard({ children }: PromptSectionCardProps) {
  return (
    <Flex
      direction="column"
      style={{
        border: '1px solid var(--slate-5)',
        borderRadius: 'var(--radius-1)',
        overflow: 'hidden',
        backgroundColor: 'var(--slate-2)',
        backdropFilter: 'blur(25px)',
      }}
    >
      {/* Card header row */}
      <Flex
        align="center"
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
            System Prompt
          </Text>
          <Text size="1" style={{ color: 'var(--slate-9)', fontWeight: 300, lineHeight: '16px' }}>
            Define the behaviour and personality of the AI assistant
          </Text>
        </Flex>
      </Flex>

      {/* Divider */}
      <Box style={{ height: 1, backgroundColor: 'var(--slate-5)', width: '100%' }} />

      {/* Content */}
      <Flex direction="column" gap="3" style={{ padding: 16 }}>
        {children}
      </Flex>
    </Flex>
  );
}

function PromptConfigCallout() {
  return (
    <Flex
      align="start"
      gap="3"
      style={{
        backgroundColor: 'var(--accent-2)',
        border: '1px solid var(--accent-6)',
        borderRadius: 'var(--radius-1)',
        padding: '12px 16px',
      }}
    >
      <Box style={{ flexShrink: 0, marginTop: 1 }}>
        <MaterialIcon name="info" size={16} color="var(--accent-9)" />
      </Box>
      <Flex direction="column" gap="1">
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          Prompt Configuration
        </Text>
        <Text size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px', fontWeight: 300 }}>
          The custom system prompt helps define the AI&apos;s behaviour, tone, and approach to
          answering questions. Make sure your prompt is clear and aligns with your
          organisation&apos;s needs.
        </Text>
      </Flex>
    </Flex>
  );
}

// ============================================================
// Main Page
// ============================================================

export default function PromptsPage() {
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
        title: 'Prompt saved',
        description: 'The system prompt has been updated',
      });
    } catch {
      addToast({
        variant: 'error',
        title: 'Failed to save prompt',
        description: 'Something went wrong. Please try again.',
        action: { label: 'Try Again', onClick: handleSave },
      });
    } finally {
      setIsSaving(false);
    }
  }, [customPrompt, addToast]);

  const handleDiscard = useCallback(() => {
    setCustomPrompt(savedPrompt);
    addToast({
      variant: 'success',
      title: 'Discarded changes',
      description: 'Your edits have been reverted',
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
              Custom System Prompt
            </Heading>
            <Text size="2" style={{ color: 'var(--slate-10)', marginTop: 4, display: 'block' }}>
              Configure the custom system prompt for AI responses
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
            Documentation
          </Button>
        </Flex>

        {/* ── System Prompt Section ── */}
        <Box style={{ marginBottom: 20 }}>
          <PromptSectionCard>
            {/* Label row */}
            <Flex align="center" justify="between">
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                Custom System Prompt
              </Text>
              <Button
                variant="outline"
                color="gray"
                size="2"
                onClick={handleUseDefault}
              >
                Use Default Prompt
              </Button>
            </Flex>

            {/* Textarea */}
            <TextArea
              rows={6}
              placeholder="Enter your custom system prompt here"
              value={customPrompt}
              onChange={(e) => setCustomPrompt(e.target.value)}
              style={{ resize: 'vertical' }}
            />

            {/* Helper text */}
            <Text size="1" style={{ color: 'var(--slate-10)', lineHeight: '16px', fontWeight: 300 }}>
              This prompt will be used across all AI chat interactions to guide the
              assistant&apos;s responses. Changes take effect immediately for new conversations.
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
