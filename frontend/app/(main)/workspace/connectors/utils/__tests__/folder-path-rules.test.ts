import { describe, it, expect } from 'vitest';
import enUS from '@/lib/i18n/locales/en-US.json';
import cases from './folder-path-cases.json';
import {
  FOLDER_PATH_PROBLEM_KEYS,
  folderPathProblems,
  looksLikeAFileType,
  type FolderPathProblem,
  type FolderPathStyle,
} from '../folder-path-rules';

type Case = {
  value: string;
  exclude: boolean;
  pathStyle: FolderPathStyle;
  wildcards: boolean;
  problems: FolderPathProblem[];
};

function lookup(key: string): unknown {
  return key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], enUS);
}

describe('folderPathProblems', () => {
  // The cases are the server's answers (folder_value_problems), shared with its tests.
  it.each((cases as Case[]).map((c) => [JSON.stringify([c.value.slice(0, 40), c.exclude, c.pathStyle, c.wildcards]), c]))(
    'refuses a new value for the reasons the server does: %s',
    (_label, c) => {
      expect(folderPathProblems(c.value, c)).toEqual(c.problems);
    }
  );

  it('has a message for every reason', () => {
    for (const key of Object.values(FOLDER_PATH_PROBLEM_KEYS)) {
      expect(typeof lookup(key)).toBe('string');
    }
  });
});

describe('looksLikeAFileType', () => {
  it.each([
    ['*.pdf', true],
    ['reports/*.docx', true],
    ['reports/*', false],
    ['customer/*/quotes', false],
    ['a/*.toolongext', false],
  ])('%s -> %s', (value, expected) => {
    expect(looksLikeAFileType(value)).toBe(expected);
  });
});
