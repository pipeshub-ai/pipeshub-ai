import { expect } from 'chai';
import { execFileSync } from 'node:child_process';
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const APP = join(__dirname, '..', '..');
const SCRIPT = join(APP, 'scripts', 'remove-plugin-deps.mjs');

interface Result {
  removedPlugins: string[];
  removedPackages: string[];
  removedFromLock: string[];
}

describe('scripts/remove-plugin-deps.mjs', () => {
  let root: string;
  const file = (name: string): string => join(root, name);
  const json = (name: string): any => JSON.parse(readFileSync(file(name), 'utf8'));
  const run = (...args: string[]): Result =>
    JSON.parse(
      execFileSync(
        process.execPath,
        [SCRIPT, '--src', file('src'), ...args],
        { stdio: 'pipe', encoding: 'utf8' },
      ),
    );
  const plugin = (name: string): void => {
    mkdirSync(join(root, 'src', 'plugins', name), { recursive: true });
  };

  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), 'pipeshub-plugin-deps-'));
    mkdirSync(file('src'), { recursive: true });
    writeFileSync(
      file('src/plugins.dependencies.json'),
      JSON.stringify({
        pluginDependencies: {
          meter: ['meter-lib', 'shared-lib'],
          other: ['shared-lib'],
        },
      }),
    );
    writeFileSync(
      file('package.json'),
      `${JSON.stringify(
        {
          name: 'app',
          dependencies: { express: '^5', 'meter-lib': '^1', 'shared-lib': '^2' },
          devDependencies: { mocha: '^11' },
        },
        null,
        4,
      )}\n`,
    );
    writeFileSync(
      file('package-lock.json'),
      `${JSON.stringify(
        {
          name: 'app',
          lockfileVersion: 3,
          packages: {
            '': {
              name: 'app',
              dependencies: { express: '^5', 'meter-lib': '^1', 'shared-lib': '^2' },
              devDependencies: { mocha: '^11' },
            },
            'node_modules/express': { version: '5.0.0', dependencies: { debug: '^4' } },
            // also needed by express: must survive
            'node_modules/debug': { version: '4.0.0' },
            'node_modules/meter-lib': {
              version: '1.0.0',
              dependencies: { debug: '^4', 'only-meter': '^1', nested: '^2' },
            },
            'node_modules/only-meter': { version: '1.0.0' },
            // meter-lib's own copy, and what only that copy needs
            'node_modules/meter-lib/node_modules/nested': {
              version: '2.0.0',
              dependencies: { leaf: '^1' },
            },
            'node_modules/leaf': { version: '1.0.0' },
            'node_modules/nested': { version: '1.0.0' },
            'node_modules/mocha': { version: '11.0.0', dependencies: { nested: '^1' } },
            'node_modules/shared-lib': { version: '2.0.0' },
            // nothing reaches it today: not this script's to remove
            'node_modules/stray': { version: '0.0.1' },
          },
        },
        null,
        2,
      )}\n`,
    );
  });
  afterEach(() => rmSync(root, { recursive: true, force: true }));

  it('changes nothing while every plug-in directory is there', () => {
    plugin('meter');
    plugin('other');
    const before = readFileSync(file('package.json'), 'utf8');
    expect(run()).to.deep.equal({
      removedPlugins: [],
      removedPackages: [],
      removedFromLock: [],
    });
    expect(readFileSync(file('package.json'), 'utf8')).to.equal(before);
  });

  it('removes the packages of a plug-in whose directory is gone, keeping one a remaining plug-in lists', () => {
    plugin('other');
    const result = run();
    expect(result.removedPlugins).to.deep.equal(['meter']);
    expect(result.removedPackages).to.deep.equal(['meter-lib']);
    expect(json('package.json').dependencies).to.deep.equal({
      express: '^5',
      'shared-lib': '^2',
    });
    expect(json('package.json').devDependencies).to.deep.equal({ mocha: '^11' });
  });

  it('drops from the lockfile exactly what only the removed package brought in', () => {
    plugin('other');
    const result = run();
    expect(result.removedFromLock).to.deep.equal([
      'node_modules/leaf',
      'node_modules/meter-lib',
      'node_modules/meter-lib/node_modules/nested',
      'node_modules/only-meter',
    ]);
    const lock = json('package-lock.json');
    expect(Object.keys(lock.packages).sort()).to.deep.equal([
      '',
      'node_modules/debug',
      'node_modules/express',
      'node_modules/mocha',
      'node_modules/nested',
      'node_modules/shared-lib',
      'node_modules/stray',
    ]);
    expect(lock.packages[''].dependencies).to.deep.equal({
      express: '^5',
      'shared-lib': '^2',
    });
  });

  it('removes a shared package once every plug-in that lists it is gone', () => {
    const result = run();
    expect(result.removedPlugins).to.deep.equal(['meter', 'other']);
    expect(result.removedPackages).to.deep.equal(['meter-lib', 'shared-lib']);
    expect(json('package-lock.json').packages).not.to.have.property(
      'node_modules/shared-lib',
    );
  });

  it('keeps the indentation and the final newline of the files it rewrites', () => {
    plugin('other');
    run();
    const text = readFileSync(file('package.json'), 'utf8');
    expect(text).to.match(/^\{\n {4}"name"/);
    expect(text.endsWith('}\n')).to.equal(true);
  });

  it('--remove names plug-ins that are still in the tree, and refuses an unknown one', () => {
    plugin('meter');
    plugin('other');
    expect(run('--remove', 'meter').removedPackages).to.deep.equal(['meter-lib']);
    expect(() => run('--remove', 'nope')).to.throw(/not in pluginDependencies/);
  });

  it('--dry-run reports and writes nothing', () => {
    const before = readFileSync(file('package-lock.json'), 'utf8');
    expect(run('--dry-run').removedPackages).to.deep.equal([
      'meter-lib',
      'shared-lib',
    ]);
    expect(readFileSync(file('package-lock.json'), 'utf8')).to.equal(before);
    expect(json('package.json').dependencies).to.have.property('meter-lib');
  });

  it('works without a lockfile and refuses a malformed map', () => {
    rmSync(file('package-lock.json'));
    expect(run().removedFromLock).to.deep.equal([]);
    writeFileSync(
      file('src/plugins.dependencies.json'),
      JSON.stringify({ pluginDependencies: { meter: 'meter-lib' } }),
    );
    expect(() => run()).to.throw(/list of package names/);
  });

  describe('the checked-in map', () => {
    const map: Record<string, string[]> = JSON.parse(
      readFileSync(join(APP, 'src', 'plugins.dependencies.json'), 'utf8'),
    ).pluginDependencies;
    const declared = JSON.parse(readFileSync(join(APP, 'package.json'), 'utf8'));

    it('gives the usage telemetry plug-in its Prometheus client', () => {
      expect(map.telemetry).to.include('prom-client');
    });

    it('names only packages package.json declares', () => {
      for (const name of Object.values(map).flat()) {
        expect(declared.dependencies, name).to.have.property(name);
      }
    });

    it('leaves no Prometheus or OpenTelemetry package in the lockfile once telemetry is removed', () => {
      const result = run(
        '--dry-run',
        '--remove',
        'telemetry',
        '--src',
        join(APP, 'src'),
      );
      expect(result.removedPackages).to.deep.equal(['prom-client']);
      expect(result.removedFromLock).to.include('node_modules/prom-client');
      const lock = JSON.parse(
        readFileSync(join(APP, 'package-lock.json'), 'utf8'),
      ).packages;
      const left = Object.keys(lock).filter(
        (path) =>
          !result.removedFromLock.includes(path) &&
          /node_modules\/(prom-client|@opentelemetry\/[^/]+)$/.test(path),
      );
      expect(left).to.deep.equal([]);
    });
  });
});
