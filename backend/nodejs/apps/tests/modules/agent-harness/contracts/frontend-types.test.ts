import { expect } from 'chai';
import { readdirSync, readFileSync, statSync } from 'fs';
import { dirname, join, relative, resolve } from 'path';
import * as ts from 'typescript';

/**
 * CPD0-13: the frontend target writes its types from the schemas (the frontend
 * compiles without strictNullChecks, under which `z.infer` makes every
 * property optional). Here, with strict on, each written type must be
 * mutually assignable with `z.infer` of the zod schema beside it, so the
 * writer cannot drift from what zod validates.
 */
const APP = resolve(__dirname, '..', '..', '..', '..');
const FRONTEND = resolve(APP, '..', '..', '..', 'frontend', 'lib', 'contracts');
const TREES = ['harness', 'control-plane'].map((root) => join(FRONTEND, root, 'generated'));

function modules(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return modules(full);
    return name.endsWith('.ts') && name !== 'index.ts' && name !== 'enums.ts' && name !== 'errors.ts' && name !== 'policy-data.ts' && name !== 'code-points.ts' ? [full] : [];
  });
}

function checks(): { file: string; source: string } {
  const lines = [
    "import type { z } from 'zod/v3';",
    'type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;',
    'type Assert<T extends true> = T;',
  ];
  TREES.flatMap(modules).forEach((file, i) => {
    const text = readFileSync(file, 'utf8');
    const schema = /export const (\w+Schema) =/.exec(text)?.[1];
    const type = /export type (\w+) =/.exec(text)?.[1];
    if (schema === undefined || type === undefined) throw new Error(`no schema or type in ${file}`);
    const spec = './' + relative(APP, file).replace(/\\/g, '/').replace(/\.ts$/, '');
    lines.push(`import { ${schema} as s${i}, type ${type} as T${i} } from '${spec}';`);
    lines.push(`export type C${i} = Assert<Same<z.infer<typeof s${i}>, T${i}>>;`);
  });
  return { file: join(APP, '__frontend_types_check__.ts'), source: lines.join('\n') };
}

describe('frontend contract types (CPD-0)', function () {
  this.timeout(120_000);

  it('every written type is the zod schema’s type', () => {
    const { file, source } = checks();
    expect(source.split('\n').filter((line) => line.startsWith('export type C')).length).to.be.greaterThan(60);
    const options: ts.CompilerOptions = {
      strict: true,
      noEmit: true,
      skipLibCheck: true,
      target: ts.ScriptTarget.ES2020,
      module: ts.ModuleKind.CommonJS,
      moduleResolution: ts.ModuleResolutionKind.Node10,
      baseUrl: APP,
      // The Node API's zod is 4 now (main #4674), which serves the zod 3 API the frontend tree is written for at zod/v3.
      paths: { 'zod/v3': [join(APP, 'node_modules', 'zod', 'v3')] },
    };
    const host = ts.createCompilerHost(options);
    const read = host.getSourceFile.bind(host);
    host.getSourceFile = (name, version) =>
      resolve(name) === file ? ts.createSourceFile(name, source, version) : read(name, version);
    const program = ts.createProgram([file], options, host);
    const errors = ts
      .getPreEmitDiagnostics(program)
      .map((d) => `${d.file ? relative(dirname(file), d.file.fileName) : ''}: ${ts.flattenDiagnosticMessageText(d.messageText, ' ')}`);
    expect(errors.slice(0, 5)).to.deep.equal([]);
  });
});
