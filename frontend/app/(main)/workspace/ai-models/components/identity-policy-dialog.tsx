'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Button, Dialog, Flex, Select, Switch, Text, TextArea, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { toast } from '@/lib/store/toast-store';
import { AIModelsApi } from '../api';
import type { IdentityPolicy } from '../types';

const MAX_GROUPS = 500;

function SwitchRow({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <Flex align="start" justify="between" gap="3">
      <Flex direction="column" gap="1">
        <Text size="2" weight="medium">{label}</Text>
        {hint ? <Text size="1" style={{ color: 'var(--gray-11)' }}>{hint}</Text> : null}
      </Flex>
      <Switch aria-label={label} checked={checked} onCheckedChange={onChange} />
    </Flex>
  );
}

interface IdentityPolicyDialogProps {
  open: boolean;
  onClose: () => void;
}

export function IdentityPolicyDialog({ open, onClose }: IdentityPolicyDialogProps) {
  const { t } = useTranslation();
  const [policy, setPolicy] = useState<IdentityPolicy | null>(null);
  const [allowList, setAllowList] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [damaged, setDamaged] = useState(false);

  const load = useCallback(async () => {
    // A policy from an earlier opening must not be savable while this load is pending.
    setPolicy(null);
    setDamaged(false);
    setError(null);
    try {
      const loaded = await AIModelsApi.getIdentityPolicy();
      setPolicy(loaded.policy);
      setDamaged(loaded.damaged);
      setAllowList(loaded.policy.groups.allowList.join('\n'));
    } catch {
      setPolicy(null);
      setError(t('workspace.aiModels.identityPolicy.loadFailed'));
    }
  }, [t]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const update = (patch: Partial<IdentityPolicy>) =>
    setPolicy((current) => (current ? { ...current, ...patch } : current));

  const save = async () => {
    if (!policy) return;
    setSaving(true);
    setError(null);
    try {
      const ids = allowList
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);
      await AIModelsApi.putIdentityPolicy({
        ...policy,
        groups: {
          ...policy.groups,
          allowList: ids,
          maxForwarded: Math.min(Math.max(Math.trunc(policy.groups.maxForwarded) || 1, 1), MAX_GROUPS),
        },
      });
      toast.success(t('workspace.aiModels.identityPolicy.saved'));
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('workspace.aiModels.identityPolicy.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <Dialog.Content style={{ maxWidth: 560 }}>
        <Dialog.Title>{t('workspace.aiModels.identityPolicy.title')}</Dialog.Title>
        <Flex direction="column" gap="4">
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.aiModels.identityPolicy.hint')}
          </Text>

          {policy ? (
            <>
              <Flex direction="column" gap="1">
                <Text size="2" weight="medium">{t('workspace.aiModels.identityPolicy.subjectSource')}</Text>
                <Select.Root
                  value={policy.subjectSource}
                  onValueChange={(value) => update({ subjectSource: value as IdentityPolicy['subjectSource'] })}
                >
                  <Select.Trigger />
                  <Select.Content>
                    <Select.Item value="userId">{t('workspace.aiModels.identityPolicy.subjectUserId')}</Select.Item>
                    <Select.Item value="email">{t('workspace.aiModels.identityPolicy.subjectEmail')}</Select.Item>
                    <Select.Item value="idpObjectId">{t('workspace.aiModels.identityPolicy.subjectIdp')}</Select.Item>
                  </Select.Content>
                </Select.Root>
              </Flex>

              <SwitchRow
                label={t('workspace.aiModels.identityPolicy.includeEmail')}
                checked={policy.includeEmail}
                onChange={(includeEmail) => update({ includeEmail })}
              />
              <SwitchRow
                label={t('workspace.aiModels.identityPolicy.includeDisplayName')}
                checked={policy.includeDisplayName}
                onChange={(includeDisplayName) => update({ includeDisplayName })}
              />
              <SwitchRow
                label={t('workspace.aiModels.identityPolicy.pseudonymize')}
                hint={t('workspace.aiModels.identityPolicy.pseudonymizeHint')}
                checked={policy.pseudonymize}
                onChange={(pseudonymize) => update({ pseudonymize })}
              />
              <SwitchRow
                label={t('workspace.aiModels.identityPolicy.groups')}
                hint={t('workspace.aiModels.identityPolicy.groupsHint')}
                checked={policy.groups.enabled}
                onChange={(enabled) => update({ groups: { ...policy.groups, enabled } })}
              />

              {policy.groups.enabled ? (
                <>
                  <Flex direction="column" gap="1">
                    <Text size="2" weight="medium">{t('workspace.aiModels.identityPolicy.allowList')}</Text>
                    <TextArea
                      rows={3}
                      value={allowList}
                      onChange={(event) => setAllowList(event.target.value)}
                    />
                    <Text size="1" style={{ color: 'var(--gray-11)' }}>
                      {t('workspace.aiModels.identityPolicy.allowListHint')}
                    </Text>
                  </Flex>
                  <Flex direction="column" gap="1">
                    <Text size="2" weight="medium">{t('workspace.aiModels.identityPolicy.maxGroups')}</Text>
                    <TextField.Root
                      type="number"
                      min={1}
                      max={MAX_GROUPS}
                      value={String(policy.groups.maxForwarded)}
                      onChange={(event) =>
                        update({ groups: { ...policy.groups, maxForwarded: Number(event.target.value) } })
                      }
                    />
                  </Flex>
                </>
              ) : null}

              <SwitchRow
                label={t('workspace.aiModels.identityPolicy.blockMissing')}
                hint={t('workspace.aiModels.identityPolicy.blockMissingHint')}
                checked={policy.onMissingIdentity === 'block'}
                onChange={(block) => update({ onMissingIdentity: block ? 'block' : 'service' })}
              />
              <SwitchRow
                label={t('workspace.aiModels.identityPolicy.embeddingAsService')}
                hint={t('workspace.aiModels.identityPolicy.embeddingAsServiceHint')}
                checked={policy.queryEmbeddingIdentity === 'service'}
                onChange={(service) => update({ queryEmbeddingIdentity: service ? 'service' : 'user' })}
              />

              <Flex direction="column" gap="1">
                <Text size="2" weight="medium">{t('workspace.aiModels.identityPolicy.servicePrefix')}</Text>
                <TextField.Root
                  value={policy.serviceIdentityPrefix}
                  maxLength={64}
                  onChange={(event) => update({ serviceIdentityPrefix: event.target.value })}
                />
              </Flex>
            </>
          ) : null}

          {damaged ? (
            <Text size="2" style={{ color: 'var(--amber-11)' }}>
              {t('workspace.aiModels.identityPolicy.damaged')}
            </Text>
          ) : null}

          {error ? <Text size="2" style={{ color: 'var(--red-11)' }}>{error}</Text> : null}

          <Flex justify="end" gap="2">
            <Button type="button" variant="soft" color="gray" onClick={onClose}>
              {t('workspace.aiModels.cancel')}
            </Button>
            <Button
              type="button"
              disabled={!policy || saving || !policy.serviceIdentityPrefix.trim()}
              onClick={() => void save()}
            >
              {t('workspace.aiModels.identity.save')}
            </Button>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
