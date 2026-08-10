'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Box, Button, Checkbox, Flex, IconButton, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { CheckedState } from '@radix-ui/react-checkbox';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FormField, WorkspaceRightPanel } from '../../../components';
import { useToastStore } from '@/lib/store/toast-store';
import { getApiBaseUrl } from '@/lib/utils/api-base-url';
import { PatApi } from '../api';
import type { PatExpiryOption, PatScopeItem } from '../types';

// ========================================
// Constants
// ========================================

const EXPIRY_OPTIONS: { value: PatExpiryOption; labelKey: string }[] = [
  { value: 30, labelKey: 'workspace.personalAccessTokens.create.expiry30Days' },
  { value: 90, labelKey: 'workspace.personalAccessTokens.create.expiry90Days' },
  { value: 365, labelKey: 'workspace.personalAccessTokens.create.expiry365Days' },
  { value: 'never', labelKey: 'workspace.personalAccessTokens.create.expiryNever' },
];

// Shortest option, not the longest — a token minted without a second
// thought shouldn't default to the widest blast radius.
const DEFAULT_EXPIRY_DAYS: PatExpiryOption = 30;

const CARD_STYLE: React.CSSProperties = {
  backgroundColor: 'var(--olive-2)',
  border: '1px solid var(--olive-3)',
  borderRadius: 'var(--radius-2)',
  padding: 'var(--space-4)',
};

const CREDENTIAL_MONO: React.CSSProperties = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
};

// ========================================
// Props
// ========================================

export interface CreatePatPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: () => void;
}

// ========================================
// Component
// ========================================

