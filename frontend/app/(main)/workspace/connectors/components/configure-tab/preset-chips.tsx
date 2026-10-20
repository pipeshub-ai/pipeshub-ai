'use client';

import React, { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, RadioCards, Text } from '@radix-ui/themes';
import { ConfirmationDialog } from '@/app/(main)/workspace/components/confirmation-dialog';
import { useWorkspaceDrawerNestedModalHost } from '@/app/(main)/workspace/components/workspace-right-panel';
import { matchPreset, type ConnectorPreset } from '../../utils/connector-presets';

const CUSTOM = '__custom';

/**
 * "Start from" choices that fill several settings at once. Shows "Custom" when
 * edited values match no preset (untouched defaults show no choice); replacing custom values asks first.
 */
export function PresetChips({
  typeKey,
  presets,
  values,
  defaults,
  suggestedId,
  confirmWhenCustom,
  disabled = false,
  onApply,
}: {
  typeKey: string;
  presets: ConnectorPreset[];
  values: Record<string, unknown>;
  defaults: Record<string, unknown>;
  suggestedId?: string | null;
  /** Ask before replacing values that match no preset. */
  confirmWhenCustom: boolean;
  disabled?: boolean;
  onApply: (values: Record<string, unknown>) => void;
}) {
  const { t } = useTranslation();
  const labelId = useId();
  const [pending, setPending] = useState<ConnectorPreset | null>(null);
  const modalHost = useWorkspaceDrawerNestedModalHost(true);
  const matched = matchPreset(presets, values, defaults);
  // Untouched settings (a new connector) aren't "Custom": nothing is chosen yet.
  const atDefaults = presets
    .flatMap((p) => Object.keys(p.values))
    .every((key) => String(values[key] ?? defaults[key]) === String(defaults[key]));
  const custom = matched === null && !atDefaults;
  const name = (id: string) => t(`workspace.connectors.presets.${typeKey}.${id}`);

  const choose = (id: string) => {
    const preset = presets.find((p) => p.id === id);
    if (!preset || id === matched) return;
    if (custom && confirmWhenCustom) setPending(preset);
    else onApply(preset.values);
  };

  return (
    <Flex direction="column" gap="2">
      <Text id={labelId} size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
        {t('workspace.connectors.presets.title')}
      </Text>
      <RadioCards.Root
        size="1"
        value={matched ?? (custom ? CUSTOM : '')}
        onValueChange={choose}
        disabled={disabled}
        aria-labelledby={labelId}
        columns="repeat(auto-fill, minmax(128px, 1fr))"
        gap="2"
      >
        {presets.map((preset) => (
          <RadioCards.Item key={preset.id} value={preset.id} style={{ minHeight: 44 }}>
            <Text size="2">{name(preset.id)}</Text>
          </RadioCards.Item>
        ))}
        {custom ? (
          <RadioCards.Item value={CUSTOM} disabled style={{ minHeight: 44 }}>
            <Text size="2">{t('workspace.connectors.presets.custom')}</Text>
          </RadioCards.Item>
        ) : null}
      </RadioCards.Root>
      {suggestedId && suggestedId !== matched ? (
        <Text size="1" style={{ color: 'var(--gray-11)' }}>
          {t('workspace.connectors.presets.suggested', { name: name(suggestedId) })}
        </Text>
      ) : null}
      <ConfirmationDialog
        open={pending !== null}
        onOpenChange={(open) => {
          if (!open) setPending(null);
        }}
        container={modalHost}
        title={t('workspace.connectors.presets.replaceTitle')}
        message={t('workspace.connectors.presets.replaceMessage', { name: pending ? name(pending.id) : '' })}
        confirmLabel={t('workspace.connectors.presets.replaceConfirm')}
        cancelLabel={t('common.cancel')}
        confirmVariant="primary"
        onConfirm={() => {
          if (pending) onApply(pending.values);
          setPending(null);
        }}
      />
    </Flex>
  );
}
