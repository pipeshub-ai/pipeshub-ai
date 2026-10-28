import { expect } from 'chai';
import fs from 'fs';
import path from 'path';

/**
 * Every copy of a package with a fixed advisory stays at or above the fixed release.
 *
 * Overrides in package.json pin some of these exactly, so a transitive bump cannot
 * raise them, and Dependabot cannot either. The lockfile is what gets installed.
 */
const SECURITY_FLOORS: Record<string, string> = {
  // GHSA-6pq8-jhm2-2vcw
  qs: '6.16.1',
};

const lockfile = JSON.parse(
  fs.readFileSync(path.join(__dirname, '..', 'package-lock.json'), 'utf8'),
) as { packages: Record<string, { version?: string }> };

function release(version: string): number[] {
  return version.split('-')[0]!.split('.').map(Number);
}

function atLeast(version: string, floor: string): boolean {
  const [a, b] = [release(version), release(floor)];
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    const [x, y] = [a[i] ?? 0, b[i] ?? 0];
    if (x !== y) return x > y;
  }
  return true;
}

function installedCopies(name: string): Array<[string, string]> {
  return Object.entries(lockfile.packages)
    .filter(([key]) => key === `node_modules/${name}` || key.endsWith(`/node_modules/${name}`))
    .map(([key, entry]) => [key, entry.version ?? '']);
}

describe('dependency security floors', () => {
  for (const [name, floor] of Object.entries(SECURITY_FLOORS)) {
    it(`installs ${name} at ${floor} or later everywhere`, () => {
      const copies = installedCopies(name);
      expect(copies, `${name} is not in package-lock.json`).to.not.be.empty;
      const below = copies.filter(([, version]) => !atLeast(version, floor));
      expect(below, `copies of ${name} below ${floor}`).to.deep.equal([]);
    });
  }

  it('compares release numbers, not strings', () => {
    expect(atLeast('6.16.10', '6.16.1')).to.equal(true);
    expect(atLeast('6.9.0', '6.16.1')).to.equal(false);
    expect(atLeast('6.16.1', '6.16.1')).to.equal(true);
  });
});
