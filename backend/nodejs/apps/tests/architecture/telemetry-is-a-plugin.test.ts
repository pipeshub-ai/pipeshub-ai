import { expect } from 'chai';
import { execFileSync, spawnSync } from 'child_process';
import {
  cpSync,
  existsSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  realpathSync,
  rmSync,
  symlinkSync,
} from 'fs';
import { tmpdir } from 'os';
import { join, relative, sep } from 'path';

const APP = join(__dirname, '..', '..');
const SRC = join(APP, 'src');
const GENERATOR = join(APP, 'scripts', 'generate-plugins.mjs');
const TSC = join(APP, 'node_modules', 'typescript', 'bin', 'tsc');

/** The only places in `src` that may name telemetry. */
const PLUGIN_DIR = 'plugins/telemetry/';
const PLUGIN_LIST = 'plugins.generated.ts';

/**
 * A build that ships more plug-ins than `src/plugins` describes them in
 * plugins.profiles.json (see scripts/generate-plugins.mjs). Without that file
 * the telemetry plug-in is the only optional part.
 */
interface BuildProfiles {
  profiles?: Record<string, string[]>;
  builds?: Record<string, string[]>;
  plugins?: Record<string, { owns?: string[] }>;
  /** More lines allowed in a tree built without any plug-in, relative to this file's directory. */
  strippedTreeAllowList?: string;
}
const PROFILES_FILE = join(APP, 'plugins.profiles.json');
const BUILD_PROFILES: BuildProfiles = existsSync(PROFILES_FILE)
  ? (JSON.parse(readFileSync(PROFILES_FILE, 'utf8')) as BuildProfiles)
  : {};

/** Every optional directory under `src`: the plug-ins and what exists only for one. */
const OPTIONAL_DIRS: string[] = [
  ...new Set([
    PLUGIN_DIR.slice(0, -1),
    ...Object.values(BUILD_PROFILES.profiles ?? {}).flat(),
    ...Object.values(BUILD_PROFILES.builds ?? {}).flat(),
    ...Object.entries(BUILD_PROFILES.plugins ?? {}).flatMap(([dir, info]) => [
      dir,
      ...(info.owns ?? []),
    ]),
  ]),
];
const inOptionalDir = (name: string): boolean =>
  OPTIONAL_DIRS.some((dir) => name.startsWith(`${dir}/`));

/** Plug-ins a build switches on only together with telemetry may import it. */
const WITH_TELEMETRY: string[] = [
  PLUGIN_DIR.slice(0, -1),
  ...Object.values(BUILD_PROFILES.builds ?? {})
    .filter((dirs) => dirs.includes(PLUGIN_DIR.slice(0, -1)))
    .flat(),
];

/**
 * Lines that still say "telemetry" in a tree built without the plug-in. Each is
 * product documentation, not code, and each is here for a stated reason. A new
 * hit fails the test: either remove the word or justify the line here.
 */
const ALLOWED_IN_STRIPPED_TREE: { file: string; text: string; why: string }[] =
  [
    {
      file: 'modules/api-docs/pipeshub-openapi.yaml',
      text: 'Configure telemetry and metrics collection for application monitoring and analytics.',
      why: 'Public API reference: describes the /configurationManager/metricsCollection routes, which stay (they store the Data Collection setting) in every build.',
    },
    {
      file: 'modules/api-docs/pipeshub-openapi.yaml',
      text: 'Configuration for application telemetry and metrics collection.',
      why: 'Public API reference: description of the MetricsCollectionConfig schema those routes return.',
    },
    {
      file: 'modules/api-docs/pipeshub-openapi.yaml',
      text: 'description: Optional telemetry captured alongside the feedback',
      why: 'Public API reference for message feedback: a field the client may send with a rating. Unrelated to usage collection.',
    },
    {
      file: 'modules/api-docs/pipeshub-openapi.yaml',
      text: 'Telemetry recorded server-side alongside the feedback. Always present',
      why: 'Public API reference for message feedback: a field stored with the rating in the conversation. Unrelated to usage collection.',
    },
  ];

