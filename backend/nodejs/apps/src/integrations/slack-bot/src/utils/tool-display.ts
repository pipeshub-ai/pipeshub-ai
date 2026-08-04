/** Drops any `{app}__` namespace prefix, splits on separators, title-cases. */
export function humanizeToolName(name: string): string {
  const segment = name.includes("__")
    ? name.slice(name.lastIndexOf("__") + 2)
    : name;
  const words = segment.split(/[_\-\s]+/).filter(Boolean);
  if (words.length === 0) return "Used a tool";
  return words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ");
}

/** Past-tense label for completed tool rows in the activity timeline. */
export function toolActivityLabel(
  toolName: string | undefined,
  displayName?: string,
): string {
  if (displayName) return displayName;
  if (!toolName) return "Used a tool";
  return humanizeToolName(toolName);
}

/** Present-tense label for in-progress tool status. */
export function toolStatusLabel(toolName: string, displayName?: string): string {
  if (displayName) return displayName;
  return `Using ${humanizeToolName(toolName)}`;
}
