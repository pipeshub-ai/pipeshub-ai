import { expect } from 'chai';
import * as fs from 'fs';
import * as path from 'path';

const harnessRoot = path.resolve(
  __dirname,
  '../../../src/modules/agent-harness',
);

const sourceFiles = (directory: string): string[] =>
  fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(full);
    return entry.name.endsWith('.ts') ? [full] : [];
  });

const IMPORT_SPECIFIER =
  /(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+)['"]([^'"]+)['"]/gm;

describe('agent-harness dependency boundary', () => {
  it('never imports enterprise_search (conversation access goes through ports)', () => {
    const files = sourceFiles(harnessRoot);
    expect(files.length).to.be.greaterThan(10);
    const offenders: string[] = [];
    for (const file of files) {
      const text = fs.readFileSync(file, 'utf8');
      for (const match of text.matchAll(IMPORT_SPECIFIER)) {
        const specifier = match[1] as string;
        const resolved = specifier.startsWith('.')
          ? path.resolve(path.dirname(file), specifier)
          : specifier;
        if (resolved.split(/[\\/]/).includes('enterprise_search')) {
          offenders.push(`${path.relative(harnessRoot, file)} -> ${specifier}`);
        }
      }
    }
    expect(offenders).to.deep.equal([]);
  });
});