export function CreatePatPanel({ open, onOpenChange, onCreated }: CreatePatPanelProps) {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);

  const [name, setName] = useState('');
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [expiryDays, setExpiryDays] = useState<PatExpiryOption>(DEFAULT_EXPIRY_DAYS);

  const [scopes, setScopes] = useState<PatScopeItem[] | null>(null);
  const [scopesLoading, setScopesLoading] = useState(false);
  // Stored as a translation key, not the translated string — `t`'s
  // identity changes on a language switch, and depending on `t` in
  // loadScopes below would re-run it while the panel is open, resetting
  // selectedScopes to every scope and discarding the user's picks.
  const [scopesErrorKey, setScopesErrorKey] = useState<string | null>(null);
  const [selectedScopes, setSelectedScopes] = useState<Set<string>>(() => new Set());

  const [isCreating, setIsCreating] = useState(false);
  const [showToken, setShowToken] = useState(false);
  const [createdToken, setCreatedToken] = useState<{
    accessToken: string;
    pasteBlock: string;
  } | null>(null);

  const sortedScopesByCategory = useMemo(() => {
    if (!scopes) return [] as [string, PatScopeItem[]][];
    const byCategory = new Map<string, PatScopeItem[]>();
    for (const scope of scopes) {
      const list = byCategory.get(scope.category) ?? [];
      list.push(scope);
      byCategory.set(scope.category, list);
    }
    return Array.from(byCategory.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [scopes]);

  const totalScopeCount = scopes?.length ?? 0;
  const allScopesSelected = totalScopeCount > 0 && selectedScopes.size === totalScopeCount;

  const toggleSelectAllScopes = useCallback(() => {
    if (allScopesSelected) {
      setSelectedScopes(new Set());
    } else {
      setSelectedScopes(new Set((scopes ?? []).map((s) => s.name)));
    }
  }, [allScopesSelected, scopes]);

  const loadScopes = useCallback(async () => {
    setScopesLoading(true);
    setScopesErrorKey(null);
    try {
      const data = await PatApi.getScopes();
      setScopes(data.scopes ?? []);
      // Default to the full MCP scope set, matching the backend default
      // when no scopes are supplied.
      setSelectedScopes(new Set((data.scopes ?? []).map((s) => s.name)));
    } catch {
      setScopesErrorKey('workspace.personalAccessTokens.create.scopesLoadError');
    } finally {
      setScopesLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) {
      setName('');
      setNameError(undefined);
      setExpiryDays(DEFAULT_EXPIRY_DAYS);
      setScopes(null);
      setScopesErrorKey(null);
      setSelectedScopes(new Set());
      setIsCreating(false);
      setShowToken(false);
      setCreatedToken(null);
      return;
    }
    void loadScopes();
  }, [open, loadScopes]);

  const handleClose = useCallback(() => {
    onOpenChange(false);
  }, [onOpenChange]);

  const copyToClipboard = useCallback(
    async (text: string) => {
      try {
        await navigator.clipboard.writeText(text);
        addToast({
          variant: 'success',
          title: t('workspace.personalAccessTokens.create.copySuccessTitle'),
          duration: 2500,
        });
      } catch {
        addToast({
          variant: 'error',
          title: t('workspace.personalAccessTokens.create.copyErrorTitle'),
          duration: 4000,
        });
      }
    },
    [addToast, t]
  );

  const toggleScope = useCallback((scopeName: string, checked: boolean) => {
    setSelectedScopes((prev) => {
      const next = new Set(prev);
      if (checked) next.add(scopeName);
      else next.delete(scopeName);
      return next;
    });
  }, []);

  const toggleCategory = useCallback((items: PatScopeItem[], checked: CheckedState) => {
    setSelectedScopes((prev) => {
      const next = new Set(prev);
      if (checked === true) {
        items.forEach((i) => next.add(i.name));
      } else {
        items.forEach((i) => next.delete(i.name));
      }
      return next;
    });
  }, []);

  const handleCreate = useCallback(async () => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      setNameError(t('workspace.personalAccessTokens.create.errorNameRequired'));
      return;
    }
    if (selectedScopes.size === 0) {
      return;
    }
    setIsCreating(true);
    try {
      const res = await PatApi.createToken({
        name: trimmedName,
        scopes: Array.from(selectedScopes),
        expiryDays,
      });
      const accessToken = res?.token?.accessToken;
      if (!accessToken) {
        addToast({
          variant: 'error',
          title: t('workspace.personalAccessTokens.create.errorCreateTitle'),
          duration: 5000,
        });
        return;
      }
      const origin = getApiBaseUrl() || window.location.origin;
      const pasteBlock = `PIPESHUB_MCP_URL=${origin}/mcp\nPIPESHUB_MCP_TOKEN=${accessToken}`;
      setCreatedToken({ accessToken, pasteBlock });
      onCreated?.();
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.personalAccessTokens.create.errorCreateTitle'),
        duration: 5000,
      });
    } finally {
      setIsCreating(false);
    }
  }, [name, selectedScopes, expiryDays, addToast, t, onCreated]);

  const onPrimaryClick = useCallback(() => {
    if (createdToken) {
      handleClose();
      return;
    }
    void handleCreate();
  }, [createdToken, handleClose, handleCreate]);

  const primaryDisabled =
    createdToken ? false : !name.trim() || scopesLoading || selectedScopes.size === 0;

  return (
    <WorkspaceRightPanel
      open={open}
      onOpenChange={onOpenChange}
      title={
        createdToken
          ? t('workspace.personalAccessTokens.create.successPanelTitle')
          : t('workspace.personalAccessTokens.create.panelTitle')
      }
      icon={<MaterialIcon name="vpn_key" size={20} color="var(--slate-12)" />}
      primaryLabel={
        createdToken
          ? t('workspace.personalAccessTokens.create.doneButton')
          : t('workspace.personalAccessTokens.create.createButton')
      }
      secondaryLabel={
        createdToken ? undefined : t('workspace.personalAccessTokens.create.cancel')
      }
      primaryDisabled={primaryDisabled}
      primaryLoading={createdToken ? false : isCreating}
      onPrimaryClick={onPrimaryClick}
      onSecondaryClick={createdToken ? undefined : handleClose}
    >
      {createdToken ? (
        <Box style={CARD_STYLE}>
          <Flex direction="column" gap="4">
            <Flex align="start" gap="3">
              <Box
                style={{
                  width: 40,
                  height: 40,
                  borderRadius: 'var(--radius-2)',
                  backgroundColor: 'var(--accent-9)',
                  flexShrink: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <MaterialIcon name="check" size={22} color="white" />
              </Box>
              <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
                <Text size="4" weight="bold" style={{ color: 'var(--slate-12)' }}>
                  {t('workspace.personalAccessTokens.create.successScreenTitle')}
                </Text>
                <Text size="2" style={{ color: 'var(--slate-11)' }}>
                  {t('workspace.personalAccessTokens.create.successScreenSubtitle')}
                </Text>
              </Flex>
            </Flex>

            <Flex direction="column" gap="2">
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {t('workspace.personalAccessTokens.create.tokenLabel')}
              </Text>
              <Flex align="center" gap="2" style={{ width: '100%' }}>
                <TextField.Root
                  size="2"
                  readOnly
                  type={showToken ? 'text' : 'password'}
                  value={createdToken.accessToken}
                  style={{ flex: 1, minWidth: 0, ...CREDENTIAL_MONO }}
                />
                <IconButton
                  type="button"
                  variant="soft"
                  color="gray"
                  size="2"
                  aria-label={
                    showToken
                      ? t('workspace.personalAccessTokens.create.hideTokenAria')
                      : t('workspace.personalAccessTokens.create.showTokenAria')
                  }
                  onClick={() => setShowToken((v) => !v)}
                  style={{ flexShrink: 0, cursor: 'pointer' }}
                >
                  <MaterialIcon
                    name={showToken ? 'visibility_off' : 'visibility'}
                    size={18}
                    color="var(--slate-11)"
                  />
                </IconButton>
                <IconButton
                  type="button"
                  variant="soft"
                  color="gray"
                  size="2"
                  aria-label={t('workspace.personalAccessTokens.create.copyTokenAria')}
                  onClick={() => void copyToClipboard(createdToken.accessToken)}
                  style={{ flexShrink: 0, cursor: 'pointer' }}
                >
                  <MaterialIcon name="content_copy" size={18} color="var(--slate-11)" />
                </IconButton>
              </Flex>
              <Text size="1" style={{ color: 'var(--amber-11)' }}>
                {t('workspace.personalAccessTokens.create.tokenShownOnceWarning')}
              </Text>
            </Flex>

            <Flex direction="column" gap="2">
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {t('workspace.personalAccessTokens.create.pasteBlockLabel')}
              </Text>
              <Box
                style={{
                  position: 'relative',
                  backgroundColor: 'var(--gray-2)',
                  border: '1px solid var(--slate-a5)',
                  borderRadius: 'var(--radius-2)',
                  padding: 'var(--space-3)',
                }}
              >
                <pre
                  style={{
                    margin: 0,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all',
                    fontSize: 12,
                    ...CREDENTIAL_MONO,
                    color: 'var(--slate-12)',
                  }}
                >
                  {createdToken.pasteBlock}
                </pre>
                <IconButton
                  type="button"
                  variant="soft"
                  color="gray"
                  size="1"
                  aria-label={t('workspace.personalAccessTokens.create.copyPasteBlockAria')}
                  onClick={() => void copyToClipboard(createdToken.pasteBlock)}
                  style={{ position: 'absolute', top: 8, right: 8, cursor: 'pointer' }}
                >
                  <MaterialIcon name="content_copy" size={16} color="var(--slate-11)" />
                </IconButton>
              </Box>
            </Flex>
          </Flex>
        </Box>
      ) : (
        <Flex direction="column" gap="4">
          <Box style={CARD_STYLE}>
            <FormField
              label={t('workspace.personalAccessTokens.create.nameLabel')}
              required
              error={nameError}
            >
              <TextField.Root
                size="2"
                placeholder={t('workspace.personalAccessTokens.create.namePlaceholder')}
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  if (nameError) setNameError(undefined);
                }}
                style={{ width: '100%' }}
              />
            </FormField>
          </Box>

          <Box style={CARD_STYLE}>
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('workspace.personalAccessTokens.create.expiryHeading')}
            </Text>
            <Flex wrap="wrap" gap="3" style={{ marginTop: 'var(--space-2)' }}>
              {EXPIRY_OPTIONS.map((opt) => (
                <Flex key={String(opt.value)} align="center" gap="2">
                  <input
                    type="radio"
                    name="pat-expiry"
                    checked={expiryDays === opt.value}
                    onChange={() => setExpiryDays(opt.value)}
                    id={`pat-expiry-${opt.value}`}
                  />
                  <label htmlFor={`pat-expiry-${opt.value}`}>
                    <Text size="2" style={{ color: 'var(--slate-12)' }}>
                      {t(opt.labelKey)}
                    </Text>
                  </label>
                </Flex>
              ))}
            </Flex>
          </Box>

          <Flex align="start" justify="between" gap="3" wrap="wrap">
            <Flex direction="column" gap="1" style={{ minWidth: 0, flex: 1 }}>
              <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {t('workspace.personalAccessTokens.create.permissionsHeading')}
              </Text>
              {selectedScopes.size === 0 && !scopesLoading && (
                <Text size="2" style={{ color: 'var(--amber-11)', marginTop: 4 }}>
                  {t('workspace.personalAccessTokens.create.errorScopeRequired')}
                </Text>
              )}
            </Flex>
            <Button
              type="button"
              variant="outline"
              color="gray"
              size="2"
              onClick={toggleSelectAllScopes}
              disabled={totalScopeCount === 0 || scopesLoading}
              style={{ flexShrink: 0, cursor: 'pointer' }}
            >
              {allScopesSelected
                ? t('workspace.personalAccessTokens.create.clearAllScopes')
                : t('workspace.personalAccessTokens.create.selectAllScopes')}
            </Button>
          </Flex>

          {scopesLoading && (
            <Text size="2" style={{ color: 'var(--slate-11)' }}>
              {t('workspace.personalAccessTokens.create.scopesLoading')}
            </Text>
          )}

          {scopesErrorKey && !scopesLoading && (
            <Flex direction="column" gap="2" align="start">
              <Text size="2" style={{ color: 'var(--red-11)' }}>
                {t(scopesErrorKey)}
              </Text>
              <Button size="2" variant="soft" onClick={() => void loadScopes()}>
                {t('workspace.personalAccessTokens.create.scopesRetry')}
              </Button>
            </Flex>
          )}

          {!scopesLoading &&
            !scopesErrorKey &&
            sortedScopesByCategory.map(([category, items]) => {
              const selectedInCat = items.filter((i) => selectedScopes.has(i.name)).length;
              const totalInCat = items.length;
              const groupChecked: CheckedState =
                selectedInCat === totalInCat ? true : selectedInCat === 0 ? false : 'indeterminate';

              return (
                <Box key={category} style={{ ...CARD_STYLE, padding: 'var(--space-3)' }}>
                  <Flex align="center" justify="between" gap="3" wrap="wrap">
                    <Flex align="center" gap="2" style={{ minWidth: 0 }}>
                      <Checkbox
                        checked={groupChecked}
                        onCheckedChange={(c) => toggleCategory(items, c as CheckedState)}
                      />
                      <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                        {category}
                      </Text>
                    </Flex>
                    <Text size="2" style={{ color: 'var(--slate-11)' }}>
                      {selectedInCat}/{totalInCat}
                    </Text>
                  </Flex>
                  <Flex direction="column" gap="2" style={{ marginTop: 'var(--space-3)' }}>
                    {items.map((scope) => (
                      <Flex
                        key={scope.name}
                        align="start"
                        gap="2"
                        style={{
                          padding: 'var(--space-2)',
                          borderRadius: 'var(--radius-2)',
                          border: '1px solid var(--olive-4)',
                        }}
                      >
                        <Checkbox
                          checked={selectedScopes.has(scope.name)}
                          onCheckedChange={(c) => toggleScope(scope.name, Boolean(c))}
                          style={{ marginTop: 2 }}
                        />
                        <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
                          <Text size="2" weight="medium" style={{ ...CREDENTIAL_MONO, color: 'var(--slate-12)' }}>
                            {scope.name}
                          </Text>
                          <Text size="2" style={{ color: 'var(--slate-11)' }}>
                            {scope.description}
                          </Text>
                        </Flex>
                      </Flex>
                    ))}
                  </Flex>
                </Box>
              );
            })}
        </Flex>
      )}
    </WorkspaceRightPanel>
  );
}
