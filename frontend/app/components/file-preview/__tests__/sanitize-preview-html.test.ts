import { describe, it, expect, vi, afterEach } from 'vitest';
import DOMPurify from 'dompurify';
import { sanitizePreviewHtml } from '../sandboxed-html';

/**
 * Pins `sanitizePreviewHtml` against loosening. Part one checks the options it
 * hands to DOMPurify (several known advisories only matter when these are
 * enabled). Part two runs a corpus of XSS payloads through both modes and
 * checks nothing executable survives.
 */

afterEach(() => vi.restoreAllMocks());

function configFor(rich: boolean): Record<string, unknown> {
  const spy = vi.spyOn(DOMPurify, 'sanitize');
  sanitizePreviewHtml('<p>x</p>', { rich });
  return spy.mock.calls[0][1] as Record<string, unknown>;
}

describe('sanitizer config', () => {
  it.each([false, true])('does not enable options that widen the attack surface (rich=%s)', (rich) => {
    const cfg = configFor(rich);
    expect(cfg.PARSER_MEDIA_TYPE).toBeUndefined();
    expect(cfg.SAFE_FOR_TEMPLATES).toBeUndefined();
    expect(cfg.IN_PLACE).toBeUndefined();
    expect(cfg.WHOLE_DOCUMENT).toBeUndefined();
    expect(cfg.ALLOW_UNKNOWN_PROTOCOLS).toBeUndefined();
    expect(cfg.ALLOW_DATA_ATTR).toBeUndefined();
    expect(cfg.ALLOWED_URI_REGEXP).toBeUndefined();
    expect(cfg.CUSTOM_ELEMENT_HANDLING).toBeUndefined();
    expect(cfg.ADD_URI_SAFE_ATTR).toBeUndefined();
    expect(cfg.ADD_DATA_URI_TAGS).toBeUndefined();
    expect(cfg.RETURN_DOM).toBeUndefined();
    expect(cfg.RETURN_DOM_FRAGMENT).toBeUndefined();
  });

  it.each([false, true])('keeps the active-content tags forbidden (rich=%s)', (rich) => {
    const forbid = configFor(rich).FORBID_TAGS as string[];
    for (const tag of ['script', 'iframe', 'object', 'embed', 'form', 'base', 'link', 'meta']) {
      expect(forbid).toContain(tag);
    }
    const added = (configFor(rich).ADD_TAGS as string[]) ?? [];
    for (const tag of ['script', 'iframe', 'object', 'embed', 'template']) {
      expect(added).not.toContain(tag);
    }
  });

  it('registers no hooks on the shared DOMPurify instance', () => {
    sanitizePreviewHtml('<p>x</p>', { rich: true });
    // A hook would run for every sanitize call; the output of a clean input must be unchanged.
    expect(sanitizePreviewHtml('<p class="a">x</p>')).toBe('<p class="a">x</p>');
  });
});

const PAYLOADS: Record<string, string> = {
  script: '<script>alert(1)</script><p>ok</p>',
  'img onerror': '<img src=x onerror=alert(1)>',
  'on* handler (pointer)': '<img src=x onpointerenter=alert(1)>',
  'details ontoggle': '<details open ontoggle=alert(1)>x</details>',
  'javascript: href': '<a href="javascript:alert(1)">x</a>',
  'javascript: href (entity-obfuscated)': '<a href="jav&#x61;script:alert(1)">x</a>',
  'svg javascript: href': '<svg><a href="javascript:alert(1)"><text>x</text></a></svg>',
  'SMIL animate href': '<svg><a><animate attributeName="href" values="x;javascript:alert(1)" dur="0.01s" fill="freeze"/><text>x</text></a></svg>',
  'SMIL set href': '<svg><a><set attributeName="href" to="javascript:alert(1)"/><text>x</text></a></svg>',
  srcdoc: '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
  'data html link': '<a href="data:text/html,<script>alert(1)</script>">x</a>',
  'shadowroot template': '<template shadowrootmode="open"><img src=x onerror=alert(1)></template>',
  'split template expression': '<p>$<object></object>{alert(1)}</p>',
  'svg style mXSS': '<svg><style><img src=x onerror=alert(1)></style></svg>',
  'mathml mXSS': '<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>',
  'form nesting mXSS': '<form><math><mtext></form><form><mglyph><style></math><img src onerror=alert(1)>',
  'object/embed': '<object data="javascript:alert(1)"></object><embed src="javascript:alert(1)">',
  'meta refresh / base': '<meta http-equiv="refresh" content="0;url=javascript:alert(1)"><base href="javascript:alert(1)//">',
  'form action': '<form action="javascript:alert(1)"><button formaction="javascript:alert(1)">x</button></form>',
};

describe.each([false, true])('known XSS payloads come out inert (rich=%s)', (rich) => {
  it.each(Object.entries(PAYLOADS))('%s', (_name, payload) => {
    const out = sanitizePreviewHtml(payload, { rich });
    const doc = new DOMParser().parseFromString(out, 'text/html');
    const all = Array.from(doc.querySelectorAll('*'));

    expect(doc.querySelector('script, iframe, frame, object, embed, base, meta, form, link')).toBeNull();
    for (const el of all) {
      for (const attr of Array.from(el.attributes)) {
        expect(attr.name, `${el.tagName} ${attr.name}`).not.toMatch(/^on/i);
        expect(attr.name).not.toBe('srcdoc');
        expect(attr.name).not.toBe('formaction');
        if (['href', 'src', 'xlink:href', 'action'].includes(attr.name)) {
          expect(attr.value.trim()).not.toMatch(/^(javascript|vbscript|data:text\/html)/i);
        }
      }
      expect(el.getAttribute('attributeName') ?? el.getAttribute('attributename') ?? '').not.toMatch(/^(xlink:)?href$/i);
      expect(el.hasAttribute('shadowrootmode')).toBe(false);
    }
  });
});
