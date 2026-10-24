'use client';

import type { TFunction } from 'i18next';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Box, Button, Callout, Flex, Switch, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ShowStoredValuesButton } from '@/app/components/ui/show-stored-values-button';
import { ThemeableAssetIcon } from '@/app/components/ui/themeable-asset-icon';
import { WorkspaceRightPanel } from '@/app/(main)/workspace/components/workspace-right-panel';
import { SchemaFormField } from '@/app/(main)/workspace/connectors/components/schema-form-field';
import type { SchemaField } from '@/app/(main)/workspace/connectors/types';
import { EXTERNAL_LINKS } from '@/lib/constants/external-links';
import { toast } from '@/lib/store/toast-store';
import { Spinner } from '@/app/components/ui/spinner';
import { useRevealScope, useSecretRevealAvailable } from '@/lib/hooks/use-secret-reveal-available';
import { useBrand } from '@/lib/branding';
import { aiModelsCapabilityLabel } from '../capability-i18n';
import { resolveModelConfigSaveError } from '../resolve-model-config-save-error';
import type { AIModelProvider, AIModelProviderField, ConfiguredModel, PickedModel } from '../types';
import { CAPABILITY_TO_MODEL_TYPE, IDENTITY_FORWARDING_FIELD_PREFIX } from '../types';
import { AIModelsApi } from '../api';
import { canFetchModels, useModelDiscovery } from '../hooks/use-model-discovery';
import { useBatchAddModels } from '../hooks/use-batch-add-models';
import { EmbeddingDownloadProgress } from './embedding-download-progress';
import {
  PickedModelsList,
  customPicked,
  pickedFromDiscovered,
  type PerModelFlag,
  type PerModelFlagField,
  type PickedDefaults,
} from './model-picker';
import { ModelCombobox } from './model-combobox';
import { BatchProgressList } from './batch-progress-list';

const COMPAT_FIELD_NAMES = ['isReasoning', 'isMultimodal', 'trustRemoteCode'] as const;

const PER_MODEL_FLAGS: readonly PerModelFlag[] = ['isReasoning', 'isMultimodal'];

/** Set on each picked row instead of once for the whole form. */
const PER_MODEL_FIELD_NAMES = new Set([
  'model',
  'modelFriendlyName',
  'contextLength',
  ...PER_MODEL_FLAGS,
]);

/** Hidden from the shared form once several models are picked; each row sets its own. */
const PER_MODEL_FORM_FIELDS: ReadonlySet<string> = new Set(
  [...PER_MODEL_FIELD_NAMES].filter((name) => name !== 'model'),
);

const PER_MODEL_FORM_FIELDS_LIST = [...PER_MODEL_FORM_FIELDS];

const NO_HIDDEN_FIELDS: ReadonlySet<string> = new Set();

/** Besides required fields, these change which models the provider lists. */
const DISCOVERY_SCOPE_FIELDS = ['endpoint', 'region', 'awsAccessKeyId'];

const DISCOVERY_DEBOUNCE_MS = 500;

function boolValue(v: unknown): boolean {
  return typeof v === 'boolean' ? v : v === 'true' ? true : v === 'false' ? false : Boolean(v);
}

function positiveIntOrNull(v: unknown): number | null {
  if (v === '' || v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : null;
}

// Providers whose model runs in-process on the embedding server (loaded via
// SentenceTransformer) rather than calling a remote API — these are the only
// ones that can trigger a slow, untracked Hub download on first use.
const LOCAL_EMBEDDING_PROVIDERS = new Set(['default', 'sentenceTransformers', 'huggingFace']);

function resolveLocalEmbeddingModelName(
  provider: AIModelProvider,
  values: Record<string, unknown>
): string | null {
  if (provider.providerId === 'default') {
    return provider.modelName?.trim() || null;
  }
  const model = String(values.model ?? '').trim();
  return model || null;
}

const READONLY_ROW_STYLE: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  minHeight: 32,
  paddingLeft: 10,
  paddingRight: 10,
  backgroundColor: 'var(--color-surface)',
  border: '1px solid var(--gray-a5)',
  borderRadius: 'var(--radius-2)',
  fontSize: 14,
  color: 'var(--gray-12)',
};

const CARD_STYLE: React.CSSProperties = {
  backgroundColor: 'var(--gray-2)',
  border: '1px solid var(--gray-a5)',
  borderRadius: 'var(--radius-3)',
  padding: 16,
};

function parseNoticeBullets(notice: string): { isBulletList: boolean; items: string[] } {
  const lines = notice.split('\n').map((line) => line.trim()).filter(Boolean);
  const bulletItems = lines
    .filter((line) => line.startsWith('- '))
    .map((line) => line.slice(2).trim());
  if (bulletItems.length > 0 && bulletItems.length === lines.length) {
    return { isBulletList: true, items: bulletItems };
  }
  return { isBulletList: false, items: [notice] };
}

function ProviderNoticeBody({ notice }: { notice: string }) {
  const { isBulletList, items } = parseNoticeBullets(notice);
  if (isBulletList) {
    return (
      <ul style={{ margin: 0, paddingLeft: 20, color: 'var(--gray-12)' }}>
        {items.map((item, index) => (
          <li key={index}>
            <Text as="span" size="2" style={{ color: 'var(--gray-12)' }}>
              {item}
            </Text>
          </li>
        ))}
      </ul>
    );
  }
  return (
    <Text size="2" style={{ color: 'var(--gray-12)', whiteSpace: 'pre-line' }}>
      {notice}
    </Text>
  );
}

/** Azure OpenAI: single model id / deployment name — no comma-separated list. */
function sanitizeAzureOpenAiCommaFreeValue(value: unknown): unknown {
  if (typeof value !== 'string') return value;
  return value.split(',')[0].trim();
}

function normalizePickedIds(providerId: string | undefined, items: PickedModel[]): PickedModel[] {
  if (providerId !== 'azureOpenAI') return items;
  const seen = new Set<string>();
  const next: PickedModel[] = [];
  for (const item of items) {
    const id = String(sanitizeAzureOpenAiCommaFreeValue(item.id) ?? '').trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    next.push(id === item.id ? item : { ...item, id });
  }
  return next;
}

/** Top-level entry fields, not part of GET `configuration`. */
const TOP_LEVEL_FIELD_NAMES = new Set([
  'isMultimodal',
  'isReasoning',
  'contextLength',
  'modelFriendlyName',
]);

const PUBLIC_CONFIG_KEYS = new Set([
  'model',
  'modelFriendlyName',
  'dimensions',
  'defaultReasoningEffort',
]);

