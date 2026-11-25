import { expect } from 'chai';
import { spawnSync } from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import {
  always,
  armed,
  FailpointError,
  failpoint,
  failpointAsync,
  nth,
  parseArms,
  registry,
  writeArms,
} from './failpoints';

const POINT = 'node.admit.after_commit_before_response';
const APPS_ROOT = path.resolve(__dirname, '../../..');

const tmpFile = (): string =>
  path.join(
    fs.mkdtempSync(path.join(os.tmpdir(), 'failpoints-')),
    'failpoints.json',
  );

describe('harness test kit: failpoints', () => {
  afterEach(() => registry.reset());

  it('K3: raise fires exactly once at the named point', async () => {
    await armed({ [POINT]: { kind: 'raise' } }, () => {
      failpoint('node.other');
      expect(() => failpoint(POINT)).to.throw(FailpointError, POINT);
      failpoint(POINT);
      expect(registry.hits(POINT)).to.equal(2);
    });
    expect(registry.active).to.equal(false);
  });

  it('K3: unarmed points are no-ops and skip the registry', () => {
    const take = registry.take.bind(registry);
    let called = 0;
    registry.take = (name: string) => {
      called += 1;
      return take(name);
    };
    try {
      for (let i = 0; i < 1000; i++) failpoint(POINT);
    } finally {
      registry.take = take;
    }
    expect(called).to.equal(0);
  });

  it('nth and always triggers', async () => {
    await armed(
      { [POINT]: { action: { kind: 'raise' }, trigger: nth(2) } },
      () => {
        failpoint(POINT);
        expect(() => failpoint(POINT)).to.throw(FailpointError);
        failpoint(POINT);
      },
    );
    await armed(
      { [POINT]: { action: { kind: 'raise' }, trigger: always() } },
      () => {
        expect(() => failpoint(POINT)).to.throw(FailpointError);
        expect(() => failpoint(POINT)).to.throw(FailpointError);
      },
    );
    expect(() => nth(0)).to.throw('1-based');
  });

  it('custom error factory', async () => {
    await armed(
      { [POINT]: { kind: 'raise', error: () => new RangeError('boom') } },
      () => {
        expect(() => failpoint(POINT)).to.throw(RangeError, 'boom');
      },
    );
  });

  it('async delay yields to the event loop; sync delay blocks', async () => {
    const ticks: number[] = [];
    const ticker = async (): Promise<void> => {
      for (let i = 0; i < 3; i++) {
        ticks.push(i);
        await new Promise((r) => setTimeout(r, 5));
      }
    };
    await armed({ [POINT]: { kind: 'delay', ms: 40 } }, () =>
      Promise.all([failpointAsync(POINT), ticker()]),
    );
    expect(ticks).to.deep.equal([0, 1, 2]);

    await armed({ [POINT]: { kind: 'delay', ms: 30 } }, () => {
      const start = Date.now();
      failpoint(POINT);
      expect(Date.now() - start).to.be.at.least(25);
    });
  });

  it('file source round-trips and reloads on change', async () => {
    const file = tmpFile();
    writeArms(file, {
      [POINT]: { action: { kind: 'raise', message: 'boom' }, trigger: nth(2) },
    });
    registry.installFile(file);
    failpoint(POINT);
    expect(() => failpoint(POINT)).to.throw(FailpointError, 'boom');

    await new Promise((r) => setTimeout(r, 15));
    writeArms(file, { 'node.other': { kind: 'raise' } });
    failpoint(POINT);
    expect(() => failpoint('node.other')).to.throw(FailpointError);
  });

  it('rejects malformed documents and unserializable arms', () => {
    expect(() => parseArms([])).to.throw();
    expect(() => parseArms({ [POINT]: { action: 'explode' } })).to.throw(
      'unknown failpoint action',
    );
    expect(() =>
      parseArms({ [POINT]: { action: 'raise', trigger: 'twice' } }),
    ).to.throw('trigger');
    expect(() =>
      writeArms(tmpFile(), {
        [POINT]: { kind: 'raise', error: () => new Error() },
      }),
    ).to.throw('FailpointError');
  });

  describe('K4: kill9 in a child process', function () {
    this.timeout(60000);

    const runChild = (file: string) =>
      spawnSync(
        process.execPath,
        [
          '-r',
          'ts-node/register',
          path.join(__dirname, 'fixtures', 'kill9-child.ts'),
          file,
        ],
        {
          cwd: APPS_ROOT,
          encoding: 'utf-8',
          env: {
            ...process.env,
            TS_NODE_TRANSPILE_ONLY: 'true',
            TS_NODE_PROJECT: 'tsconfig.test.json',
          },
          timeout: 50000,
        },
      );

    it('terminates the child with SIGKILL exactly at the armed hit', () => {
      const file = tmpFile();
      writeArms(file, {
        [POINT]: { action: { kind: 'kill9' }, trigger: nth(2) },
      });
      const result = runChild(file);
      expect(result.signal, result.stderr).to.equal('SIGKILL');
      expect(result.stdout.trim().split('\n')).to.deep.equal([
        'before 1',
        'after 1',
        'before 2',
      ]);
    });

    it('an unarmed child runs to completion', () => {
      const result = runChild(tmpFile());
      expect(result.status, result.stderr).to.equal(0);
      expect(result.stdout.trim().split('\n').slice(-2)).to.deep.equal([
        'after 3',
        'finally',
      ]);
    });
  });
});
