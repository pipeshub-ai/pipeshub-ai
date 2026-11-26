'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useToastStore } from '@/lib/store/toast-store';
import { SettingsSection } from '../../components/settings-section';
import { ModelAccountsApi, isAccountsError, toAccountsError } from '../api';
import { engineName } from '../access-copy';
import type { CodingEngineView } from '../types';
import { CodingEngineSelect } from './coding-engine-select';
import { LoadState, type Load } from './load-state';

const K = 'aiAccounts.codingEngine';

/** "Coding engine": which engine the person's Agent-mode chats hand code and file work to (ENGPICK). */
export function MyCodingEngine() {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const [load, setLoad] = useState<Load<CodingEngineView>>({ status: 'loading' });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const read = useCallback(async () => {
    setLoad({ status: 'loading' });
    try {
      setLoad({ status: 'ready', data: await ModelAccountsApi.codingEngine() });
    } catch (caught) {
      setLoad({ status: 'error', error: isAccountsError(caught) ? caught : toAccountsError(caught) });
    }
  }, []);

  useEffect(() => {
    void read();
  }, [read]);

  const choose = async (choice: string) => {
    setSaving(true);
    setError(null);
    try {
      setLoad({ status: 'ready', data: await ModelAccountsApi.putCodingEngine(choice) });
      addToast({ variant: 'success', title: t(`${K}.saved`) });
    } catch (caught) {
      setError(
        isAccountsError(caught) && caught.kind === 'invalid' && caught.message
          ? caught.message
          : t('aiAccounts.common.failed'),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection title={t(`${K}.title`)} description={t(`${K}.myDescription`)}>
      <LoadState load={load} onRetry={() => void read()}>
        {(view) => (
          <CodingEngineSelect
            label={t(`${K}.label`)}
            helper={t(`${K}.myHelper`)}
            value={view.choice}
            leading={[
              {
                value: 'org_default',
                label: t(`${K}.orgDefault`, {
                  name: view.orgDefault === null ? t(`${K}.noneShort`) : engineName(view.orgDefault),
                }),
              },
            ]}
            engines={view.engines}
            trailing={[{ value: 'none', label: t(`${K}.noneSandbox`) }]}
            onChange={(choice) => void choose(choice)}
            disabled={saving}
            error={error}
          />
        )}
      </LoadState>
    </SettingsSection>
  );
}
