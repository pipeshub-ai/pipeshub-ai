/**
 * Architecture guard (Phase 0): no feature code constructs a Redis client
 * directly.
 *
 * Every Redis-backed feature must depend on `IRedisConnectionProvider`
 * (obtained via `getRedisProvider` / `RedisConnectionProviderFactory`),
 * never `ioredis`'s `Redis` / `Cluster` directly. That is what lets a
 * separate EE repo add AWS MemoryDB support by registering one provider
 * class -- no other file has to change.
 *
 * This mirrors
 * `backend/python/tests/unit/services/redis/test_architecture_guard.py`;
 * both must stay behaviourally aligned.
 */
import { expect } from 'chai';
import * as fs from 'fs';
import * as path from 'path';
import * as ts from 'typescript';

const SRC_ROOT = path.resolve(__dirname, '../../../../src');

// Only the connection-provider implementations themselves may import the
// `ioredis` client classes directly; everything else must go through
// `IRedisConnectionProvider`. Paths are relative to `src/`.
const ALLOWED_DIRECT_CLIENT_IMPORT_FILES = new Set([
  'libs/services/redis/standaloneRedisProvider.ts',
  'libs/services/redis/clusterRedisProvider.ts',
]);

function isIoredisImport(moduleSpecifier: string): boolean {
  return moduleSpecifier === 'ioredis';
}

function* walk(dir: string): Generator<string> {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      yield* walk(full);
    } else if (entry.isFile() && /\.tsx?$/.test(entry.name)) {
      yield full;
    }
  }
}

function findRestrictedImports(sourceFile: ts.SourceFile): number[] {
  const lines: number[] = [];

  function visit(node: ts.Node): void {
    if (
      ts.isImportDeclaration(node) &&
      ts.isStringLiteral(node.moduleSpecifier) &&
      isIoredisImport(node.moduleSpecifier.text) &&
      !node.importClause?.isTypeOnly
    ) {
      const { line } = sourceFile.getLineAndCharacterOfPosition(node.getStart());
      lines.push(line + 1);
    }
    if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      node.arguments.length > 0 &&
      ts.isStringLiteral(node.arguments[0]) &&
      isIoredisImport(node.arguments[0].text)
    ) {
      const { line } = sourceFile.getLineAndCharacterOfPosition(node.getStart());
      lines.push(line + 1);
    }
    if (
      ts.isCallExpression(node) &&
      ts.isIdentifier(node.expression) &&
      node.expression.text === 'require' &&
      node.arguments.length > 0 &&
      ts.isStringLiteral(node.arguments[0]) &&
      isIoredisImport(node.arguments[0].text)
    ) {
      const { line } = sourceFile.getLineAndCharacterOfPosition(node.getStart());
      lines.push(line + 1);
    }
    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
  return lines;
}

describe('Architecture guard: no direct ioredis client imports outside providers', () => {
  it('only the provider implementations import ioredis directly', () => {
    const offenders: string[] = [];

    for (const filePath of walk(SRC_ROOT)) {
      const rel = path.relative(SRC_ROOT, filePath).split(path.sep).join('/');
      if (ALLOWED_DIRECT_CLIENT_IMPORT_FILES.has(rel)) {
        continue;
      }
      const text = fs.readFileSync(filePath, 'utf8');
      if (!text.includes('ioredis')) {
        continue;
      }
      const sourceFile = ts.createSourceFile(
        filePath,
        text,
        ts.ScriptTarget.Latest,
        true,
        rel.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
      );
      for (const line of findRestrictedImports(sourceFile)) {
        offenders.push(`${rel}:${line} imports 'ioredis'`);
      }
    }

    expect(
      offenders,
      'Found direct ioredis imports outside the connection-provider ' +
        'allow-list. Route through getRedisProvider() instead:\n' +
        offenders.join('\n'),
    ).to.have.lengthOf(0);
  });

  it('the allow-list files still exist', () => {
    for (const rel of ALLOWED_DIRECT_CLIENT_IMPORT_FILES) {
      expect(fs.existsSync(path.join(SRC_ROOT, rel)), `${rel} no longer exists`).to
        .equal(true);
    }
  });
});

// Node-only (no Python mirror): the Python factory discovers providers
// lazily inside create(), but Node's create() is synchronous while module
// loading is not, so each *process* must import the edition switch up front.
// The all-in-one image runs the API and the Slack bot as separate processes;
// a registration in one is invisible to the other.
const REDIS_EDITION_SWITCH = 'redisProviders';

function processEntryPoints(): string[] {
  const entries = ['app.ts'];
  const integrationsDir = path.join(SRC_ROOT, 'integrations');
  if (!fs.existsSync(integrationsDir)) {
    return entries;
  }
  for (const entry of fs.readdirSync(integrationsDir, { withFileTypes: true })) {
    const rel = `integrations/${entry.name}/src/index.ts`;
    if (entry.isDirectory() && fs.existsSync(path.join(SRC_ROOT, rel))) {
      entries.push(rel);
    }
  }
  return entries;
}

function importsRedisEditionSwitch(filePath: string): boolean {
  const target = path.join(SRC_ROOT, REDIS_EDITION_SWITCH);
  const sourceFile = ts.createSourceFile(
    filePath,
    fs.readFileSync(filePath, 'utf8'),
    ts.ScriptTarget.Latest,
    true,
  );
  return sourceFile.statements.some(
    (node) =>
      ts.isImportDeclaration(node) &&
      ts.isStringLiteral(node.moduleSpecifier) &&
      path.resolve(path.dirname(filePath), node.moduleSpecifier.text) === target,
  );
}

describe('Architecture guard: every process entry point imports the Redis edition switch', () => {
  it(`src/${REDIS_EDITION_SWITCH}.ts exists`, () => {
    expect(fs.existsSync(path.join(SRC_ROOT, `${REDIS_EDITION_SWITCH}.ts`))).to.equal(true);
  });

  it('app.ts and every integrations/*/src/index.ts import it', () => {
    const missing = processEntryPoints().filter(
      (rel) => !importsRedisEditionSwitch(path.join(SRC_ROOT, rel)),
    );
    expect(
      missing,
      `These process entry points never import src/${REDIS_EDITION_SWITCH}, so an ` +
        'EE REDIS_MODE is unknown in that process:\n' +
        missing.join('\n'),
    ).to.have.lengthOf(0);
  });
});
