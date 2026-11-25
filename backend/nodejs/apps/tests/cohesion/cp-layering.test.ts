import { expect } from 'chai';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { dirname, join } from 'path';
import { CONTROL_PLANE_RULES, layeringViolations } from '../support/layering';

/**
 * CP layering (05-migration-plan §6.3, CPD-0): the control plane's layers
 * import only downwards, its domain modules never import each other, it never
 * imports the agent harness, and other modules reach it only through its
 * barrel. Checked on the real tree, and each rule on a fixture that breaks it.
 */
const SRC = join(__dirname, '..', '..', 'src');

function tree(files: Record<string, string>): string {
  const root = mkdtempSync(join(tmpdir(), 'cp-layering-'));
  for (const [path, text] of Object.entries(files)) {
    mkdirSync(dirname(join(root, path)), { recursive: true });
    writeFileSync(join(root, path), text);
  }
  return root;
}

const CP = 'modules/control-plane';
const BASE: Record<string, string> = {
  [`${CP}/index.ts`]: "export * from './contracts/generated';\n",
  [`${CP}/contracts/generated/index.ts`]: "import { z } from 'zod/v3';\nexport const s = z.string();\n",
  [`${CP}/domain/policy.ts`]: "import { s } from '../contracts/generated';\nexport const p = s;\n",
  [`${CP}/ports/stores.ts`]: "import type { p } from '../domain/policy';\nexport type Store = typeof p;\n",
  [`${CP}/application/service.ts`]: "import type { Store } from '../ports/stores';\nimport { p } from '../domain/policy';\nexport const svc = (x: Store) => [x, p];\n",
  [`${CP}/adapters/mongo-store.ts`]: "import type { Store } from '../ports/stores';\nimport { Logger } from '../../../libs/logger';\nexport const a = (x: Store) => [x, Logger];\n",
  [`${CP}/modules/skills/driver.ts`]: "import type { Store } from '../../ports/stores';\nexport const d = (x: Store) => x;\n",
  [`${CP}/modules/mcp/driver.ts`]: "import { p } from '../../domain/policy';\nexport const m = p;\n",
  ['libs/logger.ts']: 'export const Logger = 1;\n',
  ['modules/agent-harness/admission.ts']: "import { s } from '../control-plane';\nexport const h = s;\n",
};

const check = (extra: Record<string, string>) => {
  const root = tree({ ...BASE, ...extra });
  try {
    return layeringViolations(root, CONTROL_PLANE_RULES).map((v) => `${v.file} -> ${v.imports}: ${v.rule}`);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
};

describe('CP layering: the control plane module', () => {
  it('CPD0-50 the control plane in src follows its layering', () => {
    expect(layeringViolations(SRC, CONTROL_PLANE_RULES)).to.deep.equal([]);
  });

  it('CPD0-51 the fixture that follows every rule passes', () => {
    expect(check({})).to.deep.equal([]);
  });

  const STORES = {
    ['libs/repositories/control-plane/policy/index.ts']: "import type { Store } from '../../../../modules/control-plane/ports/stores';\nexport const createPolicyStore = (x: Store) => x;\n",
    ['libs/repositories/control-plane/stores.ts']: "export { createPolicyStore } from './policy';\n",
  };

  it('R27-SEAM other code may import the store constructors through their own barrel', () => {
    expect(check({ ...STORES, ['modules/other/wiring.ts']: "import { createPolicyStore } from '../../libs/repositories/control-plane/stores';\n" })).to.deep.equal([]);
  });

  it('R27-SEAM refuses: other code imports a store past the store barrel', () => {
    const found = check({ ...STORES, ['modules/other/wiring.ts']: "import { createPolicyStore } from '../../libs/repositories/control-plane/policy';\n" });
    expect(found).to.deep.equal(['modules/other/wiring.ts -> ../../libs/repositories/control-plane/policy: other modules import modules/control-plane only through index.ts']);
  });

  const broken: Array<[string, Record<string, string>, RegExp]> = [
    ['domain imports a port', { [`${CP}/domain/bad.ts`]: "import type { Store } from '../ports/stores';\n" }, /domain may not import ports/],
    ['domain imports an adapter', { [`${CP}/domain/bad.ts`]: "import { a } from '../adapters/mongo-store';\n" }, /domain may not import adapters/],
    ['domain imports libs', { [`${CP}/domain/bad.ts`]: "import { Logger } from '../../../libs/logger';\n" }, /domain imports nothing outside/],
    ['domain imports a package', { [`${CP}/domain/bad.ts`]: "import mongoose from 'mongoose';\n" }, /domain imports no package but zod/],
    ['ports import application', { [`${CP}/ports/bad.ts`]: "import { svc } from '../application/service';\n" }, /ports may not import application/],
    ['application imports an adapter', { [`${CP}/application/bad.ts`]: "import { a } from '../adapters/mongo-store';\n" }, /application may not import adapters/],
    ['application imports a module', { [`${CP}/application/bad.ts`]: "import { d } from '../modules/skills/driver';\n" }, /application may not import module:skills/],
    ['one module imports another', { [`${CP}/modules/mcp/bad.ts`]: "import { d } from '../skills/driver';\n" }, /module:mcp may not import module:skills/],
    ['a module imports a core service', { [`${CP}/modules/mcp/bad.ts`]: "import { svc } from '../../application/service';\n" }, /module:mcp may not import application/],
    ['the control plane imports the agent harness', { [`${CP}/adapters/bad.ts`]: "import { h } from '../../agent-harness/admission';\n" }, /never imports modules\/agent-harness/],
    ['the agent harness imports past the barrel', { ['modules/agent-harness/bad.ts']: "import { p } from '../control-plane/domain/policy';\n" }, /only through index.ts/],
    ['a file outside the known layers', { [`${CP}/helpers/util.ts`]: 'export const u = 1;\n' }, /outside the known layers/],
  ];
  for (const [name, files, rule] of broken) {
    it(`CPD0-51 refuses: ${name}`, () => {
      const found = check(files);
      expect(found.some((v) => rule.test(v)), found.join('\n')).to.equal(true);
    });
  }
});
