'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import { Badge, Box, Flex, Heading, Switch, Text } from '@radix-ui/themes';
import { SettingsSaveBar, SettingsSection } from '../components';
import { SettingsPageContainer } from '@/app/(main)/workspace/components/settings-page-container';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { useToastStore } from '@/lib/store/toast-store';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { McpAccessApi, type McpAccessPolicy, type McpAccessTool } from './api';

const samePolicy = (a: McpAccessPolicy, b: McpAccessPolicy): boolean =>
  a.enabled === b.enabled &&
  a.allowWriteTools === b.allowWriteTools &&
  a.disabledTools.length === b.disabledTools.length &&
  a.disabledTools.every((name) => b.disabledTools.includes(name));

function SwitchRow({
  label,
  description,
  checked,
  disabled,
  onCheckedChange,
  badge,
}: {
  label: string;
  description?: string;
  checked: boolean;
  disabled?: boolean;
  onCheckedChange: (value: boolean) => void;
  badge?: React.ReactNode;
}) {
  return (
    <Flex align="center" justify="between" style={{ width: '100%', gap: 'var(--space-4)', minHeight: 44 }}>
      <Box style={{ flex: 1, minWidth: 0 }}>
        <Flex align="center" gap="2" wrap="wrap">
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {label}
          </Text>
          {badge}
        </Flex>
        {description && (
          <Text
            size="1"
            style={{ color: 'var(--slate-11)', display: 'block', marginTop: 2, lineHeight: '16px', fontWeight: 300, wordBreak: 'break-word' }}
          >
            {description}
          </Text>
        )}
      </Box>
      <Switch
        size="1"
        checked={checked}
        disabled={disabled}
        onCheckedChange={onCheckedChange}
        aria-label={label}
        style={{ flexShrink: 0, cursor: disabled ? 'default' : 'pointer' }}
      />
    </Flex>
  );
}

export default function McpAccessPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const addToast = useToastStore((s) => s.addToast);
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);

  const [saved, setSaved] = useState<McpAccessPolicy | null>(null);
  const [form, setForm] = useState<McpAccessPolicy | null>(null);
  const [tools, setTools] = useState<McpAccessTool[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

  useEffect(() => {
    if (!isProfileInitialized || isAdmin === false) return;
    let cancelled = false;
    void (async () => {
      const [policyResult, toolsResult] = await Promise.allSettled([
        McpAccessApi.getPolicy(),
        McpAccessApi.getTools(),
      ]);
      if (cancelled) return;
      if (policyResult.status === 'fulfilled') {
        setSaved(policyResult.value);
        setForm(policyResult.value);
      } else {
        setLoadFailed(true);
      }
      setTools(toolsResult.status === 'fulfilled' ? toolsResult.value : []);
    })();
    return () => {
      cancelled = true;
    };
  }, [isProfileInitialized, isAdmin]);

  const setToolOn = useCallback((name: string, on: boolean) => {
    setForm((current) =>
      current && {
        ...current,
        disabledTools: on
          ? current.disabledTools.filter((n) => n !== name)
          : [...current.disabledTools.filter((n) => n !== name), name],
      },
    );
  }, []);

  const handleSave = useCallback(async () => {
    if (!form) return;
    setIsSaving(true);
    try {
      const stored = await McpAccessApi.savePolicy(form);
      setSaved(stored);
      setForm(stored);
      addToast({ variant: 'success', title: t('workspace.mcpAccess.toasts.saveSuccess') });
    } catch {
      addToast({
        variant: 'error',
        title: t('workspace.mcpAccess.toasts.saveError'),
        description: t('workspace.mcpAccess.toasts.saveErrorDescription'),
      });
    } finally {
      setIsSaving(false);
    }
  }, [form, addToast, t]);

  if (!isProfileInitialized || isAdmin === false) return null;

  if (!loadFailed && (!form || tools === null)) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%', width: '100%' }}>
        <LottieLoader variant="loader" size={48} showLabel />
      </Flex>
    );
  }

  const dirty = !!form && !!saved && !samePolicy(form, saved);

  return (
    <Box style={{ height: '100%', overflowY: 'auto' }}>
      <SettingsPageContainer>
        <Box style={{ marginBottom: 'var(--space-6)' }}>
          <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('workspace.mcpAccess.title')}
          </Heading>
          <Text size="2" style={{ color: 'var(--slate-10)', marginTop: 'var(--space-1)', display: 'block' }}>
            {t('workspace.mcpAccess.subtitle')}
          </Text>
        </Box>

        {loadFailed || !form ? (
          <Text size="2" style={{ color: 'var(--red-a11)' }}>
            {t('workspace.mcpAccess.loadError')}
          </Text>
        ) : (
          <Flex direction="column" gap="5" style={{ paddingBottom: 'var(--space-9)' }}>
            <SettingsSection>
              <SwitchRow
                label={t('workspace.mcpAccess.enabled.label')}
                description={t('workspace.mcpAccess.enabled.description')}
                checked={form.enabled}
                onCheckedChange={(enabled) => setForm({ ...form, enabled })}
              />
              <SwitchRow
                label={t('workspace.mcpAccess.writeTools.label')}
                description={t('workspace.mcpAccess.writeTools.description')}
                checked={form.allowWriteTools}
                disabled={!form.enabled}
                onCheckedChange={(allowWriteTools) => setForm({ ...form, allowWriteTools })}
              />
            </SettingsSection>

            {tools && tools.length > 0 && (
              <SettingsSection
                title={t('workspace.mcpAccess.tools.title')}
                description={t('workspace.mcpAccess.tools.subtitle')}
              >
                {tools.map((tool) => {
                  // The server drops pipeshub_agents along with pipeshub_chat.
                  const blocked =
                    (tool.writes && !form.allowWriteTools) ||
                    (tool.name === 'pipeshub_agents' &&
                      form.disabledTools.includes('pipeshub_chat'));
                  return (
                    <SwitchRow
                      key={tool.name}
                      label={tool.title}
                      description={tool.title === tool.name ? undefined : tool.name}
                      badge={
                        tool.writes ? (
                          <Badge size="1" color="amber" variant="soft">
                            {t('workspace.mcpAccess.tools.writes')}
                          </Badge>
                        ) : undefined
                      }
                      checked={!blocked && !form.disabledTools.includes(tool.name)}
                      disabled={!form.enabled || blocked}
                      onCheckedChange={(on) => setToolOn(tool.name, on)}
                    />
                  );
                })}
              </SettingsSection>
            )}

            <Text size="1" style={{ color: 'var(--slate-11)' }}>
              {t('workspace.mcpAccess.appliesNote')}
            </Text>
          </Flex>
        )}
      </SettingsPageContainer>

      <SettingsSaveBar
        visible={dirty}
        isSaving={isSaving}
        onDiscard={() => saved && setForm(saved)}
        onSave={handleSave}
      />
    </Box>
  );
}
