'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { Box, Flex, Text, Heading, Switch, TextField, Callout } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { useToastStore } from '@/lib/store/toast-store';
import { GeminiFileSearchApi } from './api';
import type { GeminiFileSearchConfig } from './types';

// ============================================================
// Defaults — kept in sync with the Python backend.
// ============================================================

const DEFAULT_CONFIG: GeminiFileSearchConfig = {
  enabled: false,
  generationModel: 'gemini-3.5-flash',
  embeddingModel: 'models/gemini-embedding-2',
  maxStoresPerQuery: 5,
  maxMediaPerQuery: 5,
};

// ============================================================
// Page
// ============================================================

export default function GeminiFileSearchPage() {
  const router = useRouter();
  const addToast = useToastStore((s) => s.addToast);
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  // Admin-only page, like the other global feature settings.
  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

  // ── State ─────────────────────────────────────────────────
  const [config, setConfig] = useState<GeminiFileSearchConfig>(DEFAULT_CONFIG);
  const [draft, setDraft] = useState<GeminiFileSearchConfig>(DEFAULT_CONFIG);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);

  const isDirty =
    draft.enabled !== config.enabled ||
    draft.generationModel !== config.generationModel ||
    draft.embeddingModel !== config.embeddingModel ||
    draft.maxStoresPerQuery !== config.maxStoresPerQuery ||
    draft.maxMediaPerQuery !== config.maxMediaPerQuery;

  // ── Data loading ──────────────────────────────────────────
  const loadConfig = useCallback(
    async (showLoading = true) => {
      if (showLoading) setIsLoading(true);
      try {
        const loaded = await GeminiFileSearchApi.getConfig();
        setConfig(loaded);
        setDraft(loaded);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Please try again.';
        addToast({
          variant: 'error',
          title: 'Failed to load Gemini File Search settings',
          description: message,
          duration: 5000,
        });
      } finally {
        if (showLoading) setIsLoading(false);
      }
    },
    [addToast],
  );

  useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  // ── Handlers ──────────────────────────────────────────────
  const handleSave = useCallback(async () => {
    if (!isDirty) return;
    setIsSaving(true);
    const previous = config;
    try {
      const updated = await GeminiFileSearchApi.updateConfig(draft);
      setConfig(updated);
      setDraft(updated);
      addToast({
        variant: 'success',
        title: 'Gemini File Search settings saved',
        duration: 3000,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Please try again.';
      setConfig(previous);
      setDraft(previous);
      addToast({
        variant: 'error',
        title: 'Failed to save Gemini File Search settings',
        description: message,
        duration: 5000,
      });
    } finally {
      setIsSaving(false);
    }
  }, [draft, config, isDirty, addToast]);

  // ── Guard ─────────────────────────────────────────────────
  if (!isProfileInitialized || isAdmin === false) {
    return null;
  }

  // ── Loading state ─────────────────────────────────────────
  if (isLoading) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', width: '100%' }}>
        <LottieLoader variant="loader" size={48} showLabel label="Loading Gemini File Search settings…" />
      </Flex>
    );
  }

  // ── Render ────────────────────────────────────────────────
  return (
    <ServiceGate services={['query']}>
      <Box style={{ height: '100%', overflowY: 'auto', position: 'relative' }}>
        <Box style={{ padding: '64px 100px 80px', maxWidth: 900 }}>
          {/* ── Page header ── */}
          <Flex align="start" justify="between" style={{ marginBottom: 'var(--space-6)' }}>
            <Box>
              <Heading size="6" style={{ color: 'var(--slate-12)' }}>
                Gemini File Search
              </Heading>
              <Text
                size="2"
                style={{ color: 'var(--slate-10)', marginTop: 'var(--space-1)', display: 'block' }}
              >
                Augment PipesHub&apos;s retrieval with Google&apos;s multimodal File Search — adds image,
                chart, and diagram understanding to your knowledge bases.
              </Text>
            </Box>
          </Flex>

          {/* ── Enablement section ── */}
          <Flex
            direction="column"
            style={{
              border: '1px solid var(--olive-3)',
              borderRadius: 'var(--radius-1)',
              background: 'var(--olive-2)',
              marginBottom: 'var(--space-5)',
            }}
          >
            <Flex align="center" justify="between" style={{ padding: '12px 14px' }}>
              <Flex align="center" gap="3">
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
                  <MaterialIcon name="document_scanner" size={18} color="var(--slate-11)" />
                </Flex>
                <Box style={{ flex: 1, minWidth: 0 }}>
                  <Text
                    size="2"
                    weight="medium"
                    style={{ color: 'var(--slate-12)', display: 'block' }}
                  >
                    Enable Gemini File Search
                  </Text>
                  <Text
                    size="1"
                    style={{
                      color: 'var(--slate-10)',
                      display: 'block',
                      marginTop: 2,
                      fontWeight: 300,
                    }}
                  >
                    Uses the API key from your Gemini provider card in AI Models — no separate key needed.
                  </Text>
                </Box>
              </Flex>

              <Flex align="center" style={{ flexShrink: 0 }}>
                <Switch
                  color="jade"
                  size="2"
                  checked={draft.enabled}
                  disabled={isSaving}
                  onCheckedChange={(checked) => setDraft((d) => ({ ...d, enabled: checked }))}
                />
              </Flex>
            </Flex>

            {/* ── Model + limits config — shown when enabled ── */}
            {draft.enabled && (
              <Flex
                direction="column"
                gap="3"
                style={{
                  padding: '12px 14px',
                  borderTop: '1px solid var(--olive-3)',
                }}
              >
                <Callout.Root color="amber" size="1" variant="soft">
                  <Callout.Icon>
                    <MaterialIcon name="info" size={14} color="var(--amber-11)" />
                  </Callout.Icon>
                  <Callout.Text>
                    Indexed files are billed for embeddings at indexing time; queries incur normal
                    Gemini token costs. Storage is free. One File Search store is created per
                    knowledge base.
                  </Callout.Text>
                </Callout.Root>

                <ConfigField
                  label="Generation model"
                  hint="Gemini model used to answer at query time."
                  value={draft.generationModel}
                  placeholder="gemini-3.5-flash"
                  disabled={isSaving}
                  onCommit={(value) =>
                    setDraft((d) => ({ ...d, generationModel: value || DEFAULT_CONFIG.generationModel }))
                  }
                />

                <ConfigField
                  label="Embedding model"
                  hint="Must be a multimodal model to search images (use models/gemini-embedding-2)."
                  value={draft.embeddingModel}
                  placeholder="models/gemini-embedding-2"
                  disabled={isSaving}
                  onCommit={(value) =>
                    setDraft((d) => ({ ...d, embeddingModel: value || DEFAULT_CONFIG.embeddingModel }))
                  }
                />

                <NumberField
                  label="Max stores per query"
                  hint="Maximum Gemini File Search stores queried per chat turn (Gemini API limit: 5)."
                  value={draft.maxStoresPerQuery}
                  min={1}
                  max={5}
                  disabled={isSaving}
                  onCommit={(value) => setDraft((d) => ({ ...d, maxStoresPerQuery: value }))}
                />

                <NumberField
                  label="Max media per query"
                  hint="Maximum cited images downloaded and surfaced per chat turn."
                  value={draft.maxMediaPerQuery}
                  min={0}
                  max={20}
                  disabled={isSaving}
                  onCommit={(value) => setDraft((d) => ({ ...d, maxMediaPerQuery: value }))}
                />
              </Flex>
            )}
          </Flex>

          {/* ── Save bar ── */}
          {isDirty && (
            <Flex
              align="center"
              justify="between"
              style={{
                position: 'sticky',
                bottom: 0,
                padding: '12px 16px',
                borderTop: '1px solid var(--slate-5)',
                background: 'var(--color-panel-solid)',
                borderRadius: 'var(--radius-2)',
              }}
            >
              <Text size="2" style={{ color: 'var(--slate-11)' }}>
                You have unsaved changes
              </Text>
              <Flex gap="2">
                <button
                  type="button"
                  onClick={() => setDraft(config)}
                  disabled={isSaving}
                  style={{
                    cursor: 'pointer',
                    border: '1px solid var(--slate-5)',
                    background: 'transparent',
                    borderRadius: 'var(--radius-2)',
                    padding: '6px 14px',
                    color: 'var(--slate-12)',
                    fontSize: 14,
                  }}
                >
                  Discard
                </button>
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={isSaving}
                  style={{
                    cursor: isSaving ? 'wait' : 'pointer',
                    border: 'none',
                    background: 'var(--jade-9)',
                    borderRadius: 'var(--radius-2)',
                    padding: '6px 16px',
                    color: 'white',
                    fontSize: 14,
                    fontWeight: 500,
                    opacity: isSaving ? 0.7 : 1,
                  }}
                >
                  {isSaving ? 'Saving…' : 'Save changes'}
                </button>
              </Flex>
            </Flex>
          )}
        </Box>
      </Box>
    </ServiceGate>
  );
}

