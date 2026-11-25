'use client';

import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useReactFlow } from '@xyflow/react';
import { Button, Callout, Flex, IconButton, Select, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { AvailableLlmModel } from '@/chat/types';
import { ModelAccountsApi } from '@/app/(main)/workspace/ai-accounts/api';
import type { ModelAccessView } from '@/app/(main)/workspace/ai-accounts/types';
import type { RoleModelRef, RoleModels } from '../../types';
import {
  DEFAULT_ROLE_KEY,
  MAX_ROLE_MODELS,
  choicesForRole,
  modelChoiceId,
  normalizeRoleKey,
  roleCostCeiling,
  roleModelCatalog,
  roleModelLabel,
  sameModel,
  toRoleModels,
  type RoleModelCatalog,
} from '../role-models';

type CatalogLoad = { status: 'idle' } | { status: 'loading' } | { status: 'ready'; catalog: RoleModelCatalog };

interface SubAgentModelsContextValue {
  load: CatalogLoad;
  configuredModels: AvailableLlmModel[];
  ensureLoaded: () => void;
}

const SubAgentModelsContext = createContext<SubAgentModelsContextValue>({
  load: { status: 'ready', catalog: { choices: [], ceilings: {} } },
  configuredModels: [],
  ensureLoaded: () => {},
});

type ViewLoad = { status: 'idle' } | { status: 'loading' } | { status: 'ready'; view: ModelAccessView | null };

/**
 * What the builder's role selects may offer. The org's model-access view is
 * read once, and only when an agent has (or its author adds) a role model.
 */
export function SubAgentModelsProvider({
  configuredModels,
  children,
}: {
  configuredModels: AvailableLlmModel[];
  children: React.ReactNode;
}) {
  const [view, setView] = useState<ViewLoad>({ status: 'idle' });
  const requested = useRef(false);

  const ensureLoaded = useCallback(() => {
    if (requested.current) return;
    requested.current = true;
    setView({ status: 'loading' });
    ModelAccountsApi.access().then(
      (read) => setView({ status: 'ready', view: read }),
      () => setView({ status: 'ready', view: null }),
    );
  }, []);

  const value = useMemo<SubAgentModelsContextValue>(
    () => ({
      load:
        view.status === 'ready'
          ? { status: 'ready', catalog: roleModelCatalog(view.view, configuredModels) }
          : view,
      configuredModels,
      ensureLoaded,
    }),
    [view, configuredModels, ensureLoaded],
  );

  return <SubAgentModelsContext.Provider value={value}>{children}</SubAgentModelsContext.Provider>;
}

interface RoleRow {
  id: number;
  /** The "All sub-agents" row: its key is fixed, it has no name to type. */
  isDefault: boolean;
  name: string;
  model: RoleModelRef | null;
}

const INHERIT = '__inherit__';

const rowKey = (row: RoleRow): string => (row.isDefault ? DEFAULT_ROLE_KEY : normalizeRoleKey(row.name));

/** The first row of a key owns it: a later row of the same key is not saved. */
function ownedKeys(rows: RoleRow[]): Map<number, string> {
  const owners = new Map<number, string>();
  const taken = new Set<string>();
  for (const row of rows) {
    const key = rowKey(row);
    if (!key || taken.has(key)) continue;
    taken.add(key);
    owners.set(row.id, key);
  }
  return owners;
}

function rowsToRoleModels(rows: RoleRow[]): RoleModels {
  const owners = ownedKeys(rows);
  const roleModels: RoleModels = {};
  for (const row of rows) {
    const key = owners.get(row.id);
    if (key && row.model) roleModels[key] = row.model;
  }
  return roleModels;
}

function rowsFromRoleModels(stored: RoleModels): RoleRow[] {
  const entries = Object.entries(stored);
  // "All sub-agents" reads first whatever order the server kept.
  entries.sort(([a], [b]) => Number(b === DEFAULT_ROLE_KEY) - Number(a === DEFAULT_ROLE_KEY));
  return entries.map(([key, model], index) => ({
    id: index,
    isDefault: key === DEFAULT_ROLE_KEY,
    name: key === DEFAULT_ROLE_KEY ? '' : key,
    model,
  }));
}

function usePriceText(): (usdPerMTok: number) => string {
  const { t, i18n } = useTranslation();
  return useCallback(
    (usdPerMTok: number) =>
      t('agentBuilder.subAgentModels.price', {
        price: new Intl.NumberFormat(i18n.language, {
          style: 'currency',
          currency: 'USD',
          minimumFractionDigits: 2,
          maximumFractionDigits: 4,
        }).format(usdPerMTok),
      }),
    [t, i18n.language],
  );
}

const MUTED: React.CSSProperties = { color: 'var(--agent-flow-text-muted)' };

/**
 * The model each sub-agent role runs on (MA1), edited in the agent node. A
 * role without a model is not saved: that sub-agent uses the model of the
 * agent that starts it. The agent's own model stays the connected LLM nodes.
 */
export function SubAgentModelsSection({
  nodeId,
  stored,
  readOnly = false,
  startsSubAgents = true,
}: {
  nodeId: string;
  stored: unknown;
  readOnly?: boolean;
  /** False: the agent's definition grants no sub-agents (`maxChildren: 0`), so these models are never used. */
  startsSubAgents?: boolean;
}) {
  const { t } = useTranslation();
  const { setNodes } = useReactFlow();
  const { load, configuredModels, ensureLoaded } = useContext(SubAgentModelsContext);
  const priceText = usePriceText();
  const [rows, setRows] = useState<RoleRow[]>(() => rowsFromRoleModels(toRoleModels(stored)));
  const nextId = useRef(rows.length);
  const [focusRow, setFocusRow] = useState<number | null>(null);
  const addRoleButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (rows.length > 0) ensureLoaded();
  }, [rows.length, ensureLoaded]);

  const commit = useCallback(
    (next: RoleRow[]) => {
      setRows(next);
      const roleModels = rowsToRoleModels(next);
      setNodes((nodes) =>
        nodes.map((node) => {
          if (node.id !== nodeId) return node;
          const { roleModels: _previous, ...config } = (node.data.config as Record<string, unknown>) || {};
          return {
            ...node,
            data: {
              ...node.data,
              config: Object.keys(roleModels).length > 0 ? { ...config, roleModels } : config,
            },
          };
        }),
      );
    },
    [nodeId, setNodes],
  );

  const addRow = (isDefault: boolean) => {
    const id = nextId.current++;
    commit(
      isDefault
        ? [{ id, isDefault, name: '', model: null }, ...rows]
        : [...rows, { id, isDefault, name: '', model: null }],
    );
    if (!isDefault) setFocusRow(id);
  };

  const owners = ownedKeys(rows);
  const catalog = load.status === 'ready' ? load.catalog : null;
  const hasDefaultRow = rows.some((row) => row.isDefault);
  const full = rows.length >= MAX_ROLE_MODELS;
  const roleName = (row: RoleRow): string =>
    row.isDefault
      ? t('agentBuilder.subAgentModels.allSubAgents')
      : row.name.trim() || t('agentBuilder.subAgentModels.unnamedRole');

  const defaultCeiling = catalog?.ceilings[DEFAULT_ROLE_KEY] ?? null;
  const tighterRoles = catalog
    ? rows
        .map((row) => ({ row, key: owners.get(row.id), ceiling: roleCostCeiling(catalog.ceilings, rowKey(row)) }))
        .filter(
          (entry): entry is { row: RoleRow; key: string; ceiling: number } =>
            entry.key !== undefined &&
            entry.ceiling !== null &&
            (defaultCeiling === null || entry.ceiling < defaultCeiling),
        )
    : [];

  return (
    <Flex direction="column" gap="2" role="group" aria-label={t('agentBuilder.subAgentModels.title')}>
      <Text size="1" style={MUTED}>
        {t('agentBuilder.subAgentModels.hint')}
      </Text>
      {startsSubAgents ? null : (
        <Callout.Root size="1" color="gray" variant="surface" data-testid="sub-agent-models-none">
          <Callout.Text>{t('agentBuilder.subAgentModels.startsNone')}</Callout.Text>
        </Callout.Root>
      )}

      {rows.map((row) => {
        const key = rowKey(row);
        const duplicate = key !== '' && owners.get(row.id) === undefined;
        const offered = catalog ? choicesForRole(catalog, key) : [];
        const stale = row.model !== null && catalog !== null && !offered.some((choice) => sameModel(choice, row.model!));
        const unlisted = row.model !== null && !offered.some((choice) => sameModel(choice, row.model!));
        const name = roleName(row);
        const storedLabel = row.model ? roleModelLabel(row.model, configuredModels) : '';

        return (
          <Flex key={row.id} direction="column" gap="1" role="group" aria-label={name} data-testid="sub-agent-model-row">
            <Flex align="center" gap="2">
              {row.isDefault || readOnly ? (
                <Text size="1" weight="medium" style={{ flex: 1, minWidth: 0, color: 'var(--agent-flow-text)' }}>
                  {name}
                </Text>
              ) : (
                <TextField.Root
                  className="nodrag nopan"
                  size="1"
                  style={{ flex: 1, minWidth: 0 }}
                  value={row.name}
                  autoFocus={focusRow === row.id}
                  placeholder={t('agentBuilder.subAgentModels.roleNamePlaceholder')}
                  aria-label={t('agentBuilder.subAgentModels.roleNameLabel')}
                  onChange={(event) =>
                    commit(rows.map((r) => (r.id === row.id ? { ...r, name: event.target.value } : r)))
                  }
                />
              )}
              {!readOnly ? (
                <IconButton
                  className="nodrag nopan"
                  type="button"
                  size="1"
                  variant="ghost"
                  color="gray"
                  aria-label={t('agentBuilder.subAgentModels.removeRole', { role: name })}
                  onClick={() => {
                    commit(rows.filter((r) => r.id !== row.id));
                    addRoleButton.current?.focus();
                  }}
                >
                  <MaterialIcon name="close" size={14} color="var(--agent-flow-text-muted)" />
                </IconButton>
              ) : null}
            </Flex>

            {!row.isDefault && key !== '' && key !== row.name ? (
              <Text size="1" style={MUTED}>
                {t('agentBuilder.subAgentModels.savedAs', { key })}
              </Text>
            ) : null}

            {readOnly ? (
              <Text size="1" style={{ color: 'var(--agent-flow-text)' }}>
                {row.model ? storedLabel : t('agentBuilder.subAgentModels.sameAsAgent')}
              </Text>
            ) : (
              <Select.Root
                size="1"
                disabled={catalog === null}
                value={row.model ? modelChoiceId(row.model) : INHERIT}
                onValueChange={(value) => {
                  const chosen = offered.find((choice) => modelChoiceId(choice) === value);
                  if (value !== INHERIT && !chosen) return;
                  const model = chosen
                    ? { modelKey: chosen.modelKey, modelName: chosen.modelName, provider: chosen.provider }
                    : null;
                  commit(rows.map((r) => (r.id === row.id ? { ...r, model } : r)));
                }}
              >
                <Select.Trigger
                  className="nodrag nopan"
                  style={{ width: '100%' }}
                  aria-label={t('agentBuilder.subAgentModels.modelFor', { role: name })}
                  aria-busy={catalog === null}
                />
                <Select.Content>
                  <Select.Item value={INHERIT}>{t('agentBuilder.subAgentModels.sameAsAgent')}</Select.Item>
                  {offered.map((choice) => (
                    <Select.Item key={modelChoiceId(choice)} value={modelChoiceId(choice)}>
                      {choice.label}
                      {choice.usdPerMTok !== null ? (
                        <span style={{ color: 'var(--gray-11)' }}>{` · ${priceText(choice.usdPerMTok)}`}</span>
                      ) : null}
                    </Select.Item>
                  ))}
                  {row.model && unlisted ? (
                    <Select.Item value={modelChoiceId(row.model)} disabled>
                      {stale ? t('agentBuilder.subAgentModels.notAllowedOption', { model: storedLabel }) : storedLabel}
                    </Select.Item>
                  ) : null}
                </Select.Content>
              </Select.Root>
            )}

            {stale ? (
              <Text size="1" color="red" role="alert">
                {t('agentBuilder.subAgentModels.notAllowed')}
              </Text>
            ) : null}
            {duplicate ? (
              <Text size="1" color="amber">
                {t('agentBuilder.subAgentModels.duplicateRole')}
              </Text>
            ) : null}
            {!row.isDefault && key === '' && row.model ? (
              <Text size="1" color="amber">
                {t('agentBuilder.subAgentModels.nameNeeded')}
              </Text>
            ) : null}
          </Flex>
        );
      })}

      {rows.length > 0 && load.status === 'loading' ? (
        <Text size="1" role="status" style={MUTED}>
          {t('agentBuilder.subAgentModels.loading')}
        </Text>
      ) : null}
      {rows.length > 0 && defaultCeiling !== null ? (
        <Text size="1" style={MUTED}>
          {t('agentBuilder.subAgentModels.ceiling', { limit: priceText(defaultCeiling) })}
        </Text>
      ) : null}
      {tighterRoles.map(({ row, ceiling }) => (
        <Text key={row.id} size="1" style={MUTED}>
          {t('agentBuilder.subAgentModels.roleCeiling', { role: roleName(row), limit: priceText(ceiling) })}
        </Text>
      ))}

      {!readOnly ? (
        <Flex gap="2" wrap="wrap">
          {!hasDefaultRow ? (
            <Button
              className="nodrag nopan"
              type="button"
              size="1"
              variant="soft"
              color="gray"
              disabled={full}
              onClick={() => addRow(true)}
            >
              {t('agentBuilder.subAgentModels.addAllSubAgents')}
            </Button>
          ) : null}
          <Button
            ref={addRoleButton}
            className="nodrag nopan"
            type="button"
            size="1"
            variant="soft"
            color="gray"
            disabled={full}
            onClick={() => addRow(false)}
          >
            {t('agentBuilder.subAgentModels.addRole')}
          </Button>
        </Flex>
      ) : null}
      {full ? (
        <Text size="1" style={MUTED}>
          {t('agentBuilder.subAgentModels.limit', { max: MAX_ROLE_MODELS })}
        </Text>
      ) : null}
    </Flex>
  );
}
