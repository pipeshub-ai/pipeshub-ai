import { describe, it, expect } from 'vitest';
import { neutralizeMermaidSource } from '@/lib/utils/mermaid-source-policy';

const A = 'https://attacker.example';

describe('neutralizeMermaidSource — node data is judged as mermaid parses it', () => {
  it.each([
    ['quoted', `A@{ img: "${A}/i.png", label: "Bands" }`],
    ['unquoted', `A@{ img: ${A}/u.png, label: "Bands" }`],
    ['YAML hex escape', 'A@{ img: "\\x68ttps://attacker.example/e.png", label: "Bands" }'],
    ['YAML unicode escape', 'A@{ img: "\\u0068ttps://attacker.example/e.png", label: "Bands" }'],
    ['single-quoted', `A@{ img: '${A}/s.png', label: 'Bands' }`],
    ['protocol-relative', 'A@{ img: "//attacker.example/p.png", label: "Bands" }'],
    ['escaped key', `A@{ "\\x69mg": "${A}/k.png", label: "Bands" }`],
  ])('drops an outside img written %s, and keeps the rest', (_how, line) => {
    expect(neutralizeMermaidSource(`flowchart TD\n  ${line}\n  A --> B`)).toBe('flowchart TD\n  A@{ label: "Bands" }\n  A --> B');
  });

  it('reads multi-line node data as a block mapping', () => {
    const out = neutralizeMermaidSource(`flowchart TD\n  A@{\n    shape: rect\n    img: ${A}/ml.png\n  }\n  A --> B`);
    expect(out).toBe('flowchart TD\n  A@{ shape: "rect" }\n  A --> B');
  });

  it('keeps a data: image and the node layout values with their types', () => {
    const out = neutralizeMermaidSource('A@{ img: "data:image/png;base64,iVBOR", w: 60, h: 40, constraint: "on", animate: false }');
    expect(out).toBe('A@{ img: "data:image/png;base64,iVBOR", w: 60, h: 40, constraint: "on", animate: false }');
  });

  it('keeps an icon reference and drops an icon that is a URL', () => {
    expect(neutralizeMermaidSource('A@{ icon: "fa:user", form: "square" }')).toBe('A@{ icon: "fa:user", form: "square" }');
    expect(neutralizeMermaidSource(`A@{ icon: "${A}/i.svg", form: "square" }`)).toBe('A@{ form: "square" }');
  });

  it('makes label HTML inert after decoding it', () => {
    expect(neutralizeMermaidSource(`A@{ label: "\\x3Cimg src='${A}/x.png'\\x3E" }`)).toBe(`A@{ label: "#lt;img src='${A}/x.png'>" }`);
  });

  it('drops data it cannot read whole, so mermaid never reads it as node data', () => {
    expect(neutralizeMermaidSource(`A@{ img: ${A}/x.png, more: [1, 2] } --> B`)).toBe('A --> B');
    expect(neutralizeMermaidSource(`A@{ img: &anchor ${A}/x.png } --> B`)).toBe('A --> B');
    expect(neutralizeMermaidSource(`A@{ img: "${A}/x.png" ^ } --> B`)).toBe(`A img: "${A}/x.png" ^ } --> B`);
    expect(neutralizeMermaidSource(`A@{ img: "${A}/x.png`)).toBe(`A img: "${A}/x.png`);
  });

  it('writes strings so mermaid keeps them whole', () => {
    expect(neutralizeMermaidSource('A@{ label: "say \\"hi\\" @{x} ^" }')).toBe('A@{ label: "say \\x22hi\\x22 \\x40\\x7bx\\x7d \\x5e" }');
  });

  it('reads a line break inside quotes as mermaid does', () => {
    expect(neutralizeMermaidSource('A@{ label: "one\n   two" }')).toBe('A@{ label: "one<br/>two" }');
  });
});

