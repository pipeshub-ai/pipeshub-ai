import { expect } from 'chai';
import * as fs from 'fs';
import * as path from 'path';

// A metric label is exported with every sample, so it must never name who the
// series belongs to: an email address, or the domain part of one.
const FORBIDDEN = /domain|email/i;
const SRC = path.resolve(__dirname, '../../../../src');
const BACKEND = path.join(SRC, 'libs/services/telemetry/metrics-backend.ts');

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return sourceFiles(full);
    return e.name.endsWith('.ts') ? [full] : [];
  });
}

describe('metric label guard', () => {
  const files = sourceFiles(SRC).filter((f) => f !== BACKEND);

  it('declares every metric with a literal label list', () => {
    const notLiteral = files.flatMap((f) =>
      [...fs.readFileSync(f, 'utf8').matchAll(/labelNames:\s*([^\s[])/g)].map(
        () => path.relative(SRC, f),
      ),
    );
    expect(notLiteral).to.deep.equal([]);
  });

  it('gives no metric a domain or email label', () => {
    const lists = files.flatMap((f) =>
      [
        ...fs.readFileSync(f, 'utf8').matchAll(/labelNames:\s*\[([^\]]*)\]/g),
      ].map((m) => ({
        file: path.relative(SRC, f),
        labels: [...(m[1] ?? '').matchAll(/['"`]([^'"`]+)['"`]/g)].map(
          (l) => l[1] as string,
        ),
      })),
    );
    expect(lists.length).to.be.greaterThan(5);
    const offending = lists.filter((l) =>
      l.labels.some((n) => FORBIDDEN.test(n)),
    );
    expect(offending).to.deep.equal([]);
  });
});
