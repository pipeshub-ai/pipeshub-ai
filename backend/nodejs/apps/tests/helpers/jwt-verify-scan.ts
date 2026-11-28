import * as fs from 'fs';
import * as path from 'path';
import * as ts from 'typescript';

/** One place a source file checks a JWT signature, with the text of the key it checks against. */
export interface VerifyCall {
  line: number;
  key: string;
}

/** Packages that check a JWT signature, and the names under which they do it. */
const VERIFY_NAMES: Record<string, ReadonlySet<string>> = {
  jsonwebtoken: new Set(['verify']),
  jws: new Set(['verify']),
  jose: new Set([
    'jwtVerify',
    'compactVerify',
    'flattenedVerify',
    'generalVerify',
  ]),
};

/** The argument that holds the key: `jws.verify(signature, algorithm, key)` takes it third. */
const KEY_ARG: Record<string, number> = { jsonwebtoken: 1, jws: 2, jose: 1 };

/**
 * Finds every place a source file checks a JWT signature, whatever its spelling:
 * `jwt.verify(...)`, `jwt['verify'](...)`, a line-broken `jwt\n.verify(...)`, named, renamed,
 * namespace and re-exported imports, requires and dynamic imports, an alias of the module
 * (`const j = jwt`), a destructured or detached `verify`, `.call`/`.apply`, and type
 * assertions (`jwt!`, `(jwt as any)`, `<any>jwt`, `satisfies`). A computed key on the module
 * is reported too, since it cannot be shown to be something else. The same goes for `jws`
 * and `jose`.
 */