/**
 * The agent harness's tracing and its event contracts stay in every build: its spans' ids shared with
 * `harness_core.telemetry`, its OTLP exporter, and the `x.pipeshub.telemetry.recorded` run event. None of it is usage
 * collection or goes away with the plug-in.
 */
const HARNESS_TRACING_FILES = [
  'modules/agent-harness/contracts/generated/',
  'modules/agent-harness/adapters/observability/otlp-http-tracer.ts',
  'modules/agent-harness/application/harness-tracing.ts',
];
const harnessTracing = (hit: { file: string; text: string }): boolean =>
  HARNESS_TRACING_FILES.some((prefix) => hit.file.startsWith(prefix)) ||
  hit.text.includes('x.pipeshub.telemetry.recorded') ||
  (hit.file === 'modules/agent-harness/projection/run-projector.ts' && hit.text.includes('TELEMETRY_RECORDED')) ||
  hit.text.includes('harness_core.telemetry');

/**
 * The process's own saturation for the autoscaler (PERF-E): a registry of its own, served whatever the usage-data
 * switch says, so it stays in a build without the plug-in.
 */
const PROM_CLIENT_OUTSIDE_PLUGINS = ['libs/services/process-metrics.ts'];

/** Lines an edition's own code and API reference still need, each with its reason. */
const ALSO_ALLOWED: { file: string; text?: string; why: string }[] =
  BUILD_PROFILES.strippedTreeAllowList === undefined
    ? []
    : (
        JSON.parse(
          readFileSync(
            join(APP, BUILD_PROFILES.strippedTreeAllowList),
            'utf8',
          ),
        ) as { allow: { file: string; text?: string; why: string }[] }
      ).allow;

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (entry.isFile()) out.push(full);
  }
  return out;
}

const rel = (root: string, file: string): string =>
  relative(root, file).split(sep).join('/');

/** Every module specifier a TypeScript file imports, re-exports or requires. */
function importPaths(source: string): string[] {
  const found: string[] = [];
  const patterns = [
    /\bfrom\s*['"]([^'"]+)['"]/g,
    /\bimport\s*['"]([^'"]+)['"]/g,
    /\b(?:import|require)\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
  ];
  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) found.push(match[1]!);
  }
  return found;
}

function telemetryHits(root: string): { file: string; text: string }[] {
  const hits: { file: string; text: string }[] = [];
  for (const file of walk(root)) {
    const name = rel(root, file);
    if (/telemetry/i.test(name)) hits.push({ file: name, text: '(file name)' });
    const lines = readFileSync(file, 'utf8').split('\n');
    for (const line of lines) {
      if (/telemetry/i.test(line)) hits.push({ file: name, text: line.trim() });
    }
  }
  return hits;
}

/** Type errors of the project in `dir`, with paths relative to it. */
function tscErrors(dir: string): string[] {
  const run = spawnSync(process.execPath, [TSC, '--noEmit', '-p', '.'], {
    cwd: dir,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  });
  return `${run.stdout}${run.stderr}`
    .split('\n')
    .filter((line) => /error TS\d+/.test(line))
    .map((line) => line.replace(/\(\d+,\d+\)/, ''));
}

