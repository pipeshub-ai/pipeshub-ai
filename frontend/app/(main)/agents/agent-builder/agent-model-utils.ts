/**
 * Shared utilities for LLM model selection and node type generation in the
 * agent builder. Centralising here avoids duplicating the priority logic across
 * agent-builder.tsx, use-flow-reconstruction.ts, and create-agent-dialog.tsx.
 */

export interface ModelPreferenceAttribs {
  isDefault?: boolean;
  isReasoning?: boolean;
}

/**
 * Select the preferred model from a list using a 4-level priority:
 *   1. isDefault && isReasoning  — org default that can also reason
 *   2. isReasoning               — first reasoning-capable model
 *   3. isDefault                 — org default even if not reasoning
 *   4. models[0]                 — absolute fallback
 *
 * Returns `undefined` when the list is empty.
 */
export function selectPreferredModel<T extends ModelPreferenceAttribs>(
  models: T[]
): T | undefined {
  if (models.length === 0) return undefined;
  return (
    models.find((m) => m.isDefault && m.isReasoning) ??
    models.find((m) => m.isReasoning) ??
    models.find((m) => m.isDefault) ??
    models[0]
  );
}

/**
 * Generate a stable slugified `data.type` string for an LLM flow node.
 *
 * Format: `llm-{provider}-{modelKey}-{modelName}`, where all three segments
 * are normalised to lowercase alphanumerics + hyphens.
 *
 * All three parameters are optional so the function handles legacy data
 * (string-only model configs) and partially-populated catalog entries.
 */
export function llmNodeTypeSlug(
  provider?: string,
  modelKey?: string,
  modelName?: string
): string {
  const raw = `${provider || ''}-${modelKey || 'default'}-${modelName || ''}`;
  return `llm-${raw.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase()}`;
}
