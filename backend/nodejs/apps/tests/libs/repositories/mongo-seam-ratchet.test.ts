/**
 * Ratchet for the repository seam. Code outside the Mongo repositories must
 * not grow its use of Mongoose: the list of files that import a schema module
 * or `mongoose`, and the number of `.save()` / `new Model(...)` writes per
 * file, may only shrink. The committed baseline is
 * mongo-seam-baseline.json; when a count drops the test fails until the
 * baseline is re-recorded, so shrinkage is locked in:
 *
 *   SEAM_RATCHET_UPDATE=1 npx cross-env TS_NODE_PROJECT=tsconfig.test.json mocha --no-config --require ts-node/register --exit tests/libs/repositories/mongo-seam-ratchet.test.ts
 *
 * Exempt entirely: libs/repositories/**\/mongo.ts, the boot migrations and the
 * connection service (libs/services/mongo.service.ts). Schema definition files
 * (`**\/schema/**`, `*.schema.ts`, enterprise_search/types/conversation.interfaces.ts)
 * may import Mongoose, but a `.save()`, `new Model()` or model query in them counts.
 */
import { expect } from 'chai';
import fs from 'fs';
import path from 'path';
import ts from 'typescript';

const SRC = path.resolve(__dirname, '../../../src');
const BASELINE = path.resolve(__dirname, 'mongo-seam-baseline.json');

interface Baseline {
  imports: string[];
  saves: Record<string, number>;
  newModels: Record<string, number>;
}

/** Files that may use Mongoose freely: the Mongo repositories, the connection, the boot migrations. */
const isExempt = (rel: string): boolean =>
  /^libs\/repositories\/(.*\/)?mongo\.ts$/.test(rel) ||
  rel === 'libs/services/mongo.service.ts' ||
  // typing helper for filters that are built up piece by piece
  rel === 'libs/utils/mongo-filter.ts' ||
  rel === 'config.ts' ||
  rel.startsWith('modules/configuration_manager/services/migrations/');

/**
 * Schema definitions and the typing of the stored documents may import Mongoose,
 * but a `.save()`, a `new Model()` or a model query in them still counts.
 */
export const isDefinitionFile = (rel: string): boolean =>
  /(^|\/)schema\//.test(rel) ||
  /\.schema\.ts$/.test(rel) ||
  rel === 'modules/enterprise_search/types/conversation.interfaces.ts';

const MODEL_QUERIES = new Set([
  'find', 'findOne', 'findById', 'findOneAndUpdate', 'findByIdAndUpdate', 'findOneAndDelete',
  'findByIdAndDelete', 'create', 'insertMany', 'updateOne', 'updateMany', 'deleteOne', 'deleteMany',
  'countDocuments', 'estimatedDocumentCount', 'aggregate', 'bulkWrite', 'exists', 'distinct',
]);

const isStoreModule = (spec: string): boolean =>
  spec === 'mongoose' || /\/schema\/[^/]+\.schema$/.test(spec);

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (entry.name.endsWith('.ts') && !entry.name.endsWith('.d.ts')) out.push(full);
  }
  return out;
}

