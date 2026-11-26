'use client';

import React, { useCallback, useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Switch, Text } from '@radix-ui/themes';
import { useToastStore } from '@/lib/store/toast-store';
import { ModelAccountsApi, isAccountsError, toAccountsError } from '../../ai-accounts/api';
import { CodingEngineSelect } from '../../ai-accounts/components/coding-engine-select';
import { EngineIsolationNotice } from '../../ai-accounts/components/engine-isolation-notice';
import { LoadState, type Load } from '../../ai-accounts/components/load-state';
import type { CodingEngineView } from '../../ai-accounts/types';

const K = 'aiAccounts.codingEngine';
const NONE = 'none';

/** Dispatched on `window` by a setting that changes which engines the org allows: the card reads them again. */
export const CODING_ENGINES_CHANGED = 'pipeshub:coding-engines-changed';

/**
 * "Default coding engine for the org" (ENGPICK): the engine default-chat
 * Agent-mode turns hand code and file work to unless a person chose another.
 * Saved on change; the server refuses an engine the org cannot use now.
 * Above it, what the admin must know about the engines' sandbox (gVisor).
 * Below it, whether turns hand long work to the engine on their own (DEEPROUTE).
 */
export function OrgCodingEngine() {
  const { t } = useTranslation();
  const addToast = useToastStore((s) => s.addToast);
  const [load, setLoad] = useState<Load<CodingEngineView>>({ status: 'loading' });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [routeError, setRouteError] = useState<string | null>(null);

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
    const reread = () => void read();
    window.addEventListener(CODING_ENGINES_CHANGED, reread);
    return () => window.removeEventListener(CODING_ENGINES_CHANGED, reread);
  }, [read]);

  const choose = async (value: string) => {
    setSaving(true);
    setError(null);
    try {
      setLoad({ status: 'ready', data: await ModelAccountsApi.putOrgCodingEngine(value === NONE ? null : value) });
      addToast({ variant: 'success', title: t(`${K}.orgSaved`) });
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

  const route = async (on: boolean) => {
    setSaving(true);
    setRouteError(null);
    try {
      setLoad({ status: 'ready', data: await ModelAccountsApi.putOrgAutoRoute(on) });
      addToast({ variant: 'success', title: t(`${K}.autoRoute.saved`) });
    } catch {
      setRouteError(t('aiAccounts.common.failed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <LoadState load={load} onRetry={() => void read()}>
      {(view) => (
        <Flex direction="column" gap="3">
          <EngineIsolationNotice view={view} />
          <CodingEngineSelect
            label={t(`${K}.orgLabel`)}
            helper={t(`${K}.orgHelper`)}
            value={view.orgDefault ?? NONE}
            leading={[{ value: NONE, label: t(`${K}.none`) }]}
            engines={view.engines}
            onChange={(value) => void choose(value)}
            disabled={saving}
            error={error}
          />
          <AutoRouteSwitch checked={view.autoRoute} disabled={saving} error={routeError} onChange={(on) => void route(on)} />
        </Flex>
      )}
    </LoadState>
  );
}

/** DEEPROUTE: "Hand long work to the coding engine", the org's switch (on by default). */
export function AutoRouteSwitch({
  checked,
  disabled,
  error,
  onChange,
}: {
  checked: boolean;
  disabled: boolean;
  error: string | null;
  onChange: (on: boolean) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <Flex align="center" justify="between" gap="3" style={{ minHeight: 44, maxWidth: 560 }}>
      <Box style={{ flex: 1, minWidth: 0 }}>
        <Text as="label" htmlFor={id} size="2" weight="medium">
          {t(`${K}.autoRoute.label`)}
        </Text>
        <Text id={`${id}-help`} as="p" size="1" style={{ color: error ? 'var(--red-11)' : 'var(--slate-11)' }}>
          {error ?? t(`${K}.autoRoute.helper`)}
        </Text>
      </Box>
      <Switch
        id={id}
        aria-describedby={`${id}-help`}
        checked={checked}
        disabled={disabled}
        onCheckedChange={onChange}
        data-testid="org-auto-route"
      />
    </Flex>
  );
}
