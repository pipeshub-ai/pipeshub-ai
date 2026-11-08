import { describe, expect, it } from 'vitest';
import type { TFunction } from 'i18next';
import type { CategorizedNodes, EnhancedFolderTreeNode } from '../types';
import { collectionNameError, findOwnedCollectionNamed, folderNameError, hasPathSeparator } from '../utils/name-rules';

const t = ((key: string, fallback?: string, opts?: { name?: string }) =>
  typeof fallback === 'string' ? fallback.replace('{{name}}', opts?.name ?? '') : key) as unknown as TFunction;

function collection(id: string, name: string, role = 'OWNER'): EnhancedFolderTreeNode {
  return { id, name, nodeType: 'app', hasChildren: false, permission: { role, canEdit: true, canDelete: true } } as never;
}

const tree: CategorizedNodes = {
  private: [collection('kb1', 'QA Bug Bash 27 Oct')],
  shared: [collection('kb2', 'Team Notes', 'READER'), collection('kb3', 'Roadmap')],
};

describe('folder names', () => {
  it.each(['a/b', 'a\\b', '/'])('refuses %j, which holds a path separator', (name) => {
    expect(hasPathSeparator(name)).toBe(true);
    expect(folderNameError(name, t)).toBe('Folder names cannot contain "/" or "\\".');
  });

  it('accepts other punctuation', () => {
    expect(folderNameError('Q3 v1.2 - draft (final)', t)).toBeNull();
  });
});

describe('collection names', () => {
  it('finds a collection the user owns, ignoring case and surrounding spaces', () => {
    expect(findOwnedCollectionNamed(tree, '  qa bug bash 27 OCT ')?.id).toBe('kb1');
    expect(findOwnedCollectionNamed(tree, 'roadmap')?.id).toBe('kb3');
    expect(collectionNameError('qa bug bash 27 oct', tree, t)).toBe(
      'You already have a collection named "QA Bug Bash 27 Oct". Choose a different name.',
    );
  });

  it('is not blocked by a collection someone else shared with the user', () => {
    expect(collectionNameError('Team Notes', tree, t)).toBeNull();
  });

  it('lets a collection keep its own name on rename', () => {
    expect(collectionNameError('QA Bug Bash 27 Oct', tree, t, 'kb1')).toBeNull();
  });

  it('allows a new name, and anything before the sidebar has loaded', () => {
    expect(collectionNameError('Fresh', tree, t)).toBeNull();
    expect(collectionNameError('QA Bug Bash 27 Oct', null, t)).toBeNull();
  });
});