export function findVerifyCalls(source: string): VerifyCall[] {
  const file = ts.createSourceFile(
    'x.ts',
    source,
    ts.ScriptTarget.Latest,
    true,
  );
  const modules = new Map<string, string>(); // local name -> package it refers to
  const verifiers = new Map<string, string>(); // local name of a verify function -> its package

  const packageOf = (n: ts.Node | undefined): string | undefined => {
    if (
      !n ||
      !ts.isCallExpression(n) ||
      n.arguments.length !== 1 ||
      !n.arguments[0] ||
      !ts.isStringLiteralLike(n.arguments[0])
    ) {
      return undefined;
    }
    const callee = n.expression;
    const loads =
      (ts.isIdentifier(callee) && callee.text === 'require') ||
      callee.kind === ts.SyntaxKind.ImportKeyword;
    const name = n.arguments[0].text;
    return loads && name in VERIFY_NAMES ? name : undefined;
  };
  const unwrap = (n: ts.Expression | undefined): ts.Expression | undefined => {
    while (
      n &&
      (ts.isAwaitExpression(n) ||
        ts.isParenthesizedExpression(n) ||
        ts.isAsExpression(n) ||
        ts.isNonNullExpression(n) ||
        ts.isTypeAssertionExpression(n) ||
        ts.isSatisfiesExpression(n))
    ) {
      n = n.expression;
    }
    return n;
  };
  // The package an expression refers to, if it refers to one.
  const moduleOf = (n: ts.Expression | undefined): string | undefined => {
    const e = unwrap(n);
    if (!e) return undefined;
    if (ts.isIdentifier(e)) return modules.get(e.text);
    return packageOf(e);
  };
  // The package whose verify function a member access picks (`jwt.verify`), if it picks one.
  const verifyMemberOf = (e: ts.Expression): string | undefined => {
    if (!isMember(e)) return undefined;
    const pkg = moduleOf(e.expression);
    const m = memberName(e);
    return pkg && m && VERIFY_NAMES[pkg]?.has(m) ? pkg : undefined;
  };
  // The name a member access picks, when it is a literal; `undefined` for a computed key.
  const memberName = (e: ts.Expression): string | undefined => {
    if (ts.isPropertyAccessExpression(e)) return e.name.text;
    if (
      ts.isElementAccessExpression(e) &&
      ts.isStringLiteralLike(e.argumentExpression)
    ) {
      return e.argumentExpression.text;
    }
    return undefined;
  };
  const isMember = (
    e: ts.Node,
  ): e is ts.PropertyAccessExpression | ts.ElementAccessExpression =>
    ts.isPropertyAccessExpression(e) || ts.isElementAccessExpression(e);

  const bindOnce = (node: ts.Node): void => {
    if (
      ts.isImportDeclaration(node) &&
      ts.isStringLiteral(node.moduleSpecifier)
    ) {
      const pkg = node.moduleSpecifier.text;
      const names = VERIFY_NAMES[pkg];
      const clause = node.importClause;
      if (names && clause) {
        if (clause.name) modules.set(clause.name.text, pkg);
        const nb = clause.namedBindings;
        if (nb && ts.isNamespaceImport(nb)) modules.set(nb.name.text, pkg);
        if (nb && ts.isNamedImports(nb)) {
          for (const el of nb.elements) {
            if (names.has((el.propertyName ?? el.name).text))
              verifiers.set(el.name.text, pkg);
          }
        }
      }
    }
    if (
      ts.isImportEqualsDeclaration(node) &&
      ts.isExternalModuleReference(node.moduleReference) &&
      ts.isStringLiteral(node.moduleReference.expression) &&
      node.moduleReference.expression.text in VERIFY_NAMES
    ) {
      modules.set(node.name.text, node.moduleReference.expression.text);
    }
    if (ts.isVariableDeclaration(node) && node.initializer) {
      const init = unwrap(node.initializer);
      const pkg = moduleOf(init);
      const names = pkg ? VERIFY_NAMES[pkg] : undefined;
      if (pkg && ts.isIdentifier(node.name)) modules.set(node.name.text, pkg);
      if (pkg && names && ts.isObjectBindingPattern(node.name)) {
        for (const el of node.name.elements) {
          const original =
            el.propertyName && ts.isIdentifier(el.propertyName)
              ? el.propertyName.text
              : undefined;
          const local = ts.isIdentifier(el.name) ? el.name.text : '';
          if (names.has(original ?? local) && local) verifiers.set(local, pkg);
        }
      }
      // const v = jwt.verify
      const picked = init && verifyMemberOf(init);
      if (picked && ts.isIdentifier(node.name))
        verifiers.set(node.name.text, picked);
    }
    ts.forEachChild(node, bindOnce);
  };
  // Twice, so an alias declared after the code that uses it elsewhere in the file is still seen.
  bindOnce(file);
  bindOnce(file);

  const calls: VerifyCall[] = [];
  const report = (node: ts.Node, key: string): void => {
    calls.push({
      line: file.getLineAndCharacterOfPosition(node.getStart()).line + 1,
      key: key.replace(/\s+/g, ' '),
    });
  };
  // The key argument of a verify call into `pkg`; `.call(thisArg, ...)` shifts it by one.
  const keyOf = (
    call: ts.CallExpression,
    pkg: string,
    viaCall: boolean,
  ): string =>
    call.arguments[(KEY_ARG[pkg] ?? 1) + (viaCall ? 1 : 0)]?.getText() ?? '';

  const visit = (node: ts.Node): void => {
    if (ts.isCallExpression(node)) {
      const callee = unwrap(node.expression);
      const direct =
        callee && ts.isIdentifier(callee)
          ? verifiers.get(callee.text)
          : undefined;
      if (direct) {
        report(node, keyOf(node, direct, false));
      } else if (callee && isMember(callee)) {
        const pkg = verifyMemberOf(callee);
        if (pkg) report(node, keyOf(node, pkg, false));
        // jwt.verify.call(null, t, k) / verify.call(null, t, k) / .apply(null, [t, k])
        const m = memberName(callee);
        const inner = unwrap(callee.expression);
        const innerPkg =
          inner &&
          (ts.isIdentifier(inner)
            ? verifiers.get(inner.text)
            : verifyMemberOf(inner));
        if ((m === 'call' || m === 'apply') && innerPkg) {
          report(node, m === 'call' ? keyOf(node, innerPkg, true) : '(apply)');
        }
      }
    }
    // A computed key on the module cannot be shown to be something other than verify.
    if (
      ts.isElementAccessExpression(node) &&
      !ts.isStringLiteralLike(node.argumentExpression)
    ) {
      if (moduleOf(node.expression)) report(node, '(computed member)');
    }
    // import('jsonwebtoken').then((m) => m.verify(...))
    if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      packageOf(node) &&
      ts.isPropertyAccessExpression(node.parent) &&
      node.parent.name.text === 'then'
    ) {
      report(node, '(dynamic import)');
    }
    // export { verify } from 'jsonwebtoken' / export * from 'jsonwebtoken'
    if (
      ts.isExportDeclaration(node) &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier) &&
      node.moduleSpecifier.text in VERIFY_NAMES
    ) {
      const names = VERIFY_NAMES[node.moduleSpecifier.text];
      const clause = node.exportClause;
      if (!clause || ts.isNamespaceExport(clause)) report(node, '(re-export)');
      else if (
        names &&
        clause.elements.some((el) =>
          names.has((el.propertyName ?? el.name).text),
        )
      ) {
        report(node, '(re-export)');
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return calls;
}

export function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(full);
    return entry.name.endsWith('.ts') ? [full] : [];
  });
}

