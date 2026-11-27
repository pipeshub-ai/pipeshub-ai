import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

/**
 * The store itests run with `mocha --no-config`, so the suite's harness-on helper
 * (tests/helpers/harness-switch-env-global.ts) never loads, and the harness is off unless
 * an install turns it on. A runner that left the switch unset ran the harness itests with
 * the harness off: no worker credential was provisioned and no `/review` was expanded.
 */
const APPS = join(__dirname, '../..');
const REPO = join(APPS, '../../..');

const runners: Array<[string, string]> = [
  ['package.json test:itest', (JSON.parse(readFileSync(join(APPS, 'package.json'), 'utf8')) as { scripts: Record<string, string> }).scripts['test:itest'] ?? ''],
  [
    'deployment/docker-compose/tests/agent_harness_replset_test.sh',
    readFileSync(join(REPO, 'deployment/docker-compose/tests/agent_harness_replset_test.sh'), 'utf8'),
  ],
];

describe('the store itest runners turn the harness on', () => {
  for (const [name, text] of runners) {
    it(`${name} sets AGENT_HARNESS_WORKER_ENABLED=true beside its --no-config mocha`, () => {
      expect(text).to.include('--no-config');
      expect(text).to.include('require-itest-stores.ts');
      expect(text).to.match(/(^|\s)AGENT_HARNESS_WORKER_ENABLED=true(\s|$)/m);
    });
  }
});
