import { expect } from 'chai';
import * as fs from 'fs';
import * as path from 'path';
import * as ts from 'typescript';
import { checkStartup } from '../../../src/edition';

const SRC_ROOT = path.resolve(__dirname, '../../../src');

// The all-in-one image runs the API and the Slack bot as separate processes from one .env.
function processEntryPoints(): string[] {
  const integrations = path.join(SRC_ROOT, 'integrations');
  return [
    'index.ts',
    ...fs
      .readdirSync(integrations, { withFileTypes: true })
      .map((entry) => `integrations/${entry.name}/src/index.ts`)
      .filter((rel) => fs.existsSync(path.join(SRC_ROOT, rel))),
  ];
}

const calls = (statement: ts.Statement | undefined, name: string): boolean =>
  statement !== undefined &&
  ts.isExpressionStatement(statement) &&
  ts.isCallExpression(statement.expression) &&
  ts.isIdentifier(statement.expression.expression) &&
  statement.expression.expression.text === name;

describe('start-up settings check', () => {
  it('refuses nothing by default', () => {
    expect(() => checkStartup()).to.not.throw();
  });

  it('runs in every process entry point right after the store settings are filled in', () => {
    const entries = processEntryPoints();
    expect(entries).to.include('integrations/slack-bot/src/index.ts');
    const missing = entries.filter((rel) => {
      const file = path.join(SRC_ROOT, rel);
      const { statements } = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true);
      const umbrella = statements.findIndex((s) => calls(s, 'applyStoreUmbrella'));
      return umbrella === -1 || !calls(statements[umbrella + 1], 'checkStartup');
    });
    expect(missing, `entry points that do not call checkStartup() after applyStoreUmbrella(): ${missing.join(', ')}`).to.deep.equal([]);
  });
});
