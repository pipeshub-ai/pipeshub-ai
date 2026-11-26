/**
 * The name a person sees for a configured model (P2.2). One rule with
 * Python's `model_display_name` (contracts/harness/vectors/model-display-names.json):
 * the admin's display name, qualified by the model when the entry lists
 * several; else the model name; `Unavailable model` when the entry is gone.
 * The model key, a deployment name and keys are never read.
 */
export const UNAVAILABLE_MODEL = 'Unavailable model';

const modelNames = (entry: Record<string, unknown>): string[] => {
  const configuration = entry['configuration'];
  const raw =
    typeof configuration === 'object' && configuration !== null
      ? (configuration as Record<string, unknown>)['model']
      : undefined;
  return typeof raw === 'string'
    ? raw
        .split(',')
        .map((name) => name.trim())
        .filter((name) => name.length > 0)
    : [];
};

export function modelDisplayName(
  entry: Record<string, unknown> | null | undefined,
  modelName: string | null | undefined,
): string {
  if (entry === null || entry === undefined) return UNAVAILABLE_MODEL;
  const names = modelNames(entry);
  const raw = entry['modelFriendlyName'];
  const friendly = typeof raw === 'string' ? raw.trim() : '';
  if (friendly.length > 0) {
    return names.length > 1 && modelName
      ? `${friendly} (${modelName})`
      : friendly;
  }
  return modelName || names[0] || UNAVAILABLE_MODEL;
}
