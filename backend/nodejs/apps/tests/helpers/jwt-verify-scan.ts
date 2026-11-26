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
  const modules = new Map<string, ReadonlySet<string>>(); // local name -> names that verify
  const verifiers = new Set<string>(); // local names bound to a verify function

  const packageOf = (n: ts.Node | undefined): string | undefined => {
    if (
      !n ||
      !ts.isCallExpression(n) ||
      n.arguments.length !== 1 ||
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
  // The verify-names of the module an expression refers to, if it refers to one.
  const moduleOf = (
    n: ts.Expression | undefined,
  ): ReadonlySet<string> | undefined => {
    const e = unwrap(n);
    if (!e) return undefined;
    if (ts.isIdentifier(e)) return modules.get(e.text);
    const pkg = packageOf(e);
    return pkg ? VERIFY_NAMES[pkg] : undefined;
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
        if (clause.name) modules.set(clause.name.text, names);
        const nb = clause.namedBindings;
        if (nb && ts.isNamespaceImport(nb)) modules.set(nb.name.text, names);
        if (nb && ts.isNamedImports(nb)) {
          for (const el of nb.elements) {
            if (names.has((el.propertyName ?? el.name).text))
              verifiers.add(el.name.text);
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
      modules.set(
        node.name.text,
        VERIFY_NAMES[node.moduleReference.expression.text],
      );
    }
    if (ts.isVariableDeclaration(node) && node.initializer) {
      const init = unwrap(node.initializer);
      const names = moduleOf(init);
      if (names && ts.isIdentifier(node.name))
        modules.set(node.name.text, names);
      if (names && ts.isObjectBindingPattern(node.name)) {
        for (const el of node.name.elements) {
          const original =
            el.propertyName && ts.isIdentifier(el.propertyName)
              ? el.propertyName.text
              : undefined;
          const local = ts.isIdentifier(el.name) ? el.name.text : '';
          if (names.has(original ?? local) && local) verifiers.add(local);
        }
      }
      // const v = jwt.verify
      if (init && isMember(init) && ts.isIdentifier(node.name)) {
        const names2 = moduleOf(init.expression);
        const m = memberName(init);
        if (names2 && m && names2.has(m)) verifiers.add(node.name.text);
      }
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
  // `jwt.verify(t, k)` and `jwt.verify.call(null, t, k)` both carry the key one argument after the token.
  const keyOf = (call: ts.CallExpression, viaCall: boolean): string =>
    call.arguments[viaCall ? 2 : 1]?.getText() ?? '';

  const visit = (node: ts.Node): void => {
    if (ts.isCallExpression(node)) {
      const callee = unwrap(node.expression);
      if (callee && ts.isIdentifier(callee) && verifiers.has(callee.text)) {
        report(node, keyOf(node, false));
      } else if (callee && isMember(callee)) {
        const names = moduleOf(callee.expression);
        const m = memberName(callee);
        if (names && m && names.has(m)) report(node, keyOf(node, false));
        // jwt.verify.call(null, t, k) / .apply(null, [t, k])
        const inner = unwrap(callee.expression);
        if (
          (m === 'call' || m === 'apply') &&
          inner &&
          isMember(inner) &&
          (() => {
            const n2 = moduleOf(inner.expression);
            const m2 = memberName(inner);
            return !!n2 && !!m2 && n2.has(m2);
          })()
        ) {
          report(node, m === 'call' ? keyOf(node, true) : '(apply)');
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
