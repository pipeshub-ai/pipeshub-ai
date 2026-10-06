import { expect } from 'chai';
import { ESLint, Linter } from 'eslint';
import * as tsParser from '@typescript-eslint/parser';

// ts-node compiles to CJS; the config module is ESM.
const importEsm = new Function('s', 'return import(s)') as (
  s: string,
) => Promise<{ domainBoundaryConfig: Linter.Config }>;

const DOMAIN_FILES = [
  'src/modules/authz/domain/fixture.ts',
  'src/modules/enterprise_search/services/collaboration/domain/fixture.ts',
];
const OUTSIDE_FILE = 'src/modules/authz/service/fixture.ts';

const lint = async (code: string, filePath: string): Promise<string[]> => {
  const { domainBoundaryConfig } = await importEsm('../../eslint.boundaries.mjs');
  const eslint = new ESLint({
    cwd: process.cwd(),
    overrideConfigFile: true,
    overrideConfig: [
      { files: ['**/*.ts'], languageOptions: { parser: tsParser as Linter.Parser } },
      domainBoundaryConfig,
    ],
  });
  const [result] = await eslint.lintText(code, { filePath });
  return result.messages.map((m) => `${m.ruleId}: ${m.message}`);
};

describe('domain import boundaries (PH02-21)', () => {
  const forbidden: Array<[string, string]> = [
    ['mongoose', "import mongoose from 'mongoose';\nexport const x = mongoose;\n"],
    ['express', "import { Request } from 'express';\nexport type X = Request;\n"],
    ['a schema module', "import { Foo } from '../../schema/foo.schema';\nexport const x = Foo;\n"],
    ['a controller module', "import { Bar } from '../../controller/bar';\nexport const x = Bar;\n"],
  ];

  for (const file of DOMAIN_FILES) {
    for (const [name, code] of forbidden) {
      it(`rejects ${name} in ${file}`, async () => {
        const messages = await lint(code, file);
        expect(messages.some((m) => m.startsWith('no-restricted-imports'))).to.equal(true);
      });
    }
  }

  for (const [name, code] of forbidden) {
    it(`allows ${name} outside domain folders`, async () => {
      expect(await lint(code, OUTSIDE_FILE)).to.deep.equal([]);
    });
  }

  it('allows pure imports inside a domain folder', async () => {
    expect(await lint("import { join } from 'path';\nexport const x = join;\n", DOMAIN_FILES[0])).to.deep.equal([]);
  });
});
