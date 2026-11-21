'use client';

import React, { useCallback, useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useReactFlow } from '@xyflow/react';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ModelAccountsApi } from '@/app/(main)/workspace/ai-accounts/api';
import { engineName } from '@/app/(main)/workspace/ai-accounts/access-copy';
import { CodingEngineOptions } from '@/app/(main)/workspace/ai-accounts/components/coding-engine-select';
import type { CodingEngineOption, EngineProfileNarrowing } from '@/app/(main)/workspace/ai-accounts/types';
import { engineChoice, isEngineProfile, withEngine } from '../agent-account';
import { AgentAccountChoice } from './agent-account-choice';
import { Section } from './core-node-section';

const K = 'aiAccounts.codingEngine';

type EnginesLoad = { status: 'loading' } | { status: 'ready'; engines: CodingEngineOption[] } | { status: 'unavailable' };

const selectStyle: React.CSSProperties = {
  backgroundColor: 'var(--gray-2)',
  color: 'var(--gray-12)',
  border: '1px solid var(--gray-5)',
  borderRadius: 'var(--radius-2)',
  padding: '0 4px',
  height: 24,
  fontSize: 12,
  maxWidth: 170,
  outline: 'none',
  appearance: 'auto',
};

/**
 * "Coding engine" on the agent (core) card (ENGPICK): none (the built-in loop)
 * or an engine the org allows. Picking one sets the agent's engine narrowing on
 * the node, saved with the agent; the server checks it again on save.
 */
export function AgentCodingEngine({
  nodeId,
  stored,
  readOnly = false,
}: {
  nodeId: string;
  stored: unknown;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const { setNodes } = useReactFlow();
  const ids = useId();
  const [load, setLoad] = useState<EnginesLoad>({ status: 'loading' });
  const [settingsOpen, setSettingsOpen] = useState(false);
  const engineProfile = isEngineProfile(stored) ? stored : null;
  const value = engineChoice(engineProfile);

  const read = useCallback(async () => {
    setLoad({ status: 'loading' });
    try {
      setLoad({ status: 'ready', engines: (await ModelAccountsApi.codingEngine()).engines });
    } catch {
      setLoad({ status: 'unavailable' });
    }
  }, []);

  useEffect(() => {
    if (!readOnly) void read();
  }, [read, readOnly]);

  const write = useCallback(
    (next: EngineProfileNarrowing | null) => {
      setNodes((nodes) =>
        nodes.map((node) =>
          node.id === nodeId
            ? { ...node, data: { ...node.data, config: { ...((node.data.config as Record<string, unknown>) || {}), engineProfile: next } } }
            : node,
        ),
      );
    },
    [nodeId, setNodes],
  );

  const titleId = `${ids}-title`;
  const helperId = `${ids}-helper`;
  const settingsId = `${ids}-settings`;
  const choiceText = value === 'none' ? t(`${K}.noneLoopShort`) : value === 'any' ? t(`${K}.anyEngine`) : engineName(value);

  let aside: React.ReactNode = null;
  if (readOnly) {
    aside = (
      <Text size="1" style={{ color: 'var(--agent-flow-text)' }}>
        {choiceText}
      </Text>
    );
  } else if (load.status === 'loading') {
    aside = (
      <Text size="1" role="status" style={{ color: 'var(--agent-flow-text-muted)' }}>
        {t('aiAccounts.common.loading')}
      </Text>
    );
  } else if (load.status === 'ready') {
    aside = (
      <select
        className="nodrag nopan"
        aria-labelledby={titleId}
        aria-describedby={helperId}
        value={value}
        onChange={(event) => write(withEngine(engineProfile, event.target.value))}
        style={selectStyle}
      >
        <CodingEngineOptions
          value={value}
          leading={[
            { value: 'none', label: t(`${K}.noneLoop`) },
            ...(value === 'any' ? [{ value: 'any', label: t(`${K}.anyEngine`) }] : []),
          ]}
          engines={load.engines}
        />
      </select>
    );
  }

  return (
    <Section title={t(`${K}.label`)} icon="terminal" titleId={titleId} aside={aside}>
      {load.status === 'unavailable' && !readOnly ? (
        <Flex direction="column" gap="2" align="start" role="alert">
          <Text size="1">{t(`${K}.unavailable`)}</Text>
          <Button className="nodrag nopan" type="button" size="1" variant="soft" onClick={() => void read()}>
            {t('aiAccounts.common.tryAgain')}
          </Button>
        </Flex>
      ) : null}
      <Flex align="end" justify="between" gap="2">
        <Text id={helperId} size="1" style={{ color: 'var(--agent-flow-text-muted)' }}>
          {t(`${K}.agentHelper`)}
        </Text>
        {engineProfile ? (
          <Button
            className="nodrag nopan"
            type="button"
            size="1"
            variant="ghost"
            color="gray"
            aria-expanded={settingsOpen}
            aria-controls={settingsId}
            onClick={() => setSettingsOpen((open) => !open)}
            style={{ flexShrink: 0 }}
          >
            <MaterialIcon name={settingsOpen ? 'expand_more' : 'chevron_right'} size={14} />
            {t(`${K}.engineSettings`)}
          </Button>
        ) : null}
      </Flex>
      {engineProfile && settingsOpen ? (
        <Box id={settingsId} className="nodrag nopan">
          <AgentAccountChoice engineProfile={engineProfile} onChange={write} readOnly={readOnly} />
        </Box>
      ) : null}
    </Section>
  );
}