/**
 * GET `configuration` is only `model`, `modelFriendlyName`, and `dimensions`.
 * Every other registry field is absent, so it renders blank, waives `required`,
 * and is omitted from the update unless the user types in it.
 */
function findHiddenCredentialFields(
  mode: 'add' | 'edit',
  editModel: ConfiguredModel | null,
  fields: AIModelProviderField[]
): Set<string> {
  if (mode !== 'edit' || !editModel) return new Set();
  if (editModel.configuredFields) {
    return new Set(
      editModel.configuredFields.filter(
        (name) =>
          !TOP_LEVEL_FIELD_NAMES.has(name) &&
          !PUBLIC_CONFIG_KEYS.has(name) &&
          !name.startsWith(IDENTITY_FORWARDING_FIELD_PREFIX),
      ),
    );
  }
  const configured = (editModel.configuration ?? {}) as Record<string, unknown>;
  return new Set(
    fields
      .map((f) => f.name)
      .filter(
        (name) =>
          !TOP_LEVEL_FIELD_NAMES.has(name) &&
          !PUBLIC_CONFIG_KEYS.has(name) &&
          !name.startsWith(IDENTITY_FORWARDING_FIELD_PREFIX) &&
          !(name in configured),
      )
  );
}

function isFieldValueSatisfied(field: AIModelProviderField, value: unknown): boolean {
  if (!field.required) return true;
  const t = field.fieldType;
  if (t === 'BOOLEAN' || t === 'CHECKBOX') return true;
  if (t === 'NUMBER') {
    if (value === '' || value === undefined || value === null) return false;
    const n = Number(value);
    return !Number.isNaN(n);
  }
  if (t === 'SELECT') {
    return String(value ?? '').trim() !== '';
  }
  return String(value ?? '').trim() !== '';
}

function allRequiredFieldsValid(
  fields: AIModelProviderField[],
  values: Record<string, unknown>,
  hiddenCredentials: Set<string>
): boolean {
  return fields.every(
    (f) => hiddenCredentials.has(f.name) || isFieldValueSatisfied(f, values[f.name])
  );
}

function compatIcon(fieldName: string): string {
  if (fieldName === 'isReasoning') return 'lightbulb';
  if (fieldName === 'isMultimodal') return 'image';
  if (fieldName === 'trustRemoteCode') return 'verified_user';
  return 'tune';
}

function fieldStartAdornment(fieldName: string): React.ReactNode | undefined {
  if (fieldName === 'apiKey') {
    return <MaterialIcon name="key" size={16} color="var(--gray-10)" />;
  }
  if (fieldName === 'model' || fieldName === 'contextLength') {
    return <MaterialIcon name="widgets" size={16} color="var(--gray-10)" />;
  }
  return undefined;
}

export interface ModelConfigSaveResult {
  mode: 'add' | 'edit';
  modelName: string;
  modelCategory: 'ai' | 'embedding';
}

function resolveModelDisplayName(
  values: Record<string, unknown>,
  provider: AIModelProvider | null
): string {
  const friendly = String(values.modelFriendlyName ?? '').trim();
  if (friendly) return friendly;
  const model = String(values.model ?? values.deploymentName ?? '').trim();
  if (model) return model;
  if (provider?.modelName?.trim()) return provider.modelName.trim();
  return provider?.name?.trim() || 'Model';
}

function modelCategoryFromCapability(capability: string): 'ai' | 'embedding' {
  return capability === 'embedding' ? 'embedding' : 'ai';
}

interface ModelConfigDialogProps {
  open: boolean;
  mode: 'add' | 'edit';
  provider: AIModelProvider | null;
  capability: string | null;
  editModel: ConfiguredModel | null;
  /**
   * Number of models already configured for the target model type. Used in
   * `add` mode to decide whether the newly-added model should be auto-promoted
   * to default. Only the very first model of a type is auto-defaulted; for
   * every subsequent add the user must explicitly click "Set as default".
   * Defaults to `0` so callers that omit it (e.g. onboarding flows, which are
   * always first-time setup) preserve the first-model-auto-defaults behavior.
   */
  existingModelsCount?: number;
  onClose: () => void;
  onSaved: (result: ModelConfigSaveResult) => void;
}