/** What one source file uses of the store: imports, `.save()` calls, `new <SchemaModel>()`. */
export function scanText(
  text: string,
  fileName = 'x.ts',
  definitionOnly = false,
): { imports: boolean; saves: number; newModels: number } {
  const sf = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true);
  const models = new Set<string>(); // names imported from schema modules
  let imports = false;
  let saves = 0;
  let newModels = 0;
  const queries = new Set<string>(); // calls like `Model.find(...)` on locally defined models
  const visit = (node: ts.Node): void => {
    if (
      definitionOnly &&
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.initializer &&
      /\bmodel\s*[<(]|\.models\b/.test(node.initializer.getText(sf))
    ) {
      models.add(node.name.text); // a model defined in this file
    }
    if (
      definitionOnly &&
      ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      ts.isIdentifier(node.expression.expression) &&
      MODEL_QUERIES.has(node.expression.name.text)
    ) {
      queries.add(node.expression.expression.text + '.' + String(node.getStart(sf)));
    }
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      if (isStoreModule(node.moduleSpecifier.text)) {
        imports = !definitionOnly;
        const c = definitionOnly && node.moduleSpecifier.text === 'mongoose' ? undefined : node.importClause;
        if (c?.name) models.add(c.name.text);
        if (c?.namedBindings && ts.isNamedImports(c.namedBindings)) {
          c.namedBindings.elements.forEach((e) => models.add(e.name.text));
        }
      }
    } else if (
      ts.isImportEqualsDeclaration(node) &&
      ts.isExternalModuleReference(node.moduleReference) &&
      ts.isStringLiteral(node.moduleReference.expression) &&
      isStoreModule(node.moduleReference.expression.text)
    ) {
      imports = !definitionOnly; // import x = require('mongoose')
      models.add(node.name.text);
    } else if (
      ts.isExportDeclaration(node) &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier) &&
      isStoreModule(node.moduleSpecifier.text)
    ) {
      imports = !definitionOnly; // export { Schema } from 'mongoose'
    } else if (
      ts.isCallExpression(node) &&
      node.arguments.length >= 1 &&
      ts.isStringLiteral(node.arguments[0]) &&
      isStoreModule(node.arguments[0].text) &&
      (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
        (ts.isIdentifier(node.expression) && node.expression.text === 'require'))
    ) {
      imports = !definitionOnly; // require('mongoose') / import('...schema')
    }
    if (
      ts.isCallExpression(node) &&
      ((ts.isPropertyAccessExpression(node.expression) && node.expression.name.text === 'save') ||
        (ts.isElementAccessExpression(node.expression) &&
          ts.isStringLiteralLike(node.expression.argumentExpression) &&
          node.expression.argumentExpression.text === 'save'))
    ) {
      saves += 1; // obj.save() and obj['save']()
    }
    if (ts.isNewExpression(node) && ts.isIdentifier(node.expression) && models.has(node.expression.text)) {
      newModels += 1;
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  for (const q of queries) if (models.has(q.split('.')[0])) newModels += 1;
  return { imports, saves, newModels };
}

const baselineFilesUnder = (dir: string, out: string[] = []): string[] => {
  if (!fs.existsSync(dir)) return out;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) baselineFilesUnder(full, out);
    else if (entry.name.endsWith('.json') && entry.name.includes('baseline')) out.push(full);
  }
  return out;
};

/**
 * The committed baseline plus every `*baseline*.json` found under the directories
 * in `dirs` (env `SEAM_BASELINE_DIRS`, separated by the path delimiter; default none),
 * for code kept outside this tree. Lists are united and per-file counts added.
 */
export function loadBaseline(dirs: string[] = []): Baseline {
  const merged: Baseline = JSON.parse(fs.readFileSync(BASELINE, 'utf8'));
  for (const dir of dirs) {
    for (const file of baselineFilesUnder(dir).sort()) {
      const extra = JSON.parse(fs.readFileSync(file, 'utf8')) as Partial<Baseline>;
      merged.imports = [...new Set([...merged.imports, ...(extra.imports ?? [])])].sort();
      for (const k of ['saves', 'newModels'] as const) {
        for (const [f, n] of Object.entries(extra[k] ?? {})) merged[k][f] = (merged[k][f] ?? 0) + n;
      }
    }
  }
  return merged;
}

const extraDirs = (): string[] =>
  (process.env.SEAM_BASELINE_DIRS ?? '').split(path.delimiter).filter(Boolean);

export function scan(): Baseline {
  const result: Baseline = { imports: [], saves: {}, newModels: {} };
  for (const file of walk(SRC).sort()) {
    const rel = path.relative(SRC, file).split(path.sep).join('/');
    if (isExempt(rel)) continue;
    const { imports, saves, newModels } = scanText(fs.readFileSync(file, 'utf8'), file, isDefinitionFile(rel));
    if (imports) result.imports.push(rel);
    if (saves) result.saves[rel] = saves;
    if (newModels) result.newModels[rel] = newModels;
  }
  return result;
}

