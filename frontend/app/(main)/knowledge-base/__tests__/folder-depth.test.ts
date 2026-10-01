import { describe, expect, it } from 'vitest';
import { folderDepthOf, folderLevelsInPath, uploadPathOf } from '../utils/folder-depth';

describe('folder depth helpers', () => {
  it('counts only the folders in a breadcrumb trail', () => {
    expect(folderDepthOf(undefined)).toBe(0);
    expect(folderDepthOf([{ id: 'kb', name: 'Engineering', nodeType: 'app' }])).toBe(0);
    expect(
      folderDepthOf([
        { id: 'kb', name: 'Engineering', nodeType: 'app' },
        { id: 'f1', name: 'Designs', nodeType: 'folder' },
        { id: 'f2', name: 'Logos', nodeType: 'folder' },
      ]),
    ).toBe(2);
  });

  it('counts the folder levels an upload path adds', () => {
    expect(folderLevelsInPath('notes.txt')).toBe(0);
    expect(folderLevelsInPath('top/notes.txt')).toBe(1);
    expect(folderLevelsInPath('top/sub/notes.txt')).toBe(2);
    expect(folderLevelsInPath(uploadPathOf('top', 'sub/notes.txt', 'notes.txt'))).toBe(2);
    expect(folderLevelsInPath(uploadPathOf('top', undefined, 'notes.txt'))).toBe(1);
  });
});