describe('neutralizeMermaidSource — statements that load or link out', () => {
  it.each([
    `properties A: {"icon": "${A}/s.png"}`,
    'properties A: {"icon": "\\u0068ttps://attacker.example/j.png"}',
    `PROPERTIES A: {"icon": "${A}/s.png"}`,
    'properties A: {"ic&#111;n": "https://attacker.example/e.png"}',
    'properties A: not json',
  ])('drops an actor icon: %s', (line) => {
    expect(neutralizeMermaidSource(`sequenceDiagram\n  ${line}\n  A->>B: hi`)).toBe('sequenceDiagram\n\n  A->>B: hi');
  });

  it('keeps an actor class and nothing else of its properties', () => {
    expect(neutralizeMermaidSource(`  properties A: {"class": "db", "icon": "${A}/s.png"}`)).toBe('  properties A: {"class": "db"}');
  });

  it.each([
    'details A: someElement',
    `links A: {"Dash": "${A}/l"}`,
    `link A: Dash @ ${A}/l`,
    `click A href "${A}/c"`,
    'click A callback',
    'callback A "fn"',
  ])('drops %s', (line) => {
    expect(neutralizeMermaidSource(`  ${line}`)).toBe('');
  });

  it('drops a statement after a semicolon, but not a semicolon inside a label', () => {
    expect(neutralizeMermaidSource(`A-->B; click A href "${A}/c"; B-->C`)).toBe('A-->B; B-->C');
    expect(neutralizeMermaidSource('A["x; click y"] --> B')).toBe('A["x; click y"] --> B');
  });

  it('keeps edges from nodes that share a keyword name', () => {
    expect(neutralizeMermaidSource('details --> B\nlink --> C\nclick --> D')).toBe('details --> B\nlink --> C\nclick --> D');
  });

  it('drops a style that could fetch and keeps ordinary styles', () => {
    expect(neutralizeMermaidSource(`style A background-image:url(${A}/s.png)\nstyle B fill:#f9f,stroke:#333`)).toBe('\nstyle B fill:#f9f,stroke:#333');
    expect(neutralizeMermaidSource('classDef x fill:#f00,background:u\\72l(//attacker.example/x)')).toBe('');
  });
});

describe('neutralizeMermaidSource — configuration from the diagram', () => {
  it.each([
    `%%{init: {"themeCSS": ".n { background: url(${A}/c.png) }"}}%%`,
    `%%{init: {"fontFamily": "x; } * { background: url(${A}/f.png)"}}%%`,
    '%%{init: {"dompurifyConfig": {"ADD_TAGS": ["img"]}}}%%',
    '%%{init: {"theme\\u0043SS": "x"}}%%',
  ])('drops a directive that reaches CSS or label HTML: %s', (directive) => {
    expect(neutralizeMermaidSource(`${directive}\nflowchart TD\n  A --> B`)).toBe('\nflowchart TD\n  A --> B');
  });

  it('drops an unclosed directive, which runs to the end of the text', () => {
    expect(neutralizeMermaidSource('flowchart TD\n  A --> B\n%%{init: {"themeCSS": "x"')).toBe('flowchart TD\n  A --> B\n');
  });

  it('keeps a theme directive', () => {
    const text = "%%{init: {'theme': 'forest', 'themeVariables': {'primaryColor': '#ff0000'}}}%%\nflowchart TD\n  A --> B";
    expect(neutralizeMermaidSource(text)).toBe(text);
  });

  it('drops front matter that sets CSS, up to its last closing line, and keeps a plain title', () => {
    const risky = `---\nconfig:\n  themeCSS: ".n { background: url(${A}/fm.png) }"\n---\nflowchart TD\n  A --> B`;
    expect(neutralizeMermaidSource(risky)).toBe('flowchart TD\n  A --> B');
    const plain = '---\ntitle: Release flow\n---\nflowchart TD\n  A --> B';
    expect(neutralizeMermaidSource(plain)).toBe(plain);
  });
});

describe('neutralizeMermaidSource — label HTML', () => {
  it.each([
    [`A["<img src='${A}/l.png'>"] --> B`, `A["#lt;img src='${A}/l.png'>"] --> B`],
    [`A[<IMG SRC=${A}/l.png>] --> B`, `A[#lt;IMG SRC=${A}/l.png>] --> B`],
    [`A["<picture><source srcset='${A}/s.png'></picture>"]`, `A["#lt;picture>#lt;source srcset='${A}/s.png'>#lt;/picture>"]`],
    [`A["<span style='background:url(${A}/x)'>x</span>"]`, `A["#lt;span style='background:url(${A}/x)'>x</span>"]`],
    [`class Shape{\n  <<img src=${A}/a.png>>\n}`, `class Shape{\n  <#lt;img src=${A}/a.png>>\n}`],
  ])('makes a tag inert: %s', (input, output) => {
    expect(neutralizeMermaidSource(input)).toBe(output);
  });

  it('keeps line breaks, emphasis, annotations, arrows and comparisons', () => {
    const text = [
      'flowchart TD',
      '  A["First<br/>second<br>third<BR />"] --> B["<b>bold</b> <i>it</i> <strong>s</strong>"]',
      '  B <--> C',
      '  C -->|x < y| D',
      'classDiagram',
      '  class Shape{',
      '    <<interface>>',
      '  }',
      'sequenceDiagram',
      '  A<<->>B: both ways',
    ].join('\n');
    expect(neutralizeMermaidSource(text)).toBe(text);
  });

  it('is stable when run twice', () => {
    const text = `flowchart TD\n  A@{ img: "${A}/i.png", label: "<b>x</b><img src=y>" }\n  A["<img src=z>"] --> B\n  click A href "x"`;
    const once = neutralizeMermaidSource(text);
    expect(neutralizeMermaidSource(once)).toBe(once);
  });
});
