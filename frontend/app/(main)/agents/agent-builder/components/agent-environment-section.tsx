'use client';

import React, { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useReactFlow } from '@xyflow/react';
import { Flex, RadioCards, Text, TextArea } from '@radix-ui/themes';
import { AgentEnvironmentExtras } from '@/config';
import {
  agentEnvironmentOf,
  draftOf,
  environmentOf,
  type EnvironmentDraft,
} from '../../agent-environment';

const IGNORED_KEYS = ['image', 'build', 'features', 'mounts', 'runArgs', 'forwardPorts', 'customizations'];

/** The devcontainer.json keys the worker does not apply, so the author is told before saving. */
export function ignoredKeys(document: string): string[] {
  try {
    const parsed: unknown = JSON.parse(document);
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return [];
    return Object.keys(parsed).filter((key) => IGNORED_KEYS.includes(key));
  } catch {
    return [];
  }
}

export function EnvironmentField({ id, label, hint, problem, children }: {
  id: string;
  label: string;
  hint?: string;
  problem?: string | null;
  children: React.ReactNode;
}) {
  return (
    <Flex direction="column" gap="1">
      <Text as="label" htmlFor={id} size="2" weight="medium">
        {label}
      </Text>
      {children}
      {problem ? (
        <Text size="1" color="red" role="alert">
          {problem}
        </Text>
      ) : hint ? (
        <Text size="1" color="gray">
          {hint}
        </Text>
      ) : null}
    </Flex>
  );
}

/**
 * "Environment" (C20.18, WP-100) on the agent card: what the agent's coding-engine workspace needs before it starts,
 * set up in each fresh workspace. Saved with the agent only when it reads as one the server accepts; a line that does
 * not is named and nothing is saved until it is fixed. `AgentEnvironmentExtras` (config.ts) may add to it.
 */
export function AgentEnvironmentSection({ nodeId, stored, readOnly }: { nodeId: string; stored: unknown; readOnly?: boolean }) {
  const { t } = useTranslation();
  const { setNodes } = useReactFlow();
  const [draft, setDraft] = useState<EnvironmentDraft>(() => draftOf(agentEnvironmentOf(stored)));
  const read = environmentOf(draft);
  const problem = 'problem' in read ? t(read.problem.key, { name: read.problem.name ?? '' }) : null;

  const commit = useCallback(
    (change: Partial<EnvironmentDraft>) => {
      const next = { ...draft, ...change };
      setDraft(next);
      const saved = environmentOf(next);
      if ('problem' in saved) return;
      setNodes((nodes) =>
        nodes.map((node) => {
          if (node.id !== nodeId) return node;
          const { environment: _previous, ...config } = (node.data.config as Record<string, unknown>) || {};
          return {
            ...node,
            data: { ...node.data, config: saved.environment ? { ...config, environment: saved.environment } : config },
          };
        }),
      );
    },
    [draft, nodeId, setNodes],
  );

  const ignored = draft.mode === 'devcontainer' ? ignoredKeys(draft.devcontainer) : [];
  const settingsProblem = problem !== null && 'problem' in read && read.problem.key !== 'agentBuilder.environment.problem.folder'
    && read.problem.key !== 'agentBuilder.environment.problem.devcontainer' ? problem : null;

  return (
    <Flex direction="column" gap="3">
      <Text size="2" color="gray">
        {t('agentBuilder.environment.intro')}
      </Text>
      {/* Cards, not a segmented control: a segmented control never wraps, and "From devcontainer.json" beside "Setup
          steps" is wider than the agent card. */}
      <RadioCards.Root
        size="1"
        gap="2"
        columns="repeat(auto-fit, minmax(8rem, 1fr))"
        value={draft.mode}
        onValueChange={(mode) => commit({ mode: mode as EnvironmentDraft['mode'] })}
        disabled={readOnly}
        aria-label={t('agentBuilder.environment.howLabel')}
      >
        <RadioCards.Item value="steps" style={{ minHeight: 32 }}>
          {t('agentBuilder.environment.steps')}
        </RadioCards.Item>
        <RadioCards.Item value="devcontainer" style={{ minHeight: 32, overflowWrap: 'anywhere' }}>
          {t('agentBuilder.environment.devcontainer')}
        </RadioCards.Item>
      </RadioCards.Root>

      {draft.mode === 'steps' ? (
        <>
          <EnvironmentField id="env-setup" label={t('agentBuilder.environment.setupLabel')} hint={t('agentBuilder.environment.setupHint')}>
            <TextArea
              id="env-setup"
              rows={4}
              value={draft.setup}
              readOnly={readOnly}
              onChange={(event) => commit({ setup: event.target.value })}
              placeholder="pip install -r requirements.txt"
            />
          </EnvironmentField>
          <EnvironmentField
            id="env-settings"
            label={t('agentBuilder.environment.settingsLabel')}
            hint={t('agentBuilder.environment.settingsHint')}
            problem={settingsProblem}
          >
            <TextArea
              id="env-settings"
              rows={2}
              value={draft.settings}
              readOnly={readOnly}
              onChange={(event) => commit({ settings: event.target.value })}
              placeholder="LANG=C.UTF-8"
            />
          </EnvironmentField>
        </>
      ) : (
        <EnvironmentField
          id="env-devcontainer"
          label={t('agentBuilder.environment.devcontainerLabel')}
          hint={t('agentBuilder.environment.devcontainerHint')}
          problem={'problem' in read && read.problem.key === 'agentBuilder.environment.problem.devcontainer' ? problem : null}
        >
          <TextArea
            id="env-devcontainer"
            rows={6}
            value={draft.devcontainer}
            readOnly={readOnly}
            onChange={(event) => commit({ devcontainer: event.target.value })}
          />
          {ignored.length > 0 ? (
            <Text size="1" color="amber">
              {t('agentBuilder.environment.ignored', { keys: ignored.join(', ') })}
            </Text>
          ) : null}
        </EnvironmentField>
      )}

      <AgentEnvironmentExtras
        draft={draft}
        commit={commit}
        folderProblem={'problem' in read && read.problem.key === 'agentBuilder.environment.problem.folder' ? problem : null}
        readOnly={readOnly}
      />
    </Flex>
  );
}