export function ModelConfigDialog({
  open,
  mode,
  provider,
  capability,
  editModel,
  existingModelsCount = 0,
  onClose,
  onSaved,
}: ModelConfigDialogProps) {
  const { t } = useTranslation();
  const brand = useBrand();
  const docsOverviewUrl =
    brand.docsHref('ai-models/overview') ?? `${EXTERNAL_LINKS.documentation}ai-models/overview`;
  const [fields, setFields] = useState<AIModelProviderField[]>([]);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<PickedModel[]>([]);
  const [defaultModelId, setDefaultModelId] = useState<string | null>(null);
  const discovery = useModelDiscovery();
  const batch = useBatchAddModels();
  const progressToastRef = useRef<string | null>(null);
  const checkStatusRef = useRef<HTMLDivElement>(null);
  const resetBatch = batch.reset;
  const resetDiscoveryState = discovery.reset;
  const [downloadTarget, setDownloadTarget] = useState<{
    modelName: string;
    trustRemoteCode: boolean;
  } | null>(null);
  const downloadResolverRef = useRef<((ready: boolean) => void) | null>(null);
  const touchedFieldsRef = useRef<Set<string>>(new Set());
  /** Ids typed before the provider described them; they take its metadata once it does. */
  const provisionalIdsRef = useRef<Set<string>>(new Set());
  const [revealState, setRevealState] = useState<'hidden' | 'loading' | 'shown'>('hidden');
  const revealAvailable = useSecretRevealAvailable(open && mode === 'edit');

  useEffect(() => {
    setRevealState('hidden');
    if (!open || !provider || !capability) {
      setFields([]);
      setValues({});
      setError(null);
      setPicked([]);
      setDefaultModelId(null);
      resetDiscoveryState();
      resetBatch();
      touchedFieldsRef.current = new Set();
      provisionalIdsRef.current = new Set();
      return;
    }

    // Identity forwarding is set per connection in its own dialog. Editing one model here
    // would let a connection's models disagree.
    const capFields = (provider.fields[capability] ?? []).filter(
      (f) => !f.name.startsWith(IDENTITY_FORWARDING_FIELD_PREFIX),
    );
    setFields(capFields as AIModelProviderField[]);
    setPicked([]);
    setDefaultModelId(null);
    resetDiscoveryState();
    resetBatch();
    touchedFieldsRef.current = new Set();
    provisionalIdsRef.current = new Set();

    if (mode === 'edit' && editModel) {
      const initial: Record<string, unknown> = {};
      for (const f of capFields) {
        const field = f as AIModelProviderField;
        if (field.name in (editModel.configuration ?? {})) {
          let cfgVal = (editModel.configuration as Record<string, unknown>)[field.name];
          if (
            provider.providerId === 'azureOpenAI' &&
            (field.name === 'model' || field.name === 'deploymentName')
          ) {
            cfgVal = sanitizeAzureOpenAiCommaFreeValue(cfgVal);
          }
          initial[field.name] = cfgVal;
        } else if (field.name === 'isMultimodal') {
          initial[field.name] = editModel.isMultimodal ?? field.defaultValue;
        } else if (field.name === 'isReasoning') {
          initial[field.name] = editModel.isReasoning ?? field.defaultValue;
        } else if (field.name === 'contextLength') {
          initial[field.name] = editModel.contextLength ?? field.defaultValue;
        } else if (field.name === 'modelFriendlyName') {
          initial[field.name] = editModel.modelFriendlyName ?? '';
        } else {
          initial[field.name] = field.defaultValue ?? '';
        }
      }
      setValues(initial);
      const existingModel = String(initial.model ?? '').trim();
      if (existingModel) {
        setPicked([
          {
            id: existingModel,
            modelFriendlyName: String(initial.modelFriendlyName ?? '').trim() || undefined,
            isReasoning: boolValue(initial.isReasoning),
            isMultimodal: boolValue(initial.isMultimodal),
            contextLength: positiveIntOrNull(initial.contextLength),
          },
        ]);
      }
    } else {
      const defaults: Record<string, unknown> = {};
      for (const f of capFields) {
        const field = f as AIModelProviderField;
        defaults[field.name] = field.defaultValue ?? '';
      }
      setValues(defaults);
    }
  }, [open, provider, capability, mode, editModel, resetDiscoveryState, resetBatch]);

  const hiddenCredentials = useMemo(
    () => findHiddenCredentialFields(mode, editModel, fields),
    [mode, editModel, fields]
  );

  const handleFieldChange = useCallback(
    (name: string, value: unknown) => {
      const next =
        provider?.providerId === 'azureOpenAI' &&
        (name === 'model' || name === 'deploymentName')
          ? sanitizeAzureOpenAiCommaFreeValue(value)
          : value;
      touchedFieldsRef.current.add(name);
      // Editing the single model's own settings makes them the user's, not the provider's.
      if (PER_MODEL_FIELD_NAMES.has(name) && name !== 'model') provisionalIdsRef.current.clear();
      setValues((prev) => ({ ...prev, [name]: next }));
    },
    [provider?.providerId]
  );

  const canReveal =
    revealAvailable &&
    revealState !== 'shown' &&
    hiddenCredentials.size > 0 &&
    (editModel as { _source?: string } | null)?._source !== 'parent';
  const beginReveal = useRevealScope(
    `${open}:${mode}:${editModel?.modelType ?? ''}:${editModel?.modelKey ?? ''}`
  );

  // Display only: a revealed field the user leaves alone is still omitted on
  // save, so the stored value is kept exactly as it is without a reveal.
  const handleReveal = useCallback(async () => {
    if (!editModel) return;
    const stillCurrent = beginReveal();
    setRevealState('loading');
    try {
      const stored = await AIModelsApi.revealModelConfiguration(
        editModel.modelType,
        editModel.modelKey
      );
      if (!stillCurrent()) return;
      setValues((prev) => {
        const next = { ...prev };
        for (const name of hiddenCredentials) {
          if (name in stored && !touchedFieldsRef.current.has(name)) next[name] = stored[name];
        }
        return next;
      });
      setRevealState('shown');
    } catch {
      if (stillCurrent()) setRevealState('hidden');
    }
  }, [editModel, hiddenCredentials, beginReveal]);

  const waitForModelDownload = useCallback(
    (modelName: string, trustRemoteCode: boolean) =>
      new Promise<boolean>((resolve) => {
        downloadResolverRef.current = resolve;
        setDownloadTarget({ modelName, trustRemoteCode });
      }),
    []
  );

  const handleDownloadReady = useCallback(() => {
    downloadResolverRef.current?.(true);
    downloadResolverRef.current = null;
    setDownloadTarget(null);
  }, []);

  const handleDownloadCancel = useCallback(() => {
    downloadResolverRef.current?.(false);
    downloadResolverRef.current = null;
    setDownloadTarget(null);
  }, []);

  /**
   * For local embedding providers, both add AND edit hit the same backend
   * health-check (POST/PUT .../providers) that loads the model — editing the
   * `model` field to a not-yet-cached repo id is just as likely to trigger an
   * untracked multi-minute download as adding a brand-new provider. Pre-empt
   * it the same way: kick off `prepare-model` and block on the progress
   * dialog until the model is ready, before calling add/updateProvider.
   * Returns `false` if the user cancels the download.
   */
  const ensureLocalEmbeddingModelReady = useCallback(
    async (currentProvider: AIModelProvider, currentValues: Record<string, unknown>) => {
      if (capability !== 'embedding' || !LOCAL_EMBEDDING_PROVIDERS.has(currentProvider.providerId)) {
        return true;
      }
      const modelNameForDownload = resolveLocalEmbeddingModelName(currentProvider, currentValues);
      if (!modelNameForDownload) return true;

      const trustRemoteCode = Boolean(currentValues.trustRemoteCode);
      try {
        const prepareResult = await AIModelsApi.prepareModel(modelNameForDownload, trustRemoteCode);
        if (prepareResult?.status === 'ready') return true;
      } catch {
        // Embedding server may still be starting up (e.g. during onboarding
        // when all services boot simultaneously). Show the download progress
        // dialog which will poll until the server becomes reachable and the
        // model is ready, rather than failing the save outright.
      }

      return waitForModelDownload(modelNameForDownload, trustRemoteCode);
    },
    [capability, waitForModelDownload]
  );

  const handleSave = async () => {
    if (!provider || !capability) return;
    setError(null);
    setSaving(true);

    try {
      const modelTypeFromCapability = CAPABILITY_TO_MODEL_TYPE[capability];
      const modelType =
        mode === 'edit' && editModel
          ? editModel.modelType
          : modelTypeFromCapability;

      if (!modelType) {
        throw new Error(`Unknown capability: ${capability}`);
      }

      const topLevelKeys = ['isMultimodal', 'isReasoning', 'contextLength'];
      const configuration: Record<string, unknown> = {};
      const topLevel: Record<string, unknown> = {};

      // Guard: required FILE fields must have content before we build the payload.
      // JSON.stringify silently drops keys whose value is `undefined`, so an
      // undefined FILE value would disappear from the request body and arrive at
      // the backend as a missing key — producing a cryptic KeyError there.
      // Hidden credentials are exempt unconditionally (touched or not), matching
      // `allRequiredFieldsValid` above: a stripped credential file (e.g. Vertex
      // AI's service account JSON) is legitimately blank on edit because GET never
      // returns it, and if the user touches it and clears it that is an explicit
      // clear which must reach the backend as "" — not get rejected here before the
      // update loop below can send it.
      const missingRequiredFiles = fields.filter((f) => {
        const field = f as AIModelProviderField;
        if (hiddenCredentials.has(field.name)) {
          return false;
        }
        return (
          field.fieldType === 'FILE' &&
          field.required &&
          (values[field.name] === undefined ||
            values[field.name] === null ||
            String(values[field.name]).trim() === '')
        );
      });
      if (missingRequiredFiles.length > 0) {
        const names = missingRequiredFiles
          .map((f) => (f as AIModelProviderField).displayName || f.name)
          .join(', ');
        throw new Error(`Required file upload missing: ${names}. Please upload the file and try again.`);
      }

      for (const [key, val] of Object.entries(values)) {
        // Skip undefined values — JSON.stringify would silently drop them,
        // causing the backend to see a missing key instead of a clear error.
        if (val === undefined) continue;
        // A stripped credential the user never touched is sent as an absent key,
        // which tells the backend to keep the stored value. Touching it and
        // clearing it still sends "", which clears it.
        if (hiddenCredentials.has(key) && !touchedFieldsRef.current.has(key)) continue;
        if (topLevelKeys.includes(key)) {
          topLevel[key] = val;
        } else {
          configuration[key] = val;
        }
      }

      if (provider.providerId === 'azureOpenAI') {
        if ('model' in configuration) {
          configuration.model = sanitizeAzureOpenAiCommaFreeValue(configuration.model);
        }
        if ('deploymentName' in configuration) {
          configuration.deploymentName = sanitizeAzureOpenAiCommaFreeValue(
            configuration.deploymentName
          );
        }
      }

      if (mode === 'add' && picked.length > 1) {
        await runBatch(
          modelType,
          picked,
          existingModelsCount === 0 ? (defaultModelId ?? picked[0]?.id) : undefined,
        );
        return;
      }

      if (mode === 'add') {
        // Auto-default only the very first model of a given type. For every
        // subsequent add, leave `isDefault: false` so the user's current
        // default stays in place until they explicitly click "Set as default".
        // This is especially important for embeddings: silently changing the
        // default to a model with a different dimension / identity would
        // corrupt the existing vector collection. The backend's
        // set-default endpoint runs a health check to prevent that, so the
        // only way to actually switch the default is via the explicit button.
        const shouldAutoDefault = existingModelsCount === 0;

        const ready = await ensureLocalEmbeddingModelReady(provider, values);
        if (!ready) {
          setSaving(false);
          return;
        }

        beginCheck([resolveModelDisplayName(values, provider)]);
        await AIModelsApi.addProvider({
          modelType,
          provider: provider.providerId,
          modelName: provider.providerId === 'default' ? provider.modelName : undefined,
          configuration,
          isMultimodal: (topLevel.isMultimodal as boolean) ?? false,
          isReasoning: (topLevel.isReasoning as boolean) ?? false,
          isDefault: shouldAutoDefault,
          contextLength: topLevel.contextLength ? Number(topLevel.contextLength) : null,
        });
      } else if (editModel) {
        const ready = await ensureLocalEmbeddingModelReady(provider, values);
        if (!ready) {
          setSaving(false);
          return;
        }

        beginCheck([resolveModelDisplayName(values, provider)]);
        // Preserve the existing default flag on edit so that tweaking
        // dimensions / api key / etc. does not silently remove the model
        // from being the default.
        await AIModelsApi.updateProvider(modelType, editModel.modelKey, {
          provider: provider.providerId,
          configuration,
          isMultimodal: (topLevel.isMultimodal as boolean) ?? false,
          isReasoning: (topLevel.isReasoning as boolean) ?? false,
          isDefault: editModel.isDefault ?? false,
          contextLength: topLevel.contextLength ? Number(topLevel.contextLength) : null,
        });
      }

      endCheck([]);
      onSaved({
        mode,
        modelName: resolveModelDisplayName(values, provider),
        modelCategory: modelCategoryFromCapability(capability),
      });
      onClose();
    } catch (err: unknown) {
      endCheck([resolveModelDisplayName(values, provider)]);
      setError(resolveModelConfigSaveError(err, t));
    } finally {
      setSaving(false);
    }
  };

  const capLabel = capability ? aiModelsCapabilityLabel(t, capability) : '';

  // Health checks make real model calls; slow tiers take most of a minute, so say so.
  function beginCheck(models: string[]) {
    if (progressToastRef.current) toast.dismiss(progressToastRef.current);
    const providerName = provider?.name ?? '';
    progressToastRef.current = toast.loading(
      models.length === 1
        ? t('workspace.aiModels.checkingOne', { model: models[0], provider: providerName })
        : t('workspace.aiModels.checkingMany', { total: models.length, provider: providerName }),
      { description: t('workspace.aiModels.checkingHint') },
    );
    window.requestAnimationFrame(() => checkStatusRef.current?.scrollIntoView({ block: 'nearest' }));
  }

  function endCheck(failed: string[], total = 1) {
    const id = progressToastRef.current;
    if (!id) return;
    progressToastRef.current = null;
    if (failed.length === 0) {
      toast.dismiss(id);
      return;
    }
    toast.update(id, {
      variant: 'error',
      title:
        total === 1
          ? t('workspace.aiModels.checkFailedOne', { model: failed[0] })
          : t('workspace.aiModels.checkFailedMany', { failed: failed.length, total }),
      description: t('workspace.aiModels.checkFailedHint'),
      duration: 6000,
    });
  }

  useEffect(() => {
    const id = progressToastRef.current;
    if (!id || !batch.running || batch.rows.length < 2) return;
    const done = batch.rows.filter((row) => row.status === 'healthy' || row.status === 'failed').length;
    if (done === 0) return;
    toast.update(id, {
      title: t('workspace.aiModels.checkingProgress', { done, total: batch.rows.length }),
    });
  }, [batch.rows, batch.running, t]);

  useEffect(
    () => () => {
      if (progressToastRef.current) toast.dismiss(progressToastRef.current);
    },
    [],
  );

  const headerTitle = useMemo(() => {
    const name = provider?.name ?? '';
    if (mode === 'add') {
      return t('workspace.aiModels.configPanelTitleAdd', { provider: name, capability: capLabel }).trim();
    }
    return t('workspace.aiModels.configPanelTitleEdit', { provider: name, capability: capLabel }).trim();
  }, [mode, provider?.name, capLabel, t]);

  const headerIcon = useMemo(() => {
    if (!provider?.iconPath) return undefined;
    return (
      <span style={{ display: 'inline-flex', borderRadius: 4, overflow: 'hidden', lineHeight: 0 }}>
        <ThemeableAssetIcon
          src={provider.iconPath}
          size={22}
          color="var(--gray-12)"
          variant="flat"
        />
      </span>
    );
  }, [provider?.iconPath]);

  const perModelMode = mode === 'add' && picked.length > 1;
  const formValid = useMemo(
    () =>
      fields.length === 0
        ? true
        : allRequiredFieldsValid(
            perModelMode ? fields.filter((field) => !PER_MODEL_FIELD_NAMES.has(field.name)) : fields,
            values,
            hiddenCredentials,
          ),
    [fields, values, hiddenCredentials, perModelMode]
  );

  const flagFields = useMemo<PerModelFlagField[]>(
    () =>
      PER_MODEL_FLAGS.flatMap((name) => {
        const field = fields.find((f) => f.name === name);
        return field ? [{ name, label: field.displayName || name, description: field.description }] : [];
      }),
    [fields]
  );
  const contextLengthLabel = fields.find((f) => f.name === 'contextLength')?.displayName;
  const pickerDefaults = useMemo<PickedDefaults>(
    () => ({
      isReasoning: flagFields.some((f) => f.name === 'isReasoning') && boolValue(values.isReasoning),
      isMultimodal: flagFields.some((f) => f.name === 'isMultimodal') && boolValue(values.isMultimodal),
      contextLength: positiveIntOrNull(values.contextLength),
    }),
    [flagFields, values.isReasoning, values.isMultimodal, values.contextLength]
  );

  /** The single-model form, read back as a picked row. */
  const pickedFromForm = (id: string): PickedModel => ({
    id,
    modelFriendlyName: String(values.modelFriendlyName ?? '').trim() || undefined,
    ...pickerDefaults,
  });

  /**
   * One selected model is edited in the regular form; several get a row each.
   * Moving between the two carries the values across so nothing typed is lost.
   */
  const applyPicked = (changed: PickedModel[]) => {
    const next = normalizePickedIds(provider?.providerId, changed);
    const leavingSingle = picked.length === 1 && next.length > 1;
    const settled = leavingSingle
      ? next.map((item) => (item.id === picked[0].id ? pickedFromForm(item.id) : item))
      : next;
    setPicked(settled);
    if (!defaultModelId || !settled.some((item) => item.id === defaultModelId)) {
      setDefaultModelId(settled[0]?.id ?? null);
    }
    // Deployments are usually named after their model; follow it until the user types their own.
    // Edit already has a stored deployment — do not overwrite it when the model chip changes.
    const deploymentFollowsModel =
      mode === 'add' &&
      fields.some((f) => f.name === 'deploymentName') &&
      (!touchedFieldsRef.current.has('deploymentName') || String(values.deploymentName ?? '').trim() === '');
    if (settled.length === 1 && (picked.length !== 1 || picked[0].id !== settled[0].id)) {
      const only = settled[0];
      const typedName = String(values.modelFriendlyName ?? '').trim();
      if (deploymentFollowsModel) touchedFieldsRef.current.delete('deploymentName');
      setValues((prev) => ({
        ...prev,
        ...singleModelFormValues(only),
        ...(deploymentFollowsModel ? { deploymentName: only.id } : {}),
        modelFriendlyName: picked.length === 0 && typedName ? typedName : (only.modelFriendlyName ?? ''),
      }));
    } else if (settled.length === 0) {
      setValues((prev) => ({ ...prev, model: '', ...(deploymentFollowsModel ? { deploymentName: '' } : {}) }));
    }
  };

  /** The single-model form fields for one picked row. */
  const singleModelFormValues = (item: PickedModel): Record<string, unknown> => ({
    model: item.id,
    modelFriendlyName: item.modelFriendlyName ?? '',
    ...(fields.some((f) => f.name === 'contextLength') ? { contextLength: item.contextLength ?? '' } : {}),
    ...(flagFields.some((f) => f.name === 'isReasoning') ? { isReasoning: item.isReasoning } : {}),
    ...(flagFields.some((f) => f.name === 'isMultimodal') ? { isMultimodal: item.isMultimodal } : {}),
  });

  const handleModelIdsChange = (ids: string[]) => {
    const byId = new Map(discovery.models.map((model) => [model.id, model]));
    applyPicked(
      ids.map((id) => {
        const existing = picked.find((item) => item.id === id);
        const found = byId.get(id);
        if (existing && !(found && provisionalIdsRef.current.has(id))) return existing;
        if (found) {
          provisionalIdsRef.current.delete(id);
          return pickedFromDiscovered(found, pickerDefaults);
        }
        // Settings the user already set in the form are theirs; the list must not overwrite them.
        if (!PER_MODEL_FORM_FIELDS_LIST.some((name) => touchedFieldsRef.current.has(name))) {
          provisionalIdsRef.current.add(id);
        }
        return customPicked(id, pickerDefaults);
      }),
    );
  };

  const handlePickedRowsChange = (next: PickedModel[]) => {
    for (const item of next) {
      if (!picked.includes(item)) provisionalIdsRef.current.delete(item.id);
    }
    applyPicked(next);
  };

  // A list that arrives after an id was typed still describes it: apply what the
  // provider reports to rows nobody has edited, so a text-only model is not
  // vision-probed and the context window is filled in.
  useEffect(() => {
    if (discovery.models.length === 0 || provisionalIdsRef.current.size === 0) return;
    const byId = new Map(discovery.models.map((model) => [model.id, model]));
    let changed = false;
    const next = picked.map((item) => {
      const found = provisionalIdsRef.current.has(item.id) ? byId.get(item.id) : undefined;
      if (!found) return item;
      provisionalIdsRef.current.delete(item.id);
      changed = true;
      return pickedFromDiscovered(found, {
        isReasoning: item.isReasoning,
        isMultimodal: item.isMultimodal,
        contextLength: item.contextLength ?? null,
      });
    });
    if (!changed) return;
    setPicked(next);
    if (next.length === 1) {
      const friendly = String(values.modelFriendlyName ?? '').trim();
      setValues((prev) => ({
        ...prev,
        ...singleModelFormValues(next[0]),
        ...(friendly ? { modelFriendlyName: friendly } : {}),
      }));
    }
    // Runs when a list arrives; picked/values are read at that moment on purpose.
  }, [discovery.models]);

  const primaryBlocked = saving || batch.running || !provider || !capability || !formValid;

  const discoveryMode = provider?.discovery?.mode ?? 'manual';
  const canListModels = discoveryMode !== 'manual';
  const requiredFields = provider?.discovery?.requiredFields ?? [];
  const credentialKey = [...requiredFields, ...DISCOVERY_SCOPE_FIELDS]
    .map((name) => String(values[name] ?? ''))
    .join('\u0000');
  const seenCredential = useRef<string | null>(null);
  useEffect(() => {
    if (seenCredential.current === null) {
      seenCredential.current = credentialKey;
      return;
    }
    if (seenCredential.current === credentialKey) return;
    seenCredential.current = credentialKey;
    resetDiscoveryState();
  }, [credentialKey, resetDiscoveryState]);
  const canDiscover = canListModels && canFetchModels(requiredFields, values, hiddenCredentials);
  const [searchQuery, setSearchQuery] = useState('');

  const fetchModels = discovery.fetchModels;
  const valuesRef = useRef(values);
  valuesRef.current = values;
  // Load the list as soon as the connection fields are filled; no button to press.
  useEffect(() => {
    if (!open || !provider || !canDiscover) return;
    const timer = window.setTimeout(() => {
      const configuration: Record<string, unknown> = {};
      for (const [key, val] of Object.entries(valuesRef.current)) {
        if (PER_MODEL_FIELD_NAMES.has(key) || TOP_LEVEL_FIELD_NAMES.has(key)) continue;
        if (val === undefined || val === '') continue;
        configuration[key] = val;
      }
      void fetchModels({
        provider: provider.providerId,
        capability: capability ?? undefined,
        configuration,
        query: discoveryMode === 'search' ? searchQuery.trim() || undefined : undefined,
        ...(mode === 'edit' && editModel?.modelKey ? { modelKey: editModel.modelKey } : {}),
      });
    }, DISCOVERY_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [open, provider, capability, canDiscover, credentialKey, discoveryMode, searchQuery, fetchModels, mode, editModel?.modelKey]);

  /** Configuration every picked model shares: credentials and endpoint, never per-model fields. */
  const sharedBatchConfiguration = (): Record<string, unknown> => {
    const configuration: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(values)) {
      if (PER_MODEL_FIELD_NAMES.has(key) || TOP_LEVEL_FIELD_NAMES.has(key)) continue;
      if (hiddenCredentials.has(key) && !touchedFieldsRef.current.has(key)) continue;
      if (val === undefined) continue;
      configuration[key] = provider?.providerId === 'azureOpenAI' && key === 'deploymentName'
        ? sanitizeAzureOpenAiCommaFreeValue(val)
        : val;
    }
    return configuration;
  };

  const runBatch = async (modelType: string, models: PickedModel[], defaultModel?: string) => {
    if (!provider || !capability) return;
    const prepared = normalizePickedIds(provider.providerId, models);
    for (const item of prepared) {
      const ready = await ensureLocalEmbeddingModelReady(provider, { ...values, model: item.id });
      if (!ready) return;
    }
    beginCheck(prepared.map((item) => item.id));
    const result = await batch.run(
      {
        modelType,
        provider: provider.providerId,
        configuration: sharedBatchConfiguration(),
        models: prepared,
        defaultModel:
          provider.providerId === 'azureOpenAI' && defaultModel
            ? String(sanitizeAzureOpenAiCommaFreeValue(defaultModel) ?? '').trim() || undefined
            : defaultModel,
      },
      { merge: true },
    );
    const savedIds = new Set(result.rows.filter((row) => row.status === 'healthy').map((row) => row.model));
    const savedNow = prepared.filter((item) => savedIds.has(item.id));
    endCheck(prepared.filter((item) => !savedIds.has(item.id)).map((item) => item.id), prepared.length);
    if (savedNow.length > 0) {
      onSaved({
        mode: 'add',
        modelName: savedNow.map((item) => item.modelFriendlyName || item.id).join(', '),
        modelCategory: modelCategoryFromCapability(capability),
      });
    }
    const failed = result.rows.some((row) => row.status === 'failed');
    if (!failed && savedNow.length > 0 && !result.aborted) {
      onClose();
      return;
    }
    applyPicked(picked.filter((item) => !savedIds.has(item.id)));
  };

  const retryPicked = (modelId: string) => {
    const model = picked.find((item) => item.id === modelId);
    const modelType = capability ? CAPABILITY_TO_MODEL_TYPE[capability] : undefined;
    if (!model || !modelType) return;
    const isChosenDefault = existingModelsCount === 0 && defaultModelId === model.id;
    const current = picked.length === 1 ? pickedFromForm(model.id) : model;
    runBatch(modelType, [current], isChosenDefault ? model.id : undefined).catch((err: unknown) => {
      setError(resolveModelConfigSaveError(err, t));
    });
  };

  const modelField = fields.find((f) => f.name === 'model');
  const multipleModels = mode === 'add' && !fields.some((f) => f.name === 'deploymentName');
  const discoveryHint = ((): { text: string | null; tone: 'gray' | 'red' } => {
    if (!canListModels) return { text: t('workspace.aiModels.modelHintManual'), tone: 'gray' };
    if (!canDiscover) return { text: t('workspace.aiModels.modelHintNeedsFields'), tone: 'gray' };
    if (discovery.loading) return { text: null, tone: 'gray' };
    if (discovery.error) {
      return { text: t('workspace.aiModels.modelHintFailed', { message: discovery.error }), tone: 'red' };
    }
    if (!discovery.fetched) return { text: null, tone: 'gray' };
    if (discovery.models.length === 0) return { text: t('workspace.aiModels.modelHintNoneFound'), tone: 'gray' };
    const loaded = t('workspace.aiModels.modelHintLoaded', { count: discovery.models.length });
    return { text: [loaded, ...discovery.warnings].join(' '), tone: 'gray' };
  })();

  const modelFieldSlot = modelField ? (
    <ModelCombobox
      label={modelField.displayName}
      required={modelField.required}
      placeholder={canListModels ? t('workspace.aiModels.modelComboboxPlaceholder') : modelField.placeholder}
      models={discovery.models}
      value={picked.map((item) => item.id)}
      onChange={handleModelIdsChange}
      multiple={multipleModels}
      loading={discovery.loading}
      hint={discoveryHint.text}
      hintTone={discoveryHint.tone}
      disabled={saving || batch.running}
      onQueryChange={discoveryMode === 'search' ? setSearchQuery : undefined}
      commitTypedOnBlur={
        // A search provider's empty result only answers the previous query, so there
        // typed text stays a search; Enter or "Use …" still adds it.
        !canListModels ||
        (discoveryMode !== 'search' &&
          discovery.fetched &&
          !discovery.loading &&
          discovery.models.length === 0)
      }
    />
  ) : null;

  const discoverySlot = (
    <Flex direction="column" gap="3">
      <div ref={checkStatusRef}>
        {saving && !batch.running && batch.rows.length === 0 ? (
          <Flex align="center" gap="2" data-testid="ai-model-checking">
            <Spinner size={14} />
            <Text size="2" style={{ color: 'var(--gray-11)' }}>
              {t('workspace.aiModels.checkingOne', {
                model: resolveModelDisplayName(values, provider),
                provider: provider?.name ?? '',
              })}{' '}
              {t('workspace.aiModels.checkingHint')}
            </Text>
          </Flex>
        ) : null}
        <BatchProgressList
          rows={batch.rows}
          disabled={batch.running}
          onRetry={retryPicked}
          onRemove={(modelId) => {
            applyPicked(picked.filter((item) => item.id !== modelId));
            batch.setRows((current) => current.filter((row) => row.model !== modelId));
          }}
        />
      </div>
      {picked.length > 1 ? (
        <PickedModelsList
          picked={picked}
          models={discovery.models}
          onChange={handlePickedRowsChange}
          showDefault={existingModelsCount === 0}
          defaultModelId={defaultModelId}
          onDefaultChange={setDefaultModelId}
          flagFields={flagFields}
          contextLengthLabel={contextLengthLabel}
          disabled={saving || batch.running}
        />
      ) : null}
    </Flex>
  );

  const headerActions = useMemo(
    () => (
      <Button
        type="button"
        variant="ghost"
        color="gray"
        size="2"
        aria-label={t('workspace.aiModels.configDocsOpenLabel')}
        style={{ cursor: 'pointer', gap: 6 }}
        onClick={() =>
          window.open(docsOverviewUrl, '_blank', 'noopener,noreferrer')
        }
      >
        <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
      </Button>
    ),
    [t, docsOverviewUrl]
  );

  return (
    <WorkspaceRightPanel
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title={headerTitle}
      icon={headerIcon}
      headerActions={headerActions}
      primaryLabel={mode === 'add' ? t('workspace.aiModels.configAddModel') : t('workspace.aiModels.configUpdateModel')}
      secondaryLabel={t('workspace.aiModels.cancel')}
      primaryLoading={saving || batch.running}
      primaryDisabled={primaryBlocked}
      primaryTooltip={
        !formValid && !saving && Boolean(provider) && Boolean(capability)
          ? t('workspace.aiModels.configFillRequiredTooltip')
          : undefined
      }
      onPrimaryClick={() => {
        void handleSave();
      }}
      onSecondaryClick={onClose}
    >
      <ModelConfigFormBody
        provider={provider}
        capability={capability}
        capLabel={capLabel}
        fields={fields}
        values={values}
        saving={saving}
        error={error}
        hiddenCredentials={hiddenCredentials}
        hiddenFieldNames={perModelMode ? PER_MODEL_FORM_FIELDS : NO_HIDDEN_FIELDS}
        onFieldChange={handleFieldChange}
        onReveal={canReveal ? () => void handleReveal() : undefined}
        revealing={revealState === 'loading'}
        discoverySlot={mode === 'add' ? discoverySlot : undefined}
        modelFieldSlot={modelFieldSlot}
        revealed={revealState === 'shown'}
      />
      {downloadTarget && (
        <EmbeddingDownloadProgress
          open
          modelName={downloadTarget.modelName}
          trustRemoteCode={downloadTarget.trustRemoteCode}
          onReady={handleDownloadReady}
          onCancel={handleDownloadCancel}
        />
      )}
    </WorkspaceRightPanel>
  );
}

function resolveConfigInfoMessage(
  t: TFunction,
  capability: string | null,
  providerName: string,
  capLabel: string
): string {
  if (!capability) {
    return t('workspace.aiModels.configInfoNoCapability', { providerName });
  }
  if (capability === 'text_generation') {
    return t('workspace.aiModels.configInfoTextGeneration', { providerName });
  }
  if (capability === 'embedding') {
    return t('workspace.aiModels.configInfoEmbedding', { providerName });
  }
  if (capability === 'image_generation') {
    return t('workspace.aiModels.configInfoImageGeneration', { providerName });
  }
  return t('workspace.aiModels.configInfoFallback', { capLabel, providerName });
}

function ModelConfigFormBody({
  provider,
  capability,
  capLabel,
  fields,
  values,
  saving,
  error,
  hiddenCredentials,
  hiddenFieldNames,
  onFieldChange,
  onReveal,
  revealing,
  discoverySlot,
  modelFieldSlot,
  revealed,
}: {
  provider: AIModelProvider | null;
  capability: string | null;
  capLabel: string;
  fields: AIModelProviderField[];
  values: Record<string, unknown>;
  saving: boolean;
  error: string | null;
  hiddenCredentials: Set<string>;
  hiddenFieldNames: ReadonlySet<string>;
  onFieldChange: (name: string, value: unknown) => void;
  /** Absent when the deployment does not allow reading stored secrets back. */
  onReveal?: () => void;
  revealing: boolean;
  discoverySlot?: React.ReactNode;
  /** Replaces the plain `model` text input in place. */
  modelFieldSlot?: React.ReactNode;
  revealed: boolean;
}) {
  const { t } = useTranslation();
  const leaveBlankPlaceholder = t('workspace.aiModels.savedLeaveBlank');
  const placeholderFor = (field: AIModelProviderField) =>
    hiddenCredentials.has(field.name) ? leaveBlankPlaceholder : field.placeholder;
  const instanceField = useMemo(
    () =>
      hiddenFieldNames.has('modelFriendlyName')
        ? null
        : (fields.find((f) => f.name === 'modelFriendlyName') ?? null),
    [fields, hiddenFieldNames]
  );

  const compatFields = useMemo(() => {
    return COMPAT_FIELD_NAMES.filter((name) => !hiddenFieldNames.has(name))
      .map((name) => fields.find((f) => f.name === name))
      .filter((f): f is AIModelProviderField => Boolean(f));
  }, [fields, hiddenFieldNames]);

  const modelConfigFields = useMemo(
    () =>
      fields.filter(
        (f) =>
          f.name !== 'modelFriendlyName' &&
          f.name !== 'isReasoning' &&
          f.name !== 'isMultimodal' &&
          f.name !== 'trustRemoteCode' &&
          !hiddenFieldNames.has(f.name)
      ),
    [fields, hiddenFieldNames]
  );

  const section1Title = t('workspace.aiModels.configSectionCapabilityConfig', { capability: capLabel });

  if (fields.length === 0) {
    return (
      <Flex direction="column" gap="3">
        {provider?.notice ? (
          <Callout.Root
            color="red"
            variant="surface"
            size="2"
            style={{ backgroundColor: 'var(--red-a3)' }}
          >
            <Callout.Icon style={{ paddingTop: 'var(--space-1)' }}>
              <MaterialIcon name="report_problem" size={28} color="var(--red-11)" />
            </Callout.Icon>
            <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
              {provider.noticeTitle ? (
                <Text size="2" weight="bold">
                  {provider.noticeTitle}
                </Text>
              ) : null}
              <ProviderNoticeBody notice={provider.notice} />
            </Flex>
          </Callout.Root>
        ) : null}
        {provider.modelName && (
          <Callout.Root color="blue" size="1" variant="surface">
            <Callout.Icon>
              <MaterialIcon name="check_circle" size={16} />
            </Callout.Icon>
            <Callout.Text>
              <Text size="2" style={{ color: 'var(--gray-12)' }}>
                {t('workspace.aiModels.configDefaultModelNotice', { modelName: provider.modelName })}
              </Text>
            </Callout.Text>
          </Callout.Root>
        ) }
          <Callout.Root color="gray" size="1" variant="surface">
            <Callout.Icon>
              <MaterialIcon name="info" size={16} />
            </Callout.Icon>
            <Callout.Text>
              <Text size="2" style={{ color: 'var(--gray-11)' }}>
                {t('workspace.aiModels.configNoConfigRequired')}
              </Text>
            </Callout.Text>
          </Callout.Root>
        
        {error && (
          <Text size="2" style={{ color: 'var(--red-11)', padding: '4px 0' }}>
            {error}
          </Text>
        )}
      </Flex>
    );
  }

  const providerName =
    provider?.name ?? t('workspace.aiModels.configInfoPlaceholderProvider');
  const infoMessage =
    provider && capability
      ? resolveConfigInfoMessage(t, capability, provider.name, capLabel)
      : resolveConfigInfoMessage(t, null, providerName, capLabel);

  return (
    <Flex direction="column" gap="4">
      <Box style={CARD_STYLE}>
        <Flex direction="column" gap="3">
          <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {section1Title}
          </Text>
          <Callout.Root color="gray" size="1" variant="surface">
            <Callout.Text>
              <Text size="2" style={{ color: 'var(--gray-12)' }}>
                {infoMessage}
              </Text>
            </Callout.Text>
          </Callout.Root>
          {instanceField ? (
            <SchemaFormField
              field={toSchemaField(instanceField)}
              value={values[instanceField.name]}
              onChange={onFieldChange}
              disabled={saving}
              startAdornment={fieldStartAdornment(instanceField.name)}
            />
          ) : null}
        </Flex>
      </Box>

      <Box style={CARD_STYLE}>
        <Flex direction="column" gap="3">
          <Flex align="center" justify="between" gap="3">
            <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.aiModels.configSectionModelConfiguration')}
            </Text>
            {onReveal ? (
              <ShowStoredValuesButton
                loading={revealing}
                disabled={saving}
                onClick={onReveal}
              />
            ) : null}
          </Flex>
          {provider ? (
            <Flex direction="column" gap="1">
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {t('workspace.aiModels.configProviderType')}
              </Text>
              <Box style={READONLY_ROW_STYLE}>
                <Text size="2">{t('workspace.aiModels.configProviderApiRow', { name: provider.name })}</Text>
              </Box>
            </Flex>
          ) : null}
          {modelConfigFields.map((field) =>
            field.name === 'model' && modelFieldSlot ? (
              <React.Fragment key={field.name}>{modelFieldSlot}</React.Fragment>
            ) : (
              <SchemaFormField
                key={field.name}
                field={toSchemaField(field, placeholderFor(field))}
                value={values[field.name]}
                onChange={onFieldChange}
                disabled={saving}
                startAdornment={fieldStartAdornment(field.name)}
                revealed={revealed && hiddenCredentials.has(field.name)}
              />
            )
          )}
          {discoverySlot}
        </Flex>
      </Box>

      {compatFields.length > 0 ? (
        <Box style={CARD_STYLE}>
          <Flex direction="column" gap="3">
            <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.aiModels.configSectionCompatibilities')}
            </Text>
            <Flex direction="column" gap="2">
              {compatFields.map((field) => (
                <Flex
                  key={field.name}
                  align="center"
                  justify="between"
                  gap="3"
                  style={{ minHeight: 44, paddingTop: 4, paddingBottom: 4 }}
                >
                  <Flex align="center" gap="3" style={{ minWidth: 0, flex: 1 }}>
                    <MaterialIcon name={compatIcon(field.name)} size={20} color="var(--gray-11)" />
                    <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
                      <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                        {field.displayName}
                      </Text>
                      {field.description ? (
                        <Text size="1" style={{ color: 'var(--gray-10)' }}>
                          {field.description}
                        </Text>
                      ) : null}
                    </Flex>
                  </Flex>
                  <Switch
                    aria-label={field.displayName}
                    checked={boolValue(values[field.name])}
                    onCheckedChange={(checked) => onFieldChange(field.name, checked)}
                    disabled={saving}
                  />
                </Flex>
              ))}
            </Flex>
          </Flex>
        </Box>
      ) : null}

      {error && (
        <Text size="2" style={{ color: 'var(--red-11)', padding: '4px 0' }}>
          {error}
        </Text>
      )}
    </Flex>
  );
}

function toSchemaField(field: AIModelProviderField, placeholder?: string): SchemaField {
  return {
    name: field.name,
    displayName: field.displayName,
    fieldType: field.fieldType,
    required: field.required,
    defaultValue: field.defaultValue,
    placeholder: placeholder ?? field.placeholder,
    description: field.description,
    isSecret: field.isSecret,
    options: field.options?.map((o) => ({ id: o.value, label: o.label })),
    validation: field.validation,
    examples: field.examples,
  } as unknown as SchemaField;
}
