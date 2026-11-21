/**
 * QAFINAL-FU-AXE(-2): Radix's steps 9 and 10 are fills and borders, not text. gray-9 or slate-10 text is 3.4-4.2:1 in
 * light, under WCAG AA's 4.5:1 for 12-14 px text, and a step-9 icon on a step-3 panel is under 1.4.11's 3:1. Step 11
 * is the scale's low-contrast text. So in the classic UI no neutral step 9 or 10 is a foreground colour: not in a
 * `<Text>`, a style object, an icon's `color` or a stylesheet. The uses that stay are listed in EXEMPT, each with
 * what it paints: a line, fill or focus ring (no text), or a disabled control (WCAG 1.4.3 exempts inactive parts).
 *
 * R31-AXET-FU-1 holds the same rule for every other scale. A hue's step 9 or 10 (accent, red, amber, green, blue, …) is
 * a solid fill: amber-9 is 1.4:1 on white and jade-9 2.8:1, so as text or an icon it takes step 11. A neutral step 1-8
 * named as a colour (`color`, `iconColor`, an icon's `color=`) is a border step as text (gray-8 is 1.7:1) and takes step
 * 11 too. Hue uses are held wherever they are not a fill or a line; faint neutrals only where a colour is named, since
 * steps 1-8 are mostly backgrounds.
 *
 * R31UIFU-FU-2: step 11 is the text step on every surface, not only on white. Neutral 11 holds 4.5:1 on the page and on
 * neutral steps 1-5; Radix's light step 11 of jade, amber, green, blue, orange, red and sky does not (amber is 3.5:1 on
 * olive-5). globals.css darkens those steps in light, hue kept, so no text has to know its panel (TEXTSTEP-6/6b), and
 * TEXTSTEP-7 checks each use against the background its element or style names, or every surface when it names none.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const FRONTEND = path.resolve(__dirname, '../../../..');
const RADIX = readFileSync(path.join(FRONTEND, 'node_modules/@radix-ui/themes/styles.css'), 'utf8');
const GLOBALS = readFileSync(path.join(FRONTEND, 'app/globals.css'), 'utf8');

/** A neutral scale's step 9 or 10, solid or alpha. */
const LOW_STEP = /var\(--(gray|slate|olive|neutral|sage|mauve|sand)-a?(9|10)\)/g;

const HUES =
  'accent|tomato|red|ruby|crimson|pink|plum|purple|violet|iris|indigo|blue|cyan|teal|jade|green|grass|bronze|gold|brown|orange|amber|yellow|lime|mint|sky';

/** A hue's step 9 or 10, solid or alpha. */
const HUE_LOW_STEP = new RegExp(`var\\(--(${HUES})-a?(9|10)\\)`, 'g');

/** A neutral scale's step 1 to 8, solid or alpha. */
const FAINT_STEP = /var\(--(gray|slate|olive|neutral|sage|mauve|sand)-a?[1-8]\)/g;

