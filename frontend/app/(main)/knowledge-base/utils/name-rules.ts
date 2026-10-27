import type { TFunction } from 'i18next';
import type { CategorizedNodes, EnhancedFolderTreeNode } from '../types';

/** The server refuses these in folder names too (validators.ts, folderNameSchema). */
export function hasPathSeparator(name: string): boolean {
  return /[\\/]/.test(name);
}

const normalise = (name: string) => name.trim().toLocaleLowerCase();

/**
 * A collection the user owns with this name (trimmed, case-insensitive), if any.
 * Checks only the collections loaded in the sidebar, and only the ones the user
 * owns: a collection someone shared with them never blocks a name.
 */
export function findOwnedCollectionNamed(
  categorized: CategorizedNodes | null,
  name: string,
  excludeId?: string,
): EnhancedFolderTreeNode | null {
  if (!categorized) return null;
  const target = normalise(name);
  if (!target) return null;
  for (const node of [...categorized.private, ...categorized.shared]) {
    if (node.id === excludeId) continue;
    if (node.permission?.role !== 'OWNER') continue;
    if (normalise(node.name ?? '') === target) return node;
  }
  return null;
}

/** Why this folder name is refused, or null when it is fine. */
export function folderNameError(name: string, t: TFunction): string | null {
  return hasPathSeparator(name)
    ? t('kb.nameRules.folderSeparator', 'Folder names cannot contain "/" or "\\".')
    : null;
}

/** Why this collection name is refused, or null when it is fine. */
export function collectionNameError(
  name: string,
  categorized: CategorizedNodes | null,
  t: TFunction,
  excludeId?: string,
): string | null {
  const clash = findOwnedCollectionNamed(categorized, name, excludeId);
  return clash
    ? t('kb.nameRules.collectionExists', 'You already have a collection named "{{name}}". Choose a different name.', {
        name: clash.name,
      })
    : null;
}
