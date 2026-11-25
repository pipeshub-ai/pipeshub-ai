/**
 * The test tree's type errors may only go down. Mocha runs the tests through
 * ts-node without type-checking them, so a test that calls an interface which
 * no longer exists fails only when run, or not at all on a path it does not
 * reach (CPD-6 FB-13: three files after one merge). The tree still carries
 * errors from before this gate, so the gate is their count: more than
 * `typecheck-baseline.json` fails; `--update` lowers the baseline and never
 * raises it.
 */
const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const BASELINE = path.join(__dirname, 'typecheck-baseline.json');

function count() {
  const tsc = require.resolve('typescript/bin/tsc');
  const run = spawnSync(
    process.execPath,
    [tsc, '--noEmit', '-p', 'tsconfig.test.json', '--pretty', 'false'],
    { cwd: path.join(__dirname, '..'), encoding: 'utf8', maxBuffer: 256 * 1024 * 1024 },
  );
  if (run.error) throw run.error;
  const lines = `${run.stdout}${run.stderr}`.split('\n').filter((line) => /: error TS\d+:/.test(line));
  // tsc exits non-zero without one error line only when it could not run at all.
  if (lines.length === 0 && run.status !== 0) throw new Error(`tsc failed: ${run.stdout}${run.stderr}`.slice(0, 2000));
  return lines;
}

function main(argv) {
  const baseline = JSON.parse(fs.readFileSync(BASELINE, 'utf8')).errors;
  const lines = count();
  if (argv.includes('--update')) {
    if (lines.length > baseline) {
      console.error(`${lines.length} type errors in the test tree, baseline ${baseline}: the baseline only goes down`);
      return 1;
    }
    fs.writeFileSync(BASELINE, `${JSON.stringify({ errors: lines.length }, null, 2)}\n`);
    console.log(`baseline ${baseline} -> ${lines.length}`);
    return 0;
  }
  if (lines.length > baseline) {
    console.error(lines.join('\n'));
    console.error(
      `\n${lines.length} type errors in the test tree, baseline ${baseline}: ${lines.length - baseline} new. ` +
        'Fix them (the list above is every error; compare with the base branch to find the new ones).',
    );
    return 1;
  }
  console.log(
    lines.length < baseline
      ? `${lines.length} type errors in the test tree, baseline ${baseline}: lower it with \`npm run typecheck:tests -- --update\``
      : `${lines.length} type errors in the test tree, at the baseline`,
  );
  return 0;
}

// process.exit would cut off a piped stderr before the error list and summary flush (the CI log lost both).
process.exitCode = main(process.argv.slice(2));