describe('repository seam ratchet: Mongoose use outside the Mongo repositories', function () {
  this.timeout(60000);

  it('only shrinks against the committed baseline', () => {
    const now = scan();

    if (process.env.SEAM_RATCHET_UPDATE === '1') {
      if (fs.existsSync(BASELINE)) {
        const grew = growth(JSON.parse(fs.readFileSync(BASELINE, 'utf8')), now);
        if (grew.length) throw new Error(`refusing to record growth:\n${grew.join('\n')}`);
      }
      fs.writeFileSync(BASELINE, JSON.stringify(now, null, 2) + '\n');
      return;
    }

    const base = loadBaseline(extraDirs());
    const grew = growth(base, now);
    expect(
      grew,
      'New Mongoose use outside libs/repositories/**/mongo.ts. Call a repository instead:\n' + grew.join('\n'),
    ).to.deep.equal([]);
    expect(
      growth(now, base),
      'Mongoose use shrank. Lock it in: SEAM_RATCHET_UPDATE=1 (see the header of this file)',
    ).to.deep.equal([]);
  });

  it('sees every way of importing the store and of saving', () => {
    expect(scanText("import m = require('mongoose');").imports).to.equal(true);
    expect(scanText("export { Schema } from 'mongoose';").imports).to.equal(true);
    expect(scanText("export * from '../schema/x.schema';").imports).to.equal(true);
    expect(scanText("const m = require('mongoose');").imports).to.equal(true);
    expect(scanText("void import('mongoose');").imports).to.equal(true);
    expect(scanText("import { a } from './other';").imports).to.equal(false);
    expect(scanText("d.save(); d['save'](); // d.save()").saves).to.equal(2);
    expect(scanText("const s = 'd.save()';").saves).to.equal(0);
    expect(scanText("import { Users } from './schema/users.schema'; new Users({});").newModels).to.equal(1);
    // a schema definition may import the store, but writing or querying through it still counts
    const def = "import mongoose from 'mongoose'; const Thing = mongoose.model('t', s); Thing.find({}); new Thing({}); doc.save();";
    expect(scanText(def, 'x.schema.ts', true)).to.deep.equal({ imports: false, saves: 1, newModels: 2 });
    expect(scanText("import mongoose from 'mongoose'; export const Thing = mongoose.model('t', s);", 'x.schema.ts', true)).to.deep.equal({ imports: false, saves: 0, newModels: 0 });
  });

  it('merges baseline files found under SEAM_BASELINE_DIRS into the committed one', () => {
    const dir = fs.mkdtempSync(path.join(process.env.TMPDIR ?? '.', 'seam-baseline-'));
    try {
      fs.mkdirSync(path.join(dir, 'nested'));
      fs.writeFileSync(
        path.join(dir, 'nested', 'extra-baseline.json'),
        JSON.stringify({ imports: ['x/a.ts'], saves: { 'x/a.ts': 2 }, newModels: {} }),
      );
      fs.writeFileSync(path.join(dir, 'other.json'), JSON.stringify({ imports: ['ignored.ts'] }));
      const base = loadBaseline([dir]);
      expect(base.imports).to.include('x/a.ts');
      expect(base.imports).to.not.include('ignored.ts');
      expect(base.saves['x/a.ts']).to.equal(2);
      expect(loadBaseline([]).imports).to.not.include('x/a.ts');
      expect(loadBaseline([path.join(dir, 'missing')]).imports).to.not.include('x/a.ts');
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it('scans the whole source tree and exempts the seam', () => {
    // Every aggregate has moved behind a repository, so the committed baseline is empty;
    // an empty scan must mean "clean", not "the walker saw nothing".
    expect(walk(SRC).length).to.be.greaterThan(500);
    expect(isExempt('libs/repositories/org/mongo.ts')).to.equal(true);
    expect(isExempt('modules/oauth_provider/services/pat.service.ts')).to.equal(false);
    const now = scan();
    expect(now.imports).to.deep.equal([]);
    expect(now.saves).to.deep.equal({});
    expect(now.newModels).to.deep.equal({});
  });
});

/** Entries of `b` that are new or larger than in `a`. */
function growth(a: Baseline, b: Baseline): string[] {
  const out: string[] = [];
  for (const f of b.imports) if (!a.imports.includes(f)) out.push(`import: ${f}`);
  for (const k of ['saves', 'newModels'] as const) {
    for (const [f, n] of Object.entries(b[k])) {
      if (n > (a[k][f] ?? 0)) out.push(`${k}: ${f} ${a[k][f] ?? 0} -> ${n}`);
    }
  }
  return out;
}
