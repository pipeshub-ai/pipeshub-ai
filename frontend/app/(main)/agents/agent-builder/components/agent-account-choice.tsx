'use client';

import React, { useCallback, useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, RadioGroup, Text } from '@radix-ui/themes';
import { ModelAccountsApi } from '@/app/(main)/workspace/ai-accounts/api';
import { accountKindKey, kindAvailability, reasonKey } from '@/app/(main)/workspace/ai-accounts/access-copy';
import type { EngineProfileNarrowing, ModelAccessView } from '@/app/(main)/workspace/ai-accounts/types';
import { AGENT_ACCOUNT_KINDS, accountKindChoice, withAccountKinds } from '../agent-account';

export interface AgentAccountChoiceProps {
  engineProfile: EngineProfileNarrowing;
  onChange: (next: EngineProfileNarrowing) => void;
  readOnly?: boolean;
}

type AccessLoad = { status: 'loading' } | { status: 'ready'; view: ModelAccessView } | { status: 'unavailable' };

/**
 * Which kind of account this agent's engine tasks use for the model, within
 * what the release rules and the org allow. Admission checks it again
 * against the org ceiling (E4a) and the vendor rules (A1).
 */
export function AgentAccountChoice({ engineProfile, onChange, readOnly = false }: AgentAccountChoiceProps) {
  const { t } = useTranslation();
  const headingId = useId();
  const [access, setAccess] = useState<AccessLoad>({ status: 'loading' });

  const load = useCallback(async () => {
    setAccess({ status: 'loading' });
    try {
      setAccess({ status: 'ready', view: await ModelAccountsApi.access() });
    } catch {
      setAccess({ status: 'unavailable' });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const choice = accountKindChoice(engineProfile);

  return (
    <Box>
      <Text as="p" size="2" weight="bold" id={headingId}>
        {t('aiAccounts.agent.title')}
      </Text>
      <Text as="p" size="1" color="gray" mt="1">
        {t('aiAccounts.agent.description')}
      </Text>
      {choice === 'custom' && (
        <Text as="p" size="1" mt="2">
          {t('aiAccounts.agent.custom', {
            kinds: (engineProfile.accountKinds ?? []).map((kind) => t(accountKindKey(kind))).join(', '),
          })}
        </Text>
      )}
      {access.status === 'loading' && (
        <Text as="p" size="1" role="status" mt="2">
          {t('aiAccounts.common.loading')}
        </Text>
      )}
      {access.status === 'unavailable' && (
        <Flex direction="column" gap="2" align="start" mt="2" role="alert">
          <Text size="1">{t('aiAccounts.agent.unavailable')}</Text>
          <Button type="button" size="1" variant="soft" onClick={() => void load()}>
            {t('aiAccounts.common.tryAgain')}
          </Button>
        </Flex>
      )}
      {access.status === 'ready' && (
        <RadioGroup.Root
          mt="2"
          value={choice === 'custom' ? '' : choice}
          onValueChange={(value) => {
            if (value === 'any' || (AGENT_ACCOUNT_KINDS as readonly string[]).includes(value)) {
              onChange(withAccountKinds(engineProfile, value as Parameters<typeof withAccountKinds>[1]));
            }
          }}
          disabled={readOnly}
          aria-labelledby={headingId}
        >
          <RadioGroup.Item value="any">{t('aiAccounts.agent.any')}</RadioGroup.Item>
          {AGENT_ACCOUNT_KINDS.map((kind) => {
            const reason = kindAvailability(kind, access.view);
            return (
              <Flex key={kind} direction="column">
                <RadioGroup.Item value={kind} disabled={reason !== 'available'}>
                  {t(accountKindKey(kind))}
                </RadioGroup.Item>
                {reason !== 'available' && (
                  <Text size="1" color="gray" style={{ paddingLeft: 24 }}>
                    {t(reasonKey(reason))}
                  </Text>
                )}
              </Flex>
            );
          })}
        </RadioGroup.Root>
      )}
    </Box>
  );
}
