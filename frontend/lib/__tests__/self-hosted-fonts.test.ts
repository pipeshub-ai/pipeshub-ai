// An air-gapped install cannot reach fonts.googleapis.com, so the dashboard must ship its fonts.
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const root = resolve(__dirname, '..', '..');
const read = (path: string) => readFileSync(join(root, path), 'utf8');

function sources(dir: string): string[] {
  return readdirSync(join(root, dir)).flatMap((name) => {
    const rel = `${dir}/${name}`;
    if (statSync(join(root, rel)).isDirectory()) {
      return name === 'node_modules' || name === '__tests__' ? [] : sources(rel);
    }
    return /\.(tsx?|css)$/.test(name) && !/\.test\./.test(name) ? [rel] : [];
  });
}

describe('self-hosted fonts', () => {
  it('no app or lib source loads a font from Google', () => {
    const offenders = [...sources('app'), ...sources('lib')].filter((file) =>
      /fonts\.(googleapis|gstatic)\.com/.test(read(file)),
    );
    expect(offenders).toEqual([]);
  });

  it('globals.css takes Manrope and the icon fonts from npm packages', () => {
    const css = read('app/globals.css');
    for (const weight of [400, 500, 600, 700]) {
      expect(css).toContain(`@fontsource/manrope/${weight}.css`);
    }
    expect(css).toContain('material-icons/iconfont/filled.css');
    expect(css).toContain('material-symbols/outlined.css');
  });

  // The outlined icon class forces 'Material Symbols Outlined'; with no @font-face for a family the
  // ligature names show as text (every icon became overlapping words once).
  it('every font family the app CSS names has a bundled @font-face', () => {
    const generic = new Set(['inherit', 'initial', 'unset', 'monospace', 'sans-serif', 'serif', 'system-ui', 'ui-monospace']);
    const faces = new Set<string>();
    const seen = new Set<string>();
    const collectFaces = (file: string) => {
      if (seen.has(file)) return;
      seen.add(file);
      const css = readFileSync(file, 'utf8');
      for (const m of css.matchAll(/@font-face\s*{[^}]*?font-family:\s*["']?([^;"']+)["']?/g)) faces.add(m[1].trim());
      for (const m of css.matchAll(/@import\s+(?:url\()?["']([^"']+)["']/g)) {
        const target = m[1].startsWith('.') ? resolve(file, '..', m[1]) : join(root, 'node_modules', m[1]);
        if (/^https?:/.test(m[1])) throw new Error(`external stylesheet: ${m[1]}`);
        collectFaces(target);
        // The font files a bundled stylesheet points at must exist too.
        for (const f of readFileSync(target, 'utf8').matchAll(/url\(["']?(\.[^"')]+)["']?\)/g)) {
          expect(existsSync(resolve(target, '..', f[1])), `${target} -> ${f[1]}`).toBe(true);
        }
      }
    };
    collectFaces(join(root, 'app/globals.css'));
    const used = new Set<string>();
    for (const file of sources('app').filter((f) => f.endsWith('.css'))) {
      for (const m of readFileSync(join(root, file), 'utf8').matchAll(/font-family:\s*([^;}]+)/g)) {
        for (const name of m[1].replace(/!important/, '').split(',')) {
          const n = name.trim().replace(/^["']|["']$/g, '');
          if (n && !generic.has(n) && !n.startsWith('var(')) used.add(n);
        }
      }
    }
    expect(used.has('Material Symbols Outlined')).toBe(true);
    for (const family of used) {
      // The first family of a stack is the one that has to load; later ones are fallbacks.
      const stacks = [...readFileSync(join(root, 'app/globals.css'), 'utf8').matchAll(/font-family:\s*([^;}]+)/g)];
      const first = stacks.map((m) => m[1].replace(/!important/, '').split(',')[0].trim().replace(/^["']|["']$/g, ''));
      if (first.includes(family)) expect(faces.has(family), `no @font-face for ${family}`).toBe(true);
    }
  });

  // The Lottie player fetches its wasm from jsdelivr/unpkg unless given a URL; without a route to
  // the internet the loader animation never starts. The built chunks still hold the library's
  // default literal, so what is checked is that the app overrides it with a bundled asset.
  it('points the Lottie player at a wasm that ships in the build, not at a CDN', () => {
    const loader = read('app/components/ui/lottie-loader.tsx');
    expect(loader).toMatch(/setWasmUrl\(\s*new URL\([^)]*dotlottie-player\.wasm[^)]*import\.meta\.url/);
    const offenders = [...sources('app'), ...sources('lib')].filter((file) =>
      /(cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com)/.test(read(file)),
    );
    expect(offenders).toEqual([]);
  });

  // Google's stylesheet gave .material-icons-outlined its 24px size, line-height 1 and ligature settings;
  // consumers that set no inline fontSize rendered at the inherited size once it was removed.
  it('.material-icons-outlined keeps the icon reset the Google stylesheet used to apply', () => {
    const rule = read('app/globals.css').match(/\n\.material-icons-outlined\s*{([^}]*)}/)?.[1] ?? '';
    expect(rule).toMatch(/font-size:\s*24px/);
    expect(rule).toMatch(/line-height:\s*1;/);
    expect(rule).toMatch(/letter-spacing:\s*normal/);
    expect(rule).toMatch(/text-transform:\s*none/);
    expect(rule).toMatch(/white-space:\s*nowrap/);
    expect(rule).toMatch(/font-feature-settings:\s*'liga'/);
  });

  it('declares the packages as dependencies, not dev tooling', () => {
    const pkg = JSON.parse(read('package.json'));
    expect(pkg.dependencies['@fontsource/manrope']).toBeTruthy();
    expect(pkg.dependencies['material-icons']).toBeTruthy();
    expect(pkg.dependencies['material-symbols']).toBeTruthy();
  });

  it('both root layouts render no stylesheet link to an external font', () => {
    for (const layout of ['app/(main)/root-layout.tsx', 'app/(public)/root-layout.tsx']) {
      expect(read(layout)).not.toMatch(/rel="stylesheet"/);
    }
  });
});
