import { expect } from 'chai';
import { readFileSync, writeFileSync } from 'fs';
import { join } from 'path';
import ts from 'typescript';

/**
 * The control plane's public API is the export set of its two barrels: code kept outside this tree (an edition's)
 * imports only these names, so dropping or renaming one breaks it. A change to the set is deliberate: re-record with
 *
 *   CP_BARREL_UPDATE=1 npx cross-env TS_NODE_PROJECT=tsconfig.test.json mocha --no-config --require ts-node/register --exit tests/cohesion/cp-barrel-api.test.ts
 */
const SRC = join(__dirname, '..', '..', 'src');
const SNAPSHOT = join(__dirname, 'cp-barrel-api.json');
const BARRELS = ['modules/control-plane/index.ts', 'libs/repositories/control-plane/stores.ts'];

function exportsOf(files: readonly string[]): Record<string, string[]> {
  const roots = files.map((f) => join(SRC, f));
  const program = ts.createProgram(roots, {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.CommonJS,
    moduleResolution: ts.ModuleResolutionKind.Node10,
    esModuleInterop: true,
    skipLibCheck: true,
    noEmit: true,
  });
  const checker = program.getTypeChecker();
  const out: Record<string, string[]> = {};
  files.forEach((file, i) => {
    const symbol = checker.getSymbolAtLocation(program.getSourceFile(roots[i]!)!)!;
    out[file] = checker.getExportsOfModule(symbol).map((s) => s.getName()).sort();
  });
  return out;
}

describe('R27-SEAM the control plane barrels are its public API', function () {
  this.timeout(120000);

  it('exports exactly the recorded names', () => {
    const now = exportsOf(BARRELS);
    if (process.env.CP_BARREL_UPDATE === '1') {
      writeFileSync(SNAPSHOT, JSON.stringify(now, null, 2) + '\n');
      return;
    }
    const recorded = JSON.parse(readFileSync(SNAPSHOT, 'utf8')) as Record<string, string[]>;
    for (const file of BARRELS) {
      const added = now[file]!.filter((n) => !recorded[file]!.includes(n));
      const removed = recorded[file]!.filter((n) => !now[file]!.includes(n));
      expect({ file, added, removed }, 'the public API changed: re-record it (see this file)').to.deep.equal({ file, added: [], removed: [] });
    }
  });
});