/** The property or name a value is assigned to: `color:`, `iconColor=`, `'--x':`, `QUEUED:`, `const X =`. */
const ASSIGNED = /(--[\w-]+|[A-Za-z_$][\w$-]*)['"]?\s*(?::(?!:)|=(?![=>]))/g;

/** Names that paint a fill or a line, not a glyph. */
const PAINTS_FILL = /background|border|outline|shadow|bg|fill|stroke|indicator|track|ring|divider|edge|caret|gradient|scrollbar|decoration/i;

/** The source roots of the classic UI. */
const ROOTS = ['app', 'lib', 'components'];

/** How many lines above a use `near` may sit (a ternary or a selector a few lines up). */
const NEAR_LINES = 6;

type Exempt = {
  file: string;
  /** Text on the use's own line. */
  line: string;
  /** Text on that line or up to NEAR_LINES above, where `line` alone is not specific enough. */
  near?: string;
  why: 'line, fill or focus ring: no text' | 'disabled control' | 'text on a solid fill';
};

const EXEMPT: readonly Exempt[] = [
  { file: 'app/globals.css', line: '--agent-flow-edge: var(--gray-10)', why: 'line, fill or focus ring: no text' },
  { file: 'app/globals.css', line: '--agent-flow-handle-border: var(--gray-10)', why: 'line, fill or focus ring: no text' },
  { file: 'app/globals.css', line: '--agent-flow-edge: var(--gray-9)', why: 'line, fill or focus ring: no text' },
  {
    file: 'app/globals.css',
    line: 'background-color: var(--slate-10);',
    near: '::-webkit-scrollbar-thumb:hover',
    why: 'line, fill or focus ring: no text',
  },
  { file: 'app/globals.css', line: 'border-color: var(--gray-9) !important', why: 'line, fill or focus ring: no text' },
  {
    file: 'app/globals.css',
    line: 'box-shadow: inset 0 0 0 1px var(--gray-a9);',
    near: ".rt-SwitchRoot.rt-variant-surface[data-state='unchecked']",
    why: 'line, fill or focus ring: no text',
  },
  { file: 'app/globals.css', line: '--text-field-focus-color: var(--olive-9)', why: 'line, fill or focus ring: no text' },
  { file: 'app/globals.css', line: 'color-mix(in srgb, var(--olive-9) 18%, transparent)', why: 'line, fill or focus ring: no text' },
  { file: 'app/components/upload-progress-tracker.tsx', line: "borderTopColor: 'var(--slate-10)'", why: 'line, fill or focus ring: no text' },
  { file: 'app/(main)/workspace/general/page.tsx', line: "backgroundColor: 'var(--slate-9)'", why: 'line, fill or focus ring: no text' },
  {
    file: 'app/(main)/chat/components/message-area/message-bubble.tsx',
    line: "backgroundColor: 'var(--slate-9)'",
    why: 'line, fill or focus ring: no text',
  },
  {
    file: 'app/(main)/knowledge-base/components/dialogs/upload-data-sidebar.tsx',
    line: "border: `1px dashed ${isDragOver ? 'var(--accent-9)' : 'var(--slate-9)'}`",
    why: 'line, fill or focus ring: no text',
  },
  { file: 'app/(main)/workspace/actions/components/action-card.tsx', line: "color: cta === 'unavailable' ? 'var(--gray-9)'", why: 'disabled control' },
  {
    file: 'app/(main)/workspace/schedules/components/create-schedule-dialog.tsx',
    line: "style={blocked === null ? undefined : { color: 'var(--slate-10)' }}",
    why: 'disabled control',
  },
  { file: 'app/(main)/chat/components/chat-input.tsx', line: "isDisabled ? 'var(--slate-9)'", why: 'disabled control' },
  {
    file: 'app/(main)/chat/components/message-area/sandbox-result-rows.tsx',
    line: "color: choice.available ? undefined : 'var(--gray-10)'",
    why: 'disabled control',
  },
  { file: 'app/(main)/onboarding/onboardingPage.tsx', line: "color: isMiddleNextEnabled ? 'white' : 'var(--gray-9)'", why: 'disabled control' },
  {
    file: 'app/(main)/onboarding/components/step-org-profile.tsx',
    line: "? 'var(--gray-9)'",
    near: '!isFormValid || submitting || savedSuccessfully',
    why: 'disabled control',
  },
  // R31-AXET-FU-1: hue steps 9/10 that are a fill, and faint neutrals that are disabled or sit on a solid fill.
  { file: 'app/globals.css', line: '--mode-chat-active-toggle: var(--iris-9)', why: 'line, fill or focus ring: no text' },
  { file: 'app/globals.css', line: '--mode-search-toggle: var(--accent-9)', why: 'line, fill or focus ring: no text' },
  { file: 'app/globals.css', line: '--mode-web-search-toggle: var(--orange-9)', why: 'line, fill or focus ring: no text' },
  { file: 'app/globals.css', line: '--mode-image-toggle: var(--blue-9)', why: 'line, fill or focus ring: no text' },
  { file: 'app/globals.css', line: '--mode-agent-toggle: var(--green-9)', why: 'line, fill or focus ring: no text' },
  { file: 'app/components/indexing-stats/ai-enrichment-section.tsx', line: "COMPLETED_COLOR = 'var(--accent-9)'", why: 'line, fill or focus ring: no text' },
  { file: 'app/(main)/components/tours/onboarding/pixel-loader.tsx', line: "COLOR_ACTIVE = 'var(--accent-9)'", why: 'line, fill or focus ring: no text' },
  {
    file: 'app/(main)/chat/components/message-area/agent-activity.tsx',
    line: "return { kind: 'dot', color: 'var(--accent-9)' }",
    why: 'line, fill or focus ring: no text',
  },
  {
    file: 'app/(main)/chat/components/message-area/outside-access.tsx',
    line: "const DOT: Record<LineTone, string> = { ok: 'var(--green-9)'",
    why: 'line, fill or focus ring: no text',
  },
  {
    file: 'app/(main)/chat/sidebar/chat-section-element.tsx',
    line: "const DOT_COLOR: Record<'needsYou' | 'finished', string> = { needsYou: 'var(--amber-9)'",
    why: 'line, fill or focus ring: no text',
  },
  { file: 'app/components/upload-progress-tracker.tsx', line: "? 'var(--slate-1)'", near: 'color: isActive', why: 'text on a solid fill' },
  {
    file: 'app/(main)/chat/components/message-area/message-actions.tsx',
    line: '<MaterialIcon name="arrow_upward" size={14} color="var(--gray-1)" />',
    why: 'text on a solid fill',
  },
  { file: 'app/components/ui/segmented-tabs.tsx', line: "color: item.disabled ? 'var(--gray-7)'", why: 'disabled control' },
  {
    file: 'app/(main)/chat/components/chat-panel/expansion-panels/agent-scoped-resources-panel.tsx',
    line: "color: isDisabled ? 'var(--gray-7)'",
    why: 'disabled control',
  },
  { file: 'app/(main)/knowledge-base/components/dialogs/move-folder-sidebar.tsx', line: "if (isDisabled) return 'var(--slate-8)'", why: 'disabled control' },
  { file: 'app/(main)/knowledge-base/components/dialogs/move-folder-sidebar.tsx', line: "color={isDisabled ? 'var(--slate-8)'", why: 'disabled control' },
  {
    file: 'app/(main)/chat/components/message-area/response-tabs/response-tabs.tsx',
    line: "? 'var(--slate-8)'",
    near: ': isDisabled',
    why: 'disabled control',
  },
  { file: 'app/(main)/chat/components/chat-panel/plus-menu-button.tsx', line: "color={disabled ? 'var(--slate-5)'", why: 'disabled control' },
  {
    file: 'app/(main)/workspace/connectors/components/configure-tab/import-date-section.tsx',
    line: "color: importAllTime ? 'var(--gray-8)'",
    why: 'disabled control',
  },
  {
    file: 'app/(main)/chat/components/mention-composer-field.tsx',
    line: "color: readOnly ? (typeof style?.color === 'string' ? style.color : 'var(--slate-a8)')",
    why: 'disabled control',
  },
  { file: 'app/(main)/chat/components/chat-input.tsx', line: "isRegenerateMode ? 'var(--slate-a8)'", why: 'disabled control' },
  { file: 'app/(main)/chat/components/chat-input.tsx', line: "isRegenerateMode ? 'var(--slate-5)'", why: 'disabled control' },
  { file: 'app/(public)/forms/otp-sign-in-flow.tsx', line: "color: canSendOtp ? 'var(--accent-11)' : 'var(--gray-8)'", why: 'disabled control' },
];

const isSource = (name: string) => /\.(tsx?|css)$/.test(name) && !/\.(test|spec)\./.test(name);

function sources(rel: string): string[] {
  const at = path.join(FRONTEND, rel);
  if (statSync(at).isFile()) return isSource(rel) ? [rel] : [];
  return readdirSync(at).flatMap((name) =>
    name === '__tests__' || name === 'node_modules' ? [] : sources(path.join(rel, name)),
  );
}

/**
 * The areas TEXTSTEP-1 reports by: each folder under app/(main), app/(public) and app/components, then app's own
 * files, lib and components. Read from disk, so a new folder is held the day it appears.
 */
function areas(): string[] {
  const groups = ['app/(main)', 'app/(public)', 'app/components'];
  const dirs = (rel: string) =>
    readdirSync(path.join(FRONTEND, rel)).filter((name) => name !== '__tests__' && statSync(path.join(FRONTEND, rel, name)).isDirectory());
  const appRest = dirs('app').filter((name) => !groups.includes(`app/${name}`)).map((name) => `app/${name}`);
  const appFiles = readdirSync(path.join(FRONTEND, 'app')).filter((name) => isSource(name)).map((name) => `app/${name}`);
  return [
    ...groups.flatMap((group) => [
      ...dirs(group).map((name) => `${group}/${name}`),
      ...readdirSync(path.join(FRONTEND, group)).filter(isSource).map((name) => `${group}/${name}`),
    ]),
    ...appRest,
    ...appFiles,
    ...ROOTS.filter((root) => root !== 'app'),
  ];
}

type Rule = 'neutral-9-10' | 'hue-9-10' | 'faint-neutral';
type Use = { file: string; line: number; text: string; rule: Rule; exempt: number | null };

/** The name the value at `at` on line `index` is assigned to, looking up a few lines for a wrapped ternary. */
function assignedTo(lines: readonly string[], index: number, at: number): string {
  for (let row = index; row >= Math.max(0, index - NEAR_LINES); row -= 1) {
    const text = row === index ? lines[row].slice(0, at) : lines[row];
    const names = [...text.matchAll(ASSIGNED)];
    if (names.length > 0) return names[names.length - 1][1];
  }
  return '';
}

/** Whether the value at `at` paints a fill or a line: a background, border or shadow, or a `2px solid` shorthand. */
function paintsFill(lines: readonly string[], index: number, at: number): boolean {
  return PAINTS_FILL.test(assignedTo(lines, index, at)) || /\d(px)?\s+(solid|dashed|dotted)\s*$/.test(lines[index].slice(0, at));
}

/** Every low-contrast foreground candidate in a source file, under the three rules, with the exemption that covers it. */
function lowStepUses(file: string, source: string, exempt: readonly Exempt[] = EXEMPT): Use[] {
  const lines = source.split('\n');
  return lines.flatMap((text, index) => {
    const found: Rule[] = [
      ...[...text.matchAll(LOW_STEP)].map((): Rule => 'neutral-9-10'),
      ...[...text.matchAll(HUE_LOW_STEP)].filter((m) => !paintsFill(lines, index, m.index ?? 0)).map((): Rule => 'hue-9-10'),
      ...[...text.matchAll(FAINT_STEP)]
        .filter((m) => /colou?r$/i.test(assignedTo(lines, index, m.index ?? 0)) && !paintsFill(lines, index, m.index ?? 0))
        .map((): Rule => 'faint-neutral'),
    ];
    if (found.length === 0) return [];
    const window = lines.slice(Math.max(0, index - NEAR_LINES), index + 1).join('\n');
    const at = exempt.findIndex((e) => e.file === file && text.includes(e.line) && (e.near === undefined || window.includes(e.near)));
    return found.map((rule) => ({ file, line: index + 1, text: text.trim(), rule, exempt: at === -1 ? null : at }));
  });
}

const ALL = ROOTS.flatMap(sources);
const USES = ALL.flatMap((file) => lowStepUses(file, readFileSync(path.join(FRONTEND, file), 'utf8')));

describe('text colours across the classic UI', () => {
  it('TEXTSTEP-0: the areas cover every source file the check reads', () => {
    const covered = areas();
    const outside = ALL.filter((file) => !covered.some((area) => file === area || file.startsWith(`${area}/`)));
    expect(outside).toEqual([]);
    expect(covered.length).toBeGreaterThan(40);
  });

  it.each(areas())('TEXTSTEP-1: no text or icon in %s is drawn in a neutral step 9 or 10, a hue step 9 or 10, or a faint neutral', (area) => {
    const found = USES.filter((use) => use.exempt === null && (use.file === area || use.file.startsWith(`${area}/`))).map(
      (use) => `${use.rule} ${use.file}:${use.line}  ${use.text.slice(0, 120)}`,
    );
    expect(found).toEqual([]);
  });

  it('TEXTSTEP-2: the check finds a step-9/10 neutral in a Text, an icon, a ternary and a stylesheet', () => {
    const probe = [
      `<Text size="1" style={{ color: 'var(--gray-10)' }}>x</Text>`,
      `<MaterialIcon name="info" color="var(--slate-9)" />`,
      `color: active ? 'var(--slate-12)' : 'var(--slate-a10)',`,
      `.x::placeholder { color: var(--neutral-a10); }`,
      `<Text style={{ color: 'var(--gray-11)' }}>fine</Text>`,
    ].join('\n');
    expect(lowStepUses('probe.tsx', probe).map((use) => use.line)).toEqual([1, 2, 3, 4]);
  });

  it('TEXTSTEP-2b: the check finds a hue step 9/10 glyph and a faint neutral colour, and passes their fills', () => {
    const probe = [
      `<MaterialIcon name="error" size={16} color="var(--red-9)" />`,
      `<Text style={{ color: 'var(--accent-10)' }}>x</Text>`,
      `const DONE: Record<S, string> = { ok: 'var(--green-9)', held: 'var(--amber-a9)' };`,
      `color: failed`,
      `  ? 'var(--amber-10)'`,
      `<MaterialIcon name="inbox" size={40} color="var(--slate-8)" />`,
      `iconColor: 'var(--gray-a6)',`,
      `style={{ backgroundColor: 'var(--accent-9)', color: 'var(--accent-contrast)' }}`,
      `border: isOver ? '2px solid var(--accent-9)' : 'none',`,
      `boxShadow: '0 0 0 1px var(--red-10)',`,
      `iconBgColor: 'var(--olive-3)',`,
      `const PANEL = 'var(--olive-2)';`,
      `<Text style={{ color: 'var(--red-11)' }}>fine</Text>`,
    ].join('\n');
    expect(lowStepUses('probe.tsx', probe).map((use) => `${use.line} ${use.rule}`)).toEqual([
      '1 hue-9-10',
      '2 hue-9-10',
      '3 hue-9-10',
      '3 hue-9-10',
      '5 hue-9-10',
      '6 faint-neutral',
      '7 faint-neutral',
    ]);
  });

  it('TEXTSTEP-3: every exemption still names a use, so none outlives the code it was written for', () => {
    const used = new Set(USES.map((use) => use.exempt).filter((at): at is number => at !== null));
    expect(EXEMPT.filter((_, at) => !used.has(at))).toEqual([]);
  });

  it("TEXTSTEP-4: Radix's own surface and classic placeholders are gray-a10, and globals.css raises them to gray-a11", () => {
    for (const part of ['TextField', 'TextArea']) {
      expect(RADIX).toMatch(new RegExp(`\\.rt-${part}Root:where\\(\\.rt-variant-surface\\) :where\\(\\.rt-${part}Input\\)::placeholder \\{\\s*color: var\\(--gray-a10\\);`));
    }
    expect(GLOBALS).toMatch(
      /:is\(\.rt-TextFieldRoot, \.rt-TextAreaRoot\):not\(\.rt-variant-soft\) :is\(\.rt-TextFieldInput, \.rt-TextAreaInput\):not\(:disabled\):not\(:read-only\)::placeholder,\s*\.rt-SelectTrigger:not\(\.rt-variant-soft\):not\(\.rt-variant-ghost\)\[data-placeholder\] \.rt-SelectTriggerInner \{\s*color: var\(--gray-a11\);/,
    );
  });

  it('TEXTSTEP-5: every custom property the UI names is defined: by Radix, in a stylesheet, or set where it is used', () => {
    const SOURCES = ALL.map((file) => [file, readFileSync(path.join(FRONTEND, file), 'utf8')] as const);
    const defined = new Set([
      ...[...RADIX.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]),
      ...SOURCES.flatMap(([, source]) => [...source.matchAll(/(?:^|[\s{;,'"`(])(--[\w-]+)['"`]?\s*:/gm)].map((m) => m[1])),
    ]);
    // Radix sets --radix-* on a popover's content while it is open; a name followed by a quote or `${` is spliced.
    const undefinedNames = SOURCES.flatMap(([file, source]) =>
      [...source.matchAll(/var\(\s*(--[\w-]+)(?![\w'"`$-])/g)]
        .map((m) => m[1])
        .filter((name) => !name.startsWith('--radix-') && !defined.has(name))
        .map((name) => `${file}  ${name}`),
    );
    expect([...new Set(undefinedNames)]).toEqual([]);
    for (const step of ['1', '3', '9', '11', '12', 'a3', 'a5', 'a8', 'a10', 'a11', 'a12']) {
      expect([...GLOBALS.matchAll(new RegExp(`--neutral-${step}: #[0-9a-f]{6,8};`, 'g'))].length, `--neutral-${step}`).toBe(2);
    }
  });

  it('TEXTSTEP-6: step 11 of every hue the UI writes text in meets 4.5:1 on the page, every surface step 1-5 and its own soft tint, light and dark', () => {
    const found = TEXT_HUES.flatMap((hue) =>
      APPEARANCES.flatMap((look) =>
        ['11', 'a11'].flatMap((step) => {
          const fg = token(`${hue}-${step}`, look);
          if (fg === null) return [`${hue}-${step} ${look}: not defined`];
          const tint = token(`${hue}-a3`, look);
          const panels: [string, Rgb][] = [
            ...surfaces(look),
            [`${hue}-3`, rgbOf(token(`${hue}-3`, look) ?? '#000000')],
            ...cards(look).map(([name, card]): [string, Rgb] => [`${hue}-a3 on ${name}`, over(tint ?? '#00000000', card)]),
          ];
          return panels
            .map(([name, panel]) => [name, ratio(over(fg, panel), panel)] as const)
            .filter(([, r]) => r < 4.5)
            .map(([name, r]) => `${hue}-${step} ${look} on ${name}: ${r.toFixed(2)}`);
        }),
      ),
    );
    expect(found).toEqual([]);
    // The rule finds the hues from the code: the accent (emerald), red and amber at least.
    expect(TEXT_HUES).toEqual(expect.arrayContaining(['emerald', 'red', 'amber', 'green', 'jade', 'blue', 'orange']));
    // Why the light hues are raised: Radix's own step 11 misses the rule (amber 3.5:1 on olive-5, jade 4.1:1 on olive-3).
    const olive = (step: number) => rgbOf(radix(`olive-${step}`)[0]);
    expect(ratio(rgbOf(radix('amber-11')[0]), olive(5))).toBeLessThan(4.5);
    expect(ratio(rgbOf(radix('jade-11')[0]), olive(3))).toBeLessThan(4.5);
    // Why step 9 is a fill: as text the jade's step 9 is under 4.5:1 on the light page, amber's under even 3:1.
    expect(ratio(rgbOf(radix('jade-9')[0]), olive(1))).toBeLessThan(4.5);
    expect(ratio(rgbOf(radix('amber-9')[0]), olive(1))).toBeLessThan(3);
  });

  it('TEXTSTEP-6b: a raised step 11 is still its hue: Radix 11 darkened, hue within 6 degrees, no darker than step 12', () => {
    const raised = [...block(GLOBALS, RAISED).matchAll(/--([a-z]+)-(a?11):\s*(#[0-9a-f]{6});/g)];
    expect(raised.length).toBeGreaterThan(0);
    const drift = raised.flatMap(([, hue, step, value]) => {
      const [l, c, h] = oklch(rgbOf(value));
      const [l11, , h11] = oklch(rgbOf(radix(`${hue}-11`)[0]));
      const [l12] = oklch(rgbOf(radix(`${hue}-12`)[0]));
      const dh = Math.abs(((h - h11 + 540) % 360) - 180);
      const ok = dh <= 6 && l < l11 && l > l12 && c > 0.05;
      return ok ? [] : [`${hue}-${step} ${value}: hue moved ${dh.toFixed(1)}, L ${l.toFixed(3)} (11 ${l11.toFixed(3)}, 12 ${l12.toFixed(3)})`];
    });
    expect(drift).toEqual([]);
    // Solid and alpha step 11 are one colour, so a Radix colour prop (which reads a11) draws what `var(--x-11)` draws.
    const byName = new Map(raised.map(([, hue, step, value]) => [`${hue}-${step}`, value]));
    for (const [, hue] of raised) expect(byName.get(`${hue}-a11`), hue).toBe(byName.get(`${hue}-11`));
  });

  it.each(APPEARANCES)('TEXTSTEP-7: every text in a hue step 11 meets 4.5:1 on the panel it sits on (%s)', (look) => {
    const found = HUE_TEXT.flatMap((use) => {
      const fg = token(use.token, look);
      if (fg === null) return [`${use.file}:${use.line} ${use.token} is not defined`];
      const named = use.panel === null ? null : token(use.panel, look);
      // A soft tint whose card is not named sits on the page or a card (TEXTSTEP-6's rule), plain text on any surface.
      const under: [string, Rgb][] =
        named !== null ? [[use.panel as string, over(named, page(look))]] : use.tinted ? cards(look) : surfaces(look);
      const tint = use.tinted ? token(`${use.hue}-a3`, look) : null;
      return under
        .map(([name, panel]): [string, Rgb] => (tint === null ? [name, panel] : [`${use.hue}-a3 on ${name}`, over(tint, panel)]))
        .map(([name, panel]) => [name, ratio(over(fg, panel), panel)] as const)
        .filter(([, r]) => r < 4.5)
        .map(([name, r]) => `${use.file}:${use.line} ${use.token} on ${name}: ${r.toFixed(2)}  ${use.text.slice(0, 100)}`);
    });
    expect(found).toEqual([]);
    expect(HUE_TEXT.length).toBeGreaterThan(500);
  });

  it('TEXTSTEP-7b: the check finds the panel a hue text names, and the soft tint of a Badge or Callout', () => {
    const probe = [
      `<Box style={{ backgroundColor: 'var(--olive-3)' }}>`,
      `  <Text style={{ color: 'var(--amber-11)' }}>held</Text>`,
      `<Badge size="1" color="red">failed</Badge>`,
      `<Button variant="soft" color="jade">retry</Button>`,
      `<Button color="red">delete</Button>`,
      `<Text size="1" color="green">done</Text>`,
      `<MaterialIcon name="error" color="var(--red-a11)" />`,
      `style={{ border: '1px solid var(--red-11)' }}`,
    ].join('\n');
    expect(hueTextUses('probe.tsx', probe).map((u) => `${u.line} ${u.token} ${u.panel ?? '-'} ${u.tinted}`)).toEqual([
      '2 amber-11 olive-3 false',
      '3 red-a11 - true',
      '4 jade-a11 - true',
      '6 green-a11 - false',
      '7 red-a11 - false',
    ]);
  });
});

// --- colour arithmetic and the theme's tokens (TEXTSTEP-6/7) ---------------------------------------------------------

type Rgb = [number, number, number];
type Look = 'light' | 'dark';
const APPEARANCES: readonly Look[] = ['light', 'dark'];

/** The block globals.css raises hue text steps in, light only. */
const RAISED = ':root:not(.dark, .dark-theme), .light, .light-theme';

function hexOf(value: string): string {
  const rgba = value.match(/^rgba?\(\s*(\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\s*\)$/);
  if (rgba === null) return value.toLowerCase();
  const [r, g, b] = rgba.slice(1, 4).map((x) => Number(x).toString(16).padStart(2, '0'));
  const a = Math.round(Number(rgba[4] ?? 1) * 255).toString(16).padStart(2, '0');
  return `#${r}${g}${b}${a}`;
}
const rgbOf = (hex: string): Rgb => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as Rgb;
const alphaOf = (hex: string) => (hex.length > 7 ? parseInt(hex.slice(7, 9), 16) / 255 : 1);
/** A colour, alpha or not, as it shows over `under`. */
const over = (hex: string, under: Rgb): Rgb => {
  const a = alphaOf(hex);
  return rgbOf(hex).map((c, i) => Math.round(c * a + under[i] * (1 - a))) as Rgb;
};
const linear = (c: number) => (c / 255 <= 0.04045 ? c / 255 / 12.92 : ((c / 255 + 0.055) / 1.055) ** 2.4);
const lum = (v: Rgb) => 0.2126 * linear(v[0]) + 0.7152 * linear(v[1]) + 0.0722 * linear(v[2]);
const ratio = (a: Rgb, b: Rgb) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
/** OKLCH lightness, chroma and hue angle. */
function oklch(v: Rgb): [number, number, number] {
  const [r, g, b] = v.map(linear);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return [L, Math.hypot(A, B), ((Math.atan2(B, A) * 180) / Math.PI + 360) % 360];
}

/** Radix's sRGB declarations of a token: light first, dark second. */
const radix = (name: string) => [...RADIX.matchAll(new RegExp(`--${name}: (#[0-9a-f]{6,8});`, 'g'))].map((m) => m[1]);

/** The declarations of the block whose selector is `selector`. */
function block(css: string, selector: string): string {
  const at = css.indexOf(`${selector} {`);
  if (at === -1) throw new Error(`globals.css has no block ${selector}`);
  return css.slice(at, css.indexOf('}', at));
}
const OWN: Record<Look, string[]> = {
  light: [block(GLOBALS, ':root, .light, .light-theme'), block(GLOBALS, RAISED)],
  dark: [block(GLOBALS, ':root[class~="dark"]')],
};

/** A token's value in one appearance: globals.css's own declaration last, else Radix's; `accent` is emerald. */
function token(name: string, look: Look): string | null {
  const key = name.replace(/^accent-/, 'emerald-');
  const own = OWN[look]
    .map((css) => css.match(new RegExp(`--${key}:\\s*(#[0-9a-fA-F]{6,8}|rgba?\\([^)]*\\));`))?.[1])
    .filter((v): v is string => v !== undefined);
  if (own.length > 0) return hexOf(own[own.length - 1]);
  return radix(key)[look === 'light' ? 0 : 1] ?? null;
}

const page = (look: Look): Rgb => (look === 'light' ? [255, 255, 255] : rgbOf(token('olive-1', 'dark') as string));
/** The page and the neutral surface steps (subtle, panel, hover, pressed) text may sit on: neutral 11's own floor. */
const surfaces = (look: Look): [string, Rgb][] => [
  ['page', page(look)],
  ...[1, 2, 3, 4, 5].map((step): [string, Rgb] => [`olive-${step}`, rgbOf(token(`olive-${step}`, look) as string)]),
];
/** What a soft Badge or Callout sits on: the page or a card. */
const cards = (look: Look) => surfaces(look).filter(([name]) => ['page', 'olive-2', 'olive-3'].includes(name));

// --- every text in a hue's step 11 ------------------------------------------------------------------------------------

const TEXT_HUE = new RegExp(`var\\(--(${HUES}|emerald)-(a?11)\\)`, 'g');
const HUE_PROP = new RegExp(`\\bcolor=\\{?['"](${HUES}|emerald)['"]`, 'g');
/** Radix parts whose `color` prop draws text in the hue's a11; a soft Badge, Callout or Button on its own a3 tint. */
const PROP_TEXT = /<(Text|Heading|Link|Code|Em|Strong|Badge|Callout\.Root|Button|IconButton)\b/;
const BACKGROUND = /(?:background(?:Color)?|--[\w-]*-bg)['"]?\s*:\s*[^,;]*?var\(--([\w-]+)\)/;

type HueText = { file: string; line: number; text: string; hue: string; token: string; panel: string | null; tinted: boolean };

/** Which arm of a `cond ? a : b` the text at `at` is in, if any. */
function arm(text: string, at: number): 'then' | 'else' | null {
  const before = text.slice(0, at);
  const ask = before.lastIndexOf('?');
  if (ask === -1) return null;
  return before.indexOf(' : ', ask) === -1 ? 'then' : 'else';
}

/**
 * The background the element or style around a use names: the nearest one at or above it, back to the line that opens
 * the element or rule, else on the next two lines. Where both sit in a ternary, the same arm.
 */
function panelNear(lines: readonly string[], index: number, at: number): string | null {
  const pick = (row: number): string | null => {
    const found = lines[row].match(BACKGROUND);
    if (found === null) return null;
    const side = row === index ? null : arm(lines[index], at);
    if (side === null) return found[1];
    const vars = [...lines[row].matchAll(/var\(--([\w-]+)\)/g)];
    const split = lines[row].indexOf(' : ', lines[row].indexOf('?'));
    const chosen = vars.find((v) => lines[row].includes('?') && ((v.index ?? 0) > split) === (side === 'else'));
    return chosen?.[1] ?? found[1];
  };
  for (let row = index; row >= Math.max(0, index - NEAR_LINES); row -= 1) {
    const found = pick(row);
    if (found !== null) return found;
    if (row < index && /<[A-Za-z]|\{\s*$/.test(lines[row]) && !/style=\{\{\s*$/.test(lines[row])) break;
  }
  for (let row = index + 1; row <= Math.min(lines.length - 1, index + 2); row += 1) {
    const found = pick(row);
    if (found !== null) return found;
  }
  return null;
}

function hueTextUses(file: string, source: string): HueText[] {
  const lines = source.split('\n');
  return lines.flatMap((text, index) => {
    const out: HueText[] = [];
    for (const m of text.matchAll(TEXT_HUE)) {
      if (paintsFill(lines, index, m.index ?? 0)) continue;
      const hue = m[1] === 'accent' ? 'emerald' : m[1];
      out.push({ file, line: index + 1, text: text.trim(), hue, token: `${hue}-${m[2]}`, panel: panelNear(lines, index, m.index ?? 0), tinted: false });
    }
    for (const m of text.matchAll(HUE_PROP)) {
      const window = lines.slice(Math.max(0, index - NEAR_LINES), index + 1);
      const opener = [...window].reverse().map((row) => row.match(PROP_TEXT)?.[1]).find((tag) => tag !== undefined);
      if (opener === undefined) continue;
      const element = window.slice(window.findLastIndex((row) => PROP_TEXT.test(row))).join('\n') + lines.slice(index + 1, index + 4).join('\n');
      const variant = element.match(/variant=["'](\w+)["']/)?.[1];
      const button = opener === 'Button' || opener === 'IconButton';
      // A solid Button draws the hue's contrast colour on step 9, not step 11.
      if (button && (variant === undefined || variant === 'solid' || variant === 'classic')) continue;
      const tinted = ['Badge', 'Callout.Root', 'Button', 'IconButton'].includes(opener) && ['soft', 'surface', undefined].includes(variant);
      const hue = m[1] === 'accent' ? 'emerald' : m[1];
      out.push({ file, line: index + 1, text: text.trim(), hue, token: `${hue}-a11`, panel: tinted ? null : panelNear(lines, index, m.index ?? 0), tinted });
    }
    return out;
  });
}

const HUE_TEXT = ALL.flatMap((file) => hueTextUses(file, readFileSync(path.join(FRONTEND, file), 'utf8')));
const TEXT_HUES = [...new Set(HUE_TEXT.map((use) => use.hue))].sort();
