import { describe, expect, it } from 'vitest';
import { commandTitle } from '../command-titles';
import { words } from './fixtures';

/** A command a job ran, as Activity names it: what it did, from what the command says, never more. */
describe('command titles', () => {
  it.each([
    ['git clone https://github.com/acme/payments.git', 'Copied the payments repository'],
    ['git clone git@github.com:acme/payments', 'Copied the payments repository'],
    ['cd payments && git clone https://github.com/acme/web-app', 'Copied the web-app repository'],
    ['git status', 'Checked the changes'],
    ['git diff HEAD~1', 'Checked the changes'],
    ['git commit -m "fix rounding"', 'Saved a commit'],
    ['git push origin fix/rounding', 'Pushed the changes'],
    ['git checkout -b fix/rounding', 'Switched branch'],
    ['pytest tests/test_refunds.py -q', 'Ran the refund tests'],
    ['cd payments && python -m pytest', 'Ran the tests'],
    ['npm test', 'Ran the tests'],
    ['npx vitest run src/totals.test.ts', 'Ran the total tests'],
    ['go test ./...', 'Ran the tests'],
    ['cargo test', 'Ran the tests'],
    ['npm install', 'Installed packages'],
    ['npm ci', 'Installed packages'],
    ['pnpm add zod', 'Installed packages'],
    ['pip install -r requirements.txt', 'Installed packages'],
    ['uv pip install requests', 'Installed packages'],
    ['npm run build', 'Built the project'],
    ['make', 'Built the project'],
    ['cargo build --release', 'Built the project'],
    ['tsc --noEmit', 'Checked the types'],
    ['ls -la', 'Looked at files'],
    ['cat src/refunds/total.py', 'Looked at files'],
    ['head -n 20 out/q3.csv', 'Looked at files'],
    ['grep -rn round src', 'Searched the files'],
    ['rg "round" src', 'Searched the files'],
    ['mkdir -p out', 'Made a folder'],
    ['python report.py', 'Ran report.py'],
    ['node scripts/build-index.js', 'Ran build-index.js'],
  ])('%s', (command, title) => {
    expect(commandTitle({ command, status: 'completed' }, words).title).toBe(title);
  });

  it('keeps "Ran a command" for anything it does not know, rather than guess', () => {
    expect(commandTitle({ command: 'frobnicate --all', status: 'completed' }, words).title).toBe('Ran a command');
    expect(commandTitle({ command: 'curl https://x.y', status: 'completed' }, words).title).toBe('Ran a command');
    expect(commandTitle({ command: '', status: 'completed' }, words).title).toBe('Ran a command');
  });

  it('says it while it runs', () => {
    expect(commandTitle({ command: 'git clone https://github.com/acme/payments', status: 'running' }, words).title).toBe('Copying the payments repository');
    expect(commandTitle({ command: 'pytest', status: 'running' }, words).title).toBe('Running the tests');
    expect(commandTitle({ command: 'frobnicate', status: 'running' }, words).title).toBe('Running a command');
  });

  it('a test run says how it came out, from its output or its exit code', () => {
    const run = (output: string, exitCode: number) => commandTitle({ command: 'pytest tests/test_refunds.py', status: exitCode === 0 ? 'completed' : 'failed', exitCode, output }, words).result;
    expect(run('F.\n1 failed, 1 passed in 0.42s', 1)).toBe('1 failed');
    expect(run('3 failed, 9 passed', 1)).toBe('3 failed');
    expect(run('..\n2 passed in 0.39s', 0)).toBe('All passed');
    expect(run('Tests: 2 failed, 5 passed, 7 total', 1)).toBe('2 failed');
    expect(run('', 1)).toBe('Some failed');
    expect(commandTitle({ command: 'pytest', status: 'running' }, words).result).toBeUndefined();
    expect(commandTitle({ command: 'npm install', status: 'completed', exitCode: 0 }, words).result).toBeUndefined();
  });
});
