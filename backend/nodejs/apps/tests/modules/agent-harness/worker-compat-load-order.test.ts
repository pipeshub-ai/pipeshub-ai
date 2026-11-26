/**
 * The sweeper's guarded features (DUR2) are module constants of worker-compat, built from run-spec's. A value
 * import closing a cycle through run-spec left them undefined in a process that loaded run-spec first, so a
 * stranded run kept `resolved_config` (CPD6-11 failed only in the full itest run). Each case is a fresh process.
 */
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { expect } from 'chai';

const APPS = path.resolve(__dirname, '../../..');
const DOMAIN = path.join(APPS, 'src/modules/agent-harness/domain');

const guardedAfterLoading = (first: string): unknown =>
  JSON.parse(
    execFileSync(
      process.execPath,
      [
        '-r',
        'ts-node/register',
        '-e',
        `require(${JSON.stringify(path.join(DOMAIN, first))});` +
          `process.stdout.write(JSON.stringify(require(${JSON.stringify(path.join(DOMAIN, 'worker-compat'))}).GUARDED_WORKER_FEATURES ?? null));`,
      ],
      {
        cwd: APPS,
        env: { ...process.env, TS_NODE_TRANSPILE_ONLY: '1', TS_NODE_PROJECT: 'tsconfig.test.json' },
        encoding: 'utf8',
      },
    ),
  );

describe('worker-compat: the guarded features whatever loads first', function () {
  this.timeout(60_000);

  for (const first of ['run-spec', 'execution', 'worker-compat']) {
    it(`${first} first: agent_code, resolved_config, run_surface, release_pin, deliverable_pin, output_locale and keyword_search_budget`, () => {
      expect(guardedAfterLoading(first)).to.deep.equal([
        'agent_code',
        'resolved_config',
        'run_surface',
        'release_pin',
        'deliverable_pin',
        'output_locale',
        'keyword_search_budget',
      ]);
    });
  }
});