describe('architecture: telemetry is a plug-in', () => {
  describe('the source tree', () => {
    it('imports telemetry only from the plug-in itself and the generated plug-in list', () => {
      const offenders: string[] = [];
      for (const file of walk(SRC)) {
        const name = rel(SRC, file);
        if (!name.endsWith('.ts')) continue;
        if (name === PLUGIN_LIST) continue;
        if (WITH_TELEMETRY.some((dir) => name.startsWith(`${dir}/`))) continue;
        // The harness's generated event contracts name its trace event, not the plug-in.
        if (name.startsWith(HARNESS_TRACING_FILES[0]!)) continue;
        for (const path of importPaths(readFileSync(file, 'utf8'))) {
          // The usage telemetry package only; a module merely named *opentelemetry* is not it.
          if (/(?<!open)telemetry/i.test(path)) offenders.push(`${name} -> ${path}`);
        }
      }
      expect(offenders).to.deep.equal([]);
    });

    it('imports a plug-in, or what exists only for one, from nowhere but plug-ins and the generated list', () => {
      const offenders: string[] = [];
      for (const file of walk(SRC)) {
        const name = rel(SRC, file);
        if (!name.endsWith('.ts')) continue;
        if (name === PLUGIN_LIST || inOptionalDir(name)) continue;
        for (const path of importPaths(readFileSync(file, 'utf8'))) {
          if (!path.startsWith('.')) continue;
          const target = rel(SRC, join(file, '..', path));
          if (OPTIONAL_DIRS.some((dir) => target === dir || target.startsWith(`${dir}/`))) {
            offenders.push(`${name} -> ${path}`);
          }
        }
      }
      expect(offenders).to.deep.equal([]);
    });

    it('imports a Prometheus or OpenTelemetry client only inside a plug-in', () => {
      const offenders: string[] = [];
      for (const file of walk(SRC)) {
        const name = rel(SRC, file);
        if (!name.endsWith('.ts') || inOptionalDir(name)) continue;
        if (PROM_CLIENT_OUTSIDE_PLUGINS.includes(name)) continue;
        for (const path of importPaths(readFileSync(file, 'utf8'))) {
          if (/^(prom-client|@opentelemetry\/)/.test(path)) {
            offenders.push(`${name} -> ${path}`);
          }
        }
      }
      expect(offenders).to.deep.equal([]);
    });

    it('finds an import of telemetry however it is written', () => {
      const sample = [
        "import { a } from '../plugins/telemetry/flag';",
        "export { b } from './Telemetry.service';",
        "import './libs/telemetry';",
        "const c = require('../telemetry');",
        "const d = await import('./plugins/telemetry');",
        "import { e } from './libs/platform-events';",
      ].join('\n');
      expect(
        importPaths(sample).filter((path) => /telemetry/i.test(path)),
      ).to.have.length(5);
    });

    it('keeps the checked-in plug-in list in step with the generator', () => {
      execFileSync(process.execPath, [GENERATOR, '--check'], { stdio: 'pipe' });
    });
  });

  describe('a build without the telemetry plug-in', function () {
    this.timeout(600_000);
    let root: string;
    let stripped: string;

    before(() => {
      root = mkdtempSync(join(tmpdir(), 'pipeshub-stripped-'));
      stripped = join(root, 'src');
      cpSync(SRC, stripped, { recursive: true });
      cpSync(join(APP, 'tsconfig.json'), join(root, 'tsconfig.json'));
      symlinkSync(realpathSync(join(APP, 'node_modules')), join(root, 'node_modules'));
      for (const dir of OPTIONAL_DIRS) {
        rmSync(join(stripped, dir), { recursive: true, force: true });
      }
      // No arguments: the generator lists whatever plug-in directories are left.
      execFileSync(process.execPath, [GENERATOR, '--src', stripped], { stdio: 'pipe' });
    });

    after(() => {
      if (root) rmSync(root, { recursive: true, force: true });
    });

    it('regenerates an empty plug-in list', () => {
      const list = readFileSync(join(stripped, PLUGIN_LIST), 'utf8');
      expect(list).to.include('export const plugins: readonly PlatformPlugin[] = [];');
      expect(list).not.to.match(/telemetry/i);
      expect(existsSync(join(stripped, 'plugins', 'telemetry'))).to.equal(false);
    });

    it('type-checks', () => {
      const errors = tscErrors(root);
      if (errors.length === 0) return;
      // An installation whose dependencies do not match package.json fails the
      // full tree too; removing the plug-in must add nothing to that.
      const full = new Set(tscErrors(APP));
      expect(errors.filter((error) => !full.has(error))).to.deep.equal([]);
    });

    it('says "telemetry" nowhere except the documented API reference lines', () => {
      const unexpected = telemetryHits(stripped).filter(
        (hit) =>
          !harnessTracing(hit) &&
          ![...ALLOWED_IN_STRIPPED_TREE, ...ALSO_ALLOWED].some(
            (allowed) =>
              allowed.file === hit.file &&
              (allowed.text === undefined || hit.text.includes(allowed.text)),
          ),
      );
      expect(unexpected).to.deep.equal([]);
    });

    it('still needs every allowed line (drop an entry once its line is gone)', () => {
      const hits = telemetryHits(stripped);
      const unused = ALLOWED_IN_STRIPPED_TREE.filter(
        (allowed) =>
          !hits.some((hit) => hit.file === allowed.file && hit.text.includes(allowed.text)),
      ).map((allowed) => allowed.text);
      expect(unused).to.deep.equal([]);
    });

    it('imports no Prometheus or OpenTelemetry client', () => {
      const offenders: string[] = [];
      for (const file of walk(stripped)) {
        if (!file.endsWith('.ts')) continue;
        if (PROM_CLIENT_OUTSIDE_PLUGINS.includes(rel(stripped, file))) continue;
        for (const path of importPaths(readFileSync(file, 'utf8'))) {
          if (/^(prom-client|@opentelemetry\/)/.test(path)) {
            offenders.push(`${rel(stripped, file)} -> ${path}`);
          }
        }
      }
      expect(offenders).to.deep.equal([]);
    });
  });

  describe('scripts/generate-plugins.mjs', () => {
    let root: string;
    const generate = (args: string[], env: NodeJS.ProcessEnv = {}): string => {
      execFileSync(process.execPath, [GENERATOR, '--src', join(root, 'src'), ...args], {
        stdio: 'pipe',
        env: { ...process.env, PLATFORM_PLUGINS: undefined, ...env } as NodeJS.ProcessEnv,
      });
      return readFileSync(join(root, 'src', PLUGIN_LIST), 'utf8');
    };

    beforeEach(() => {
      root = mkdtempSync(join(tmpdir(), 'pipeshub-plugins-'));
      for (const name of ['beta', 'alpha-one']) {
        cpSync(join(SRC, 'plugins.generated.ts'), join(root, 'src', 'plugins', name, 'index.ts'));
      }
    });
    afterEach(() => rmSync(root, { recursive: true, force: true }));

    it('lists every plug-in directory, sorted, when given no list', () => {
      const list = generate([]);
      expect(list).to.include("import alphaOnePlugin from './plugins/alpha-one';");
      expect(list).to.include("import betaPlugin from './plugins/beta';");
      expect(list.indexOf('alphaOnePlugin,')).to.be.lessThan(list.indexOf('betaPlugin,'));
    });

    it('takes the list from arguments or from PLATFORM_PLUGINS', () => {
      expect(generate(['beta'])).not.to.include('alpha-one');
      expect(generate([], { PLATFORM_PLUGINS: 'alpha-one' })).not.to.include('beta');
    });

    it('writes an empty list for --none and for an empty PLATFORM_PLUGINS', () => {
      const empty = 'export const plugins: readonly PlatformPlugin[] = [];';
      expect(generate(['--none'])).to.include(empty);
      expect(generate([], { PLATFORM_PLUGINS: '' })).to.include(empty);
    });

    it('refuses a plug-in that is not there, and --check reports a stale file', () => {
      expect(() => generate(['missing'])).to.throw();
      generate(['beta']);
      expect(() => generate(['--check'])).to.throw();
      expect(() => generate(['beta', '--check'])).not.to.throw();
    });
  });
});
