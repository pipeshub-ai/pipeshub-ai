import type { AvailableLlmModel, ModelInfo } from '@/chat/types';
import { modelDisplayLabel } from '@/chat/utils/apply-conversation-model-info';
import type { ModelAccessView } from '@/app/(main)/workspace/ai-accounts/types';
import type { RoleModelRef, RoleModels } from '../types';
import type { AgentFormPayload } from './types';

/** The role key every sub-agent without its own entry falls to. */
export const DEFAULT_ROLE_KEY = 'default';
export const MAX_ROLE_MODELS = 64;
const MAX_ROLE_KEY_LENGTH = 64;

/**
 * The key a role name is saved (and looked up at spawn) under. Held to the
 * server's answer by the `roleKeys` table of `contracts/harness/vectors/child-model.json`.
 */
export function normalizeRoleKey(name: string): string {
  return name
    .replace(/[^A-Za-z0-9_.-]+/g, '-')
    .toLowerCase()
    .replace(/^[^a-z0-9]+/, '')
    .slice(0, MAX_ROLE_KEY_LENGTH);
}

function toRoleModelRef(value: unknown): RoleModelRef | null {
  if (value === null || typeof value !== 'object') return null;
  const { modelKey, modelName, provider } = value as Record<string, unknown>;
  if (typeof modelKey !== 'string' || typeof modelName !== 'string' || typeof provider !== 'string') return null;
  if (!modelKey || !modelName) return null;
  return { modelKey, modelName, provider };
}

/** An agent's stored role models, entry by entry; anything that is not one is left out. */
export function toRoleModels(value: unknown): RoleModels {
  const roleModels: RoleModels = {};
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return roleModels;
  for (const [role, entry] of Object.entries(value)) {
    const ref = toRoleModelRef(entry);
    if (ref) roleModels[role] = ref;
  }
  return roleModels;
}

/**
 * `roleModels` as the save sends it: the edited map; `{}` when the author
 * removed every entry of an agent that had some (so the server clears them);
 * nothing at all for an agent that never had any, whose payload stays as it was.
 */
export function roleModelsPayload(edited: unknown, stored: unknown): Pick<AgentFormPayload, 'roleModels'> {
  const roleModels = toRoleModels(edited);
  if (Object.keys(roleModels).length > 0) return { roleModels };
  return Object.keys(toRoleModels(stored)).length > 0 ? { roleModels: {} } : {};
}

export interface RoleModelChoice extends RoleModelRef {
  label: string;
  usdPerMTok: number | null;
  allowed: boolean;
}

export interface RoleModelCatalog {
  choices: RoleModelChoice[];
  ceilings: Record<string, number>;
}

export const sameModel = (a: RoleModelRef, b: RoleModelRef): boolean =>
  a.modelKey === b.modelKey && a.modelName === b.modelName;

/** A stable id for a model in a select: two entries of one key list different models. */
export const modelChoiceId = (model: RoleModelRef): string => JSON.stringify([model.modelKey, model.modelName]);

export function roleModelLabel(model: RoleModelRef, configured: AvailableLlmModel[]): string {
  return modelDisplayLabel({ modelKey: model.modelKey, modelName: model.modelName } as ModelInfo, configured);
}

/**
 * What a role's select can offer: the org's model-access view when it names
 * the models (MA1), else every configured model (an older server, or the
 * view could not be read: the server still refuses what is not allowed).
 */
export function roleModelCatalog(view: ModelAccessView | null, configured: AvailableLlmModel[]): RoleModelCatalog {
  if (view?.modelChoices) {
    return {
      choices: view.modelChoices.map((choice) => ({ ...choice, label: roleModelLabel(choice, configured) })),
      ceilings: view.roleCostCeilings ?? {},
    };
  }
  return {
    choices: configured.map((model) => ({
      modelKey: model.modelKey,
      modelName: model.modelName,
      provider: model.provider,
      label: roleModelLabel(model, configured),
      usdPerMTok: null,
      allowed: true,
    })),
    ceilings: {},
  };
}

/** The price bound on a role: the least of its own and `default`; null when neither is set. */
export function roleCostCeiling(ceilings: Record<string, number>, roleKey: string): number | null {
  const bounds = [ceilings[roleKey], ceilings[DEFAULT_ROLE_KEY]].filter(
    (bound): bound is number => typeof bound === 'number',
  );
  return bounds.length > 0 ? Math.min(...bounds) : null;
}

/** The models a role may be given. Under a ceiling a model with no price cannot be shown to fit, so it is not offered. */
export function choicesForRole(catalog: RoleModelCatalog, roleKey: string): RoleModelChoice[] {
  const ceiling = roleCostCeiling(catalog.ceilings, roleKey);
  return catalog.choices.filter(
    (choice) =>
      choice.allowed && (ceiling === null || (choice.usdPerMTok !== null && choice.usdPerMTok <= ceiling)),
  );
}
