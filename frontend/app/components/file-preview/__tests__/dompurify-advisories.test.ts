import { describe, it, expect } from 'vitest';
import DOMPurify from 'dompurify';
import { sanitizePreviewHtml } from '../sandboxed-html';

/**
 * Regression tests for three DOMPurify advisories fixed in 3.4.17.
 *
 * The app's only sanitiser config (`sanitizePreviewHtml`) uses none of the
 * options these advisories need (`SAFE_FOR_TEMPLATES`, `IN_PLACE`, hooks,
 * `PARSER_MEDIA_TYPE`), so the first block pins that the app output stays safe
 * for each payload. The second block runs each advisory's proof of concept
 * against the installed library, so a downgrade below the patched version, or
 * a lockfile that resolves an old copy, fails here.
 */

describe('app sanitiser config against the advisory payloads', () => {
  const smil =
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60"><a id="q">' +
    '<animate attributeName="href" values="x;javascript:alert(1)" dur="0.01s" fill="freeze"/>' +
    '<text x="10" y="40">click</text></a></svg>';

  it.each([false, true])('drops SMIL attributeName=href animation (rich=%s)', (rich) => {
    const out = sanitizePreviewHtml(smil, { rich });
    expect(out).not.toMatch(/javascript:/i);
    expect(out).not.toMatch(/attributename\s*=\s*"href"/i);
  });

  it.each([false, true])('leaves split ${...} text inert: no executable element or attribute, no shadow root (rich=%s)', (rich) => {
    const out = sanitizePreviewHtml(
      '<p>$<object></object>{alert(1)}</p><img src="x" onerror="alert(1)"><template shadowrootmode="open">$<object></object>{alert(1)}</template>',
      { rich },
    );
    // The app does not strip template text (it never evaluates it); the
    // guarantee is that nothing executable or shadow-rooted survives.
    expect(out).not.toMatch(/\son[a-z]+\s*=/i);
    expect(out).not.toMatch(/javascript:/i);
    expect(out).not.toMatch(/<(script|object|embed|iframe)/i);
    expect(out).not.toMatch(/shadowroot/i);
  });
});

describe('installed DOMPurify includes the advisory fixes', () => {
  it('GHSA-c6gr-qvv4-5v3f: removes SMIL attributeName=href in application/xhtml+xml mode', () => {
    const dirty =
      '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60"><a id="q">' +
      '<animate attributeName="href" values="x;javascript:alert(1)" dur="0.01s" fill="freeze"/>' +
      '<text x="10" y="40">click</text></a></svg>';
    const clean = DOMPurify.sanitize(dirty, {
      PARSER_MEDIA_TYPE: 'application/xhtml+xml',
      ALLOWED_TAGS: ['svg', 'a', 'animate', 'text'],
      ALLOWED_ATTR: ['attributeName', 'values', 'dur', 'fill', 'x', 'y', 'id', 'width', 'height'],
    });
    expect(clean).not.toMatch(/attributeName/);
  });

  it('GHSA-cg2g-xh64-v4q7: re-scrubs template expressions split inside a shadow root', () => {
    const host = document.createElement('div');
    const root = host.attachShadow({ mode: 'open' });
    root.appendChild(document.createTextNode('$'));
    root.appendChild(document.createElement('object'));
    root.appendChild(document.createTextNode('{alert(document.domain)}'));

    DOMPurify.sanitize(host, { IN_PLACE: true, SAFE_FOR_TEMPLATES: true });

    host.shadowRoot!.normalize();
    expect(host.shadowRoot!.textContent).not.toContain('${');
    expect(host.shadowRoot!.textContent).not.toContain('alert(document.domain)');
  });

  it.each(['parent', 'nextSibling'] as const)(
    'GHSA-mrv2-3pqr-j5jr: IN_PLACE hook that removes the %s still neutralises the detached subtree',
    (mode) => {
      const purify = DOMPurify(window);
      document.body.innerHTML =
        '<div id="root"><figure><img class="bad" src="a.png">' +
        '<img id="evil" src="x" onerror="alert(1)"></figure><p>ok</p></div>';
      const root = document.getElementById('root')!;
      const evil = document.getElementById('evil')!;

      purify.addHook('afterSanitizeAttributes', (node) => {
        if (node.getAttribute && node.getAttribute('class') === 'bad') {
          if (mode === 'parent') node.parentNode!.parentNode!.removeChild(node.parentNode!);
          else node.nextElementSibling!.remove();
        }
      });
      purify.sanitize(root, { IN_PLACE: true });

      expect(evil.getAttribute('onerror')).toBeNull();
    },
  );
});