/**
 * Every verify call under `dir` that the allowlist does not name. `allowed` maps a path
 * (relative to `base`) to the exact keys it may verify against, so adding a call to an
 * allowlisted file with another secret is reported too.
 */
export function unlistedVerifyCalls(
  dir: string,
  base: string,
  allowed: Record<string, readonly string[]>,
): string[] {
  const found: string[] = [];
  for (const file of sourceFiles(dir)) {
    const rel = path.relative(base, file).split(path.sep).join('/');
    const remaining = [...(allowed[rel] ?? [])];
    for (const call of findVerifyCalls(fs.readFileSync(file, 'utf8'))) {
      const i = remaining.indexOf(call.key);
      if (i === -1)
        found.push(
          `${rel}:${call.line} verifies with ${call.key || '(no key)'}`,
        );
      else remaining.splice(i, 1);
    }
  }
  return found;
}

const LIBRARY = /^(jsonwebtoken|jws|jose)(\/.*)?$/;

/**
 * Lines where a source file names a JWT library as a string (an import, a require, a
 * `createRequire` call, a dynamic import, `module.require`, an aliased `require`). Whatever
 * the spelling of the call that follows, a file that never names the library cannot verify
 * a token with it.
 */
export function findLibraryMentions(source: string): number[] {
  const file = ts.createSourceFile(
    'x.ts',
    source,
    ts.ScriptTarget.Latest,
    true,
  );
  const lines: number[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isStringLiteralLike(node) && LIBRARY.test(node.text)) {
      lines.push(file.getLineAndCharacterOfPosition(node.getStart()).line + 1);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return lines;
}

/**
 * Files under `dir` that name a JWT library but are neither in `mayNameLibrary` nor in
 * `verifiers` (the files already allowed to verify). `mayNameLibrary` holds the files that
 * only sign, decode or configure.
 */
export function unlistedLibraryMentions(
  dir: string,
  base: string,
  mayNameLibrary: readonly string[],
  verifiers: Record<string, readonly string[]>,
): string[] {
  const found: string[] = [];
  for (const file of sourceFiles(dir)) {
    const rel = path.relative(base, file).split(path.sep).join('/');
    if (mayNameLibrary.includes(rel) || rel in verifiers) continue;
    const lines = findLibraryMentions(fs.readFileSync(file, 'utf8'));
    if (lines.length) found.push(`${rel}:${lines[0]} names a JWT library`);
  }
  return found;
}

/** The local names a binding declares, including destructured ones (`{ a: b, c: [d] }`). */
function bindingNames(name: ts.BindingName): string[] {
  if (ts.isIdentifier(name)) return [name.text];
  return name.elements.flatMap((el) =>
    ts.isOmittedExpression(el) ? [] : bindingNames(el.name),
  );
}

/**
 * The names a source file exports: exported declarations, export lists (`export { a }`,
 * `export { a as b }`) and named re-exports (`export { a } from './x'`), by exported name.
 */
export function exportedNames(source: string): string[] {
  const file = ts.createSourceFile(
    'x.ts',
    source,
    ts.ScriptTarget.Latest,
    true,
  );
  const names: string[] = [];
  for (const st of file.statements) {
    if (ts.isExportDeclaration(st)) {
      if (st.exportClause && ts.isNamedExports(st.exportClause)) {
        for (const el of st.exportClause.elements) names.push(el.name.text);
      }
    } else if (
      ts.canHaveModifiers(st) &&
      ts.getModifiers(st)?.some((m) => m.kind === ts.SyntaxKind.ExportKeyword)
    ) {
      if (ts.isVariableStatement(st)) {
        for (const d of st.declarationList.declarations) {
          names.push(...bindingNames(d.name));
        }
      } else if (
        (ts.isFunctionDeclaration(st) || ts.isClassDeclaration(st)) &&
        st.name
      ) {
        names.push(st.name.text);
      }
    }
  }
  return names;
}