// ============================================================
// Reusable text + number field rows (match web-search styling)
// ============================================================

interface ConfigFieldProps {
  label: string;
  hint: string;
  value: string;
  placeholder: string;
  disabled?: boolean;
  onCommit: (value: string) => void;
}

function ConfigField({ label, hint, value, placeholder, disabled, onCommit }: ConfigFieldProps) {
  const [inputValue, setInputValue] = useState(value);

  useEffect(() => {
    setInputValue(value);
  }, [value]);

  return (
    <Flex direction="column" gap="1" style={{ padding: '4px 0' }}>
      <Text size="1" weight="medium" style={{ color: 'var(--slate-12)' }}>
        {label}
      </Text>
      <TextField.Root
        type="text"
        value={inputValue}
        placeholder={placeholder}
        onChange={(e) => setInputValue(e.target.value)}
        onBlur={() => onCommit(inputValue.trim())}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
        }}
        disabled={disabled}
        style={{ maxWidth: 360 }}
      />
      <Text size="1" style={{ color: 'var(--slate-10)', fontWeight: 300 }}>
        {hint}
      </Text>
    </Flex>
  );
}

interface NumberFieldProps {
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  disabled?: boolean;
  onCommit: (value: number) => void;
}

function NumberField({ label, hint, value, min, max, disabled, onCommit }: NumberFieldProps) {
  const [inputValue, setInputValue] = useState(String(value));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setInputValue(String(value));
    setError(null);
  }, [value]);

  const handleCommit = () => {
    const trimmed = inputValue.trim();
    if (trimmed === '') return;
    const parsed = Number(trimmed);
    if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
      setError(`Enter a whole number between ${min} and ${max}`);
      setInputValue(String(value));
      return;
    }
    setError(null);
    if (parsed !== value) onCommit(parsed);
  };

  return (
    <Flex direction="column" gap="1" style={{ padding: '4px 0' }}>
      <Text size="1" weight="medium" style={{ color: 'var(--slate-12)' }}>
        {label}
      </Text>
      <TextField.Root
        type="number"
        min={min}
        max={max}
        step={1}
        value={inputValue}
        onChange={(e) => setInputValue(e.target.value)}
        onBlur={handleCommit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
        }}
        disabled={disabled}
        style={{ maxWidth: 220 }}
      />
      <Text size="1" style={{ color: error ? 'var(--red-10)' : 'var(--slate-10)', fontWeight: 300 }}>
        {error ?? hint}
      </Text>
    </Flex>
  );
}
