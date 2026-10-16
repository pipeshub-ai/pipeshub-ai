import { beforeEach, describe, expect, it, vi } from 'vitest';

const saveTextFile = vi.hoisted(() => vi.fn());
vi.mock('../save-text-file', () => ({ saveTextFile }));

import { codeFileName, downloadCode } from '../code-download';

beforeEach(() => saveTextFile.mockReset());

describe('codeFileName', () => {
  it.each([
    ['python', 'code.py'],
    ['py', 'code.py'],
    ['typescript', 'code.ts'],
    ['tsx', 'code.tsx'],
    ['c++', 'code.cpp'],
    ['bash', 'code.sh'],
    ['powershell', 'code.ps1'],
    ['yml', 'code.yaml'],
    ['sql', 'code.sql'],
    ['json', 'code.json'],
  ])('names a %s block %s', (language, name) => {
    expect(codeFileName(language)).toBe(name);
  });

  it('ignores case, spaces and a language- prefix', () => {
    expect(codeFileName('  JavaScript ')).toBe('code.js');
    expect(codeFileName('language-python')).toBe('code.py');
  });

  it.each([
    '',
    'text',
    'plaintext',
    'brainfuck',
    'bat',
    'cmd',
    'vbs',
    'exe',
    '../../evil.sh',
    '__proto__',
    'constructor',
    'toString',
    'hasOwnProperty',
  ])(
    'saves %j as plain text, never with an extension the system would run',
    (language) => {
      expect(codeFileName(language)).toBe('code.txt');
    },
  );
});

describe('downloadCode', () => {
  it('saves the code under its language file name as plain text', () => {
    downloadCode('print("hi")\n', 'python');

    expect(saveTextFile).toHaveBeenCalledWith('code.py', 'print("hi")\n', 'text/plain');
  });

  it('ends the file with a single new line', () => {
    downloadCode('a = 1', 'python');
    downloadCode('b = 2\n', 'python');

    expect(saveTextFile.mock.calls[0][1]).toBe('a = 1\n');
    expect(saveTextFile.mock.calls[1][1]).toBe('b = 2\n');
  });

  it('keeps the code exactly as shown, including its indentation and blank lines', () => {
    const code = 'def f():\n\n    return 1\n';

    downloadCode(code, 'python');

    expect(saveTextFile.mock.calls[0][1]).toBe(code);
  });
});
