'use client';

import React, { useCallback, useEffect, useId, useMemo, useState } from 'react';
import { Box, Dialog, Flex, IconButton, Spinner, Text, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';

// ─── Constants ────────────────────────────────────────────────────────────────

const MIN_ZOOM = 25;
const MAX_ZOOM = 400;
const ZOOM_STEP = 25;
/** 100% = SVG fills the full width of the dialog content area. */
const DEFAULT_ZOOM = 100;
const COPY_FEEDBACK_MS = 2000;

// ─── Dark-mode detection ──────────────────────────────────────────────────────

/**
 * Returns true when the page is in dark mode.
 *
 * Checks (in priority order):
 *  1. `class="dark"` on <html> or <body>  (Tailwind / most frameworks)
 *  2. `data-appearance="dark"` on <html>  (Radix Themes)
 *  3. `data-theme="dark"` on <html>       (other design systems)
 *  4. `prefers-color-scheme: dark`        (system preference fallback)
 *
 * A MutationObserver keeps the value live when the user toggles the theme.
 */
function readDarkMode(): boolean {
  if (typeof window === 'undefined') return false;
  const el = document.documentElement;
  return (
    el.classList.contains('dark') ||
    document.body.classList.contains('dark') ||
    el.getAttribute('data-appearance') === 'dark' ||
    el.getAttribute('data-theme') === 'dark' ||
    window.matchMedia('(prefers-color-scheme: dark)').matches
  );
}

function useDarkMode(): boolean {
  const [dark, setDark] = useState(readDarkMode);

  useEffect(() => {
    const update = () => setDark(readDarkMode());

    const mo = new MutationObserver(update);
    mo.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class', 'data-appearance', 'data-theme'],
    });
    mo.observe(document.body, { attributes: true, attributeFilter: ['class'] });

    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    mq.addEventListener('change', update);

    return () => {
      mo.disconnect();
      mq.removeEventListener('change', update);
    };
  }, []);

  return dark;
}

// ─── Mermaid initialisation (singleton) ─────────────────────────────────────

/**
 * Always use mermaid's 'default' (light) theme so that node fills, borders,
 * and text colours render correctly. Arrow/line visibility in dark mode is
 * handled by giving the diagram container a fixed white background (below).
 */
let mermaidInitPromise: Promise<void> | null = null;

function ensureInit(): Promise<void> {
  if (mermaidInitPromise) return mermaidInitPromise;

  mermaidInitPromise = import('mermaid').then(({ default: mermaid }) => {
    mermaid.initialize({
      startOnLoad: false,
      theme: 'default',
      securityLevel: 'antiscript',
      fontFamily: 'inherit',
    });
  });

  return mermaidInitPromise;
}

/** How long (ms) to wait after the last `chart` change before rendering.
 *  During SSE streaming this keeps the spinner visible and avoids repeated
 *  render attempts on every incoming token chunk. */
const RENDER_DEBOUNCE_MS = 400;

// ─── Mermaid source sanitiser ────────────────────────────────────────────────

/**
 * Determines whether a trimmed line is a dangling arrow definition — it ends
 * with an arrow operator (and optional pipe-label) but has NO target node.
 *
 * Design notes:
 * - Uses bracket/quote depth tracking so node labels like A["text-->"] are
 *   never mistakenly flagged: if we're still inside a bracket or string at
 *   the end of the line, the trailing chars are part of a label, not an arrow.
 * - Checks an explicit list of every mermaid arrow type (flowchart + sequence)
 *   rather than a single catch-all regex, which avoids false positives on
 *   valid target node names that happen to end in '-', 'o', 'x', etc.
 * - Mermaid comment lines (%%) are always kept as-is.
 */
function hasDanglingArrow(t: string): boolean {
  if (!t || t.startsWith('%%')) return false;

  // Walk the line tracking bracket / quote depth so we can tell if the final
  // chars are inside a node label or a bare arrow expression.
  let inDq = false;    // inside double-quoted string
  let inSq = false;    // inside single-quoted string
  let depth = 0;       // net open-bracket depth

  for (let i = 0; i < t.length; i++) {
    const ch = t[i];
    if (!inSq && ch === '"') { inDq = !inDq; continue; }
    if (!inDq && ch === "'") { inSq = !inSq; continue; }
    if (inDq || inSq) continue;
    if (ch === '[' || ch === '(' || ch === '{') depth++;
    else if (ch === ']' || ch === ')' || ch === '}') depth = Math.max(0, depth - 1);
  }

  // Ended inside a string or unclosed bracket → truncated label, not an arrow
  if (inDq || inSq || depth > 0) return false;

  // Optional pipe-label suffix that can follow any arrow: -->|text| or just -->
  const P = '(?:\\|[^|]*\\|)?\\s*$';

  // Test against every documented mermaid arrow ending.
  // Order: most specific first to avoid partial-match ambiguity.
  return (
    // ── Flowchart (graph / flowchart) ───────────────────────────────────────
    new RegExp(`-->${P}`).test(t)         ||  // -->   standard arrow
    new RegExp(`---${P}`).test(t)         ||  // ---   open link (no arrowhead)
    new RegExp(`-\\.->(?:\\|[^|]*\\|)?\\s*$`).test(t)  ||  // -.->  dotted arrow
    new RegExp(`-\\.-(?:\\|[^|]*\\|)?\\s*$`).test(t)   ||  // -.-   dotted link
    new RegExp(`==>${P}`).test(t)         ||  // ==>   thick arrow
    new RegExp(`===${P}`).test(t)         ||  // ===   thick link
    new RegExp(`--[ox]${P}`).test(t)      ||  // --o / --x   circle / cross end
    new RegExp(`o--o${P}`).test(t)        ||  // o--o  both-circle
    new RegExp(`x--x${P}`).test(t)        ||  // x--x  both-cross
    new RegExp(`<-->${P}`).test(t)        ||  // <-->  bidirectional
    new RegExp(`<--${P}`).test(t)         ||  // <--   reversed arrow
    // ── Sequence diagram ───────────────────────────────────────────────────
    new RegExp(`->>${P}`).test(t)         ||  // ->>   async
    new RegExp(`-->>${P}`).test(t)        ||  // -->>  dotted async
    new RegExp(`->${P}`).test(t)          ||  // ->    sync (sequence)
    new RegExp(`--${P}`).test(t)          ||  // --    dotted sync (sequence)
    // ── Trailing pipe-label with no target  e.g. A -->|label|  ────────────
    /\|[^|]*\|\s*$/.test(t)
  );
}

/**
 * Keywords that open a depth-tracked block requiring a matching `end`.
 * Covers flowchart `subgraph` and all sequence-diagram block types.
 */
const BLOCK_OPENER = /^(subgraph|loop|alt|opt|par|critical|break|rect)\b/;

/** Matches the start of a flowchart/graph edge expression. */
const EDGE_START = /^(\w+)\s*(?:-->|---|-\.->|-\.-|==>|===|--[ox]|o--o|x--x|<-->|<--)/;

/** Extract the final node ID from a simple `Source -->|lbl| Target` edge line. */
const EDGE_TARGET = /(?:-->|---|-\.->|-\.-|==>|===|--[ox]|o--o|x--x|<-->|<--)(?:\|[^|]*\|)?\s*(\w+)\s*$/;

/**
 * Proactive normalisations that are ALWAYS applied before sending source to
 * mermaid — even when the source is syntactically valid.
 *
 * These target known mermaid quirks that produce visually broken diagrams
 * without causing a render failure (so a fallback sanitiser would never see
 * them):
 *
 *  1. \n inside subgraph title strings — mermaid renders the escape as a
 *     line-break in the subgraph header bar, but the header container does
 *     not auto-size, so the second line overflows below the box border.
 *     Node labels handle \n correctly; only subgraph headers are affected.
 *
 *  2. Self-referential subgraph edges — e.g. `Bash -->|lbl| CoreTools` where
 *     Bash is declared *inside* the CoreTools subgraph. Drawing an edge from a
 *     child node back to its own parent confuses mermaid's layout engine and
 *     produces half-rendered / misaligned subgraphs.
 */
function normalizeMermaid(source: string): string {
  // Fix 1: collapse \n (literal backslash-n) to a space in subgraph title lines
  let result = source.replace(
    /^\s*subgraph\b.*/mg,
    (line) => line.replace(/\\n/g, ' '),
  );

  // Fix 2: remove self-referential subgraph edges
  // Step 2a — build nodeId → enclosing-subgraph map
  const subgraphIds    = new Set<string>();
  const nodeToSubgraph = new Map<string, string>();
  let depth = 0;
  let currentSg: string | null = null;

  for (const line of result.split('\n')) {
    const t = line.trim();
    const sgOpen = t.match(/^subgraph\s+(\w+)/);
    if (sgOpen) {
      if (++depth === 1) {
        currentSg = sgOpen[1];
        subgraphIds.add(currentSg);
      }
    } else if (/^end\b/.test(t) && depth > 0) {
      if (--depth === 0) currentSg = null;
    } else if (currentSg && !EDGE_START.test(t)) {
      const m = t.match(/^(\w+)\s*(?:[\[({]|$)/);
      if (m && m[1] !== 'style' && m[1] !== 'classDef' && m[1] !== 'class') {
        nodeToSubgraph.set(m[1], currentSg);
      }
    }
  }

  // Step 2b — filter out any edge whose source is inside its own target subgraph
  if (subgraphIds.size > 0 && nodeToSubgraph.size > 0) {
    result = result.split('\n').filter((line) => {
      const t = line.trim();
      if (!EDGE_START.test(t)) return true;
      const srcM = t.match(EDGE_START);
      const tgtM = t.match(EDGE_TARGET);
      if (!srcM || !tgtM) return true;
      const srcSg = nodeToSubgraph.get(srcM[1]);
      return !(srcSg && tgtM[1] === srcSg);
    }).join('\n');
  }

  // Fix 3: Strip markdown link syntax [text](url) from inside node labels.
  // LLMs frequently embed inline source citations in node labels, e.g.:
  //   note1[Description [source](https://example.com)]
  // Nested brackets are invalid in mermaid node-label syntax and cause a
  // hard parse failure. Replace each [text](http…) with just the link text.
  result = result.replace(/\[([^\[\]]*)\]\(https?:\/\/[^)]*\)/g, '$1');
  
  // Fix 4: Strip '@' from inside node label brackets.
  // The mermaid flowchart lexer's TEXT token pattern is:
  //   /[A-Za-z0-9!"#$%&'*+.`?\_/]+/
  // '@' is NOT in that character class. A bare '@' inside a label (e.g.
  // GH[@claude on GitHub]) causes a hard lexer failure. Note that the
  // '@{...}' shape-metadata syntax is NOT inside brackets, so stripping '@'
  // from inside '[...]' is safe and does not break any valid mermaid feature.
  result = result.replace(/\[([^\]]*)\]/g, (match, content) =>
    content.includes('@') ? `[${content.replace(/@/g, '')}]` : match,
  );

  return result;
}

/**
 * Error-recovery repair for truncated / syntactically broken mermaid source.
 * Only called when the normalised source fails to render.
 *
 *  1. Remove lines with dangling arrows (arrow operator with no target node).
 *  2. Close any unclosed block statements (subgraph / loop / alt / opt / …).
 */
function sanitizeMermaid(source: string): { result: string; changed: boolean } {
  const lines = source.split('\n');
  const out: string[] = [];
  let depth = 0;
  let changed = false;

  for (const line of lines) {
    const t = line.trim();

    if (hasDanglingArrow(t)) {
      changed = true;
      continue;
    }

    if (BLOCK_OPENER.test(t))            depth++;
    else if (/^end\b/.test(t) && depth > 0) depth--;

    out.push(line);
  }

  for (let i = 0; i < depth; i++) {
    out.push('end');
    changed = true;
  }

  return { result: out.join('\n').trim(), changed };
}

// ─── Dark-mode overlay ────────────────────────────────────────────────────────

/**
 * A light blue-gray for edges/arrows — visible against --slate-2 without
 * clashing with default-theme node fills.
 */
const DARK_EDGE_COLOR = '#9baab8';

/**
 * Light slate for label/message text on dark backgrounds.
 * Slightly warmer than pure white so it doesn't feel harsh.
 */
const DARK_TEXT_COLOR = '#e2e8f0';

/**
 * Inject a <style> block into a rendered mermaid SVG string so the diagram
 * is legible on dark backgrounds (Radix --slate-2 / similar).
 *
 * Two classes of problem are fixed here:
 *
 * A. Lines & arrowheads — mermaid's default theme uses dark strokes that
 *    disappear on dark containers.  We override to DARK_EDGE_COLOR.
 *
 * B. Label text — message labels, edge labels, loop/alt descriptions, and
 *    sequence numbers are rendered with dark `fill` values that become
 *    invisible on a dark background.  We override to DARK_TEXT_COLOR.
 *    Node body text (inside filled rectangles) is intentionally NOT touched
 *    because the node has its own light background and the default dark text
 *    remains readable.
 *
 * Selectors cover every diagram type shipped with mermaid v11:
 *   - Flowchart / graph: .edgePath .path, .flowchart-link, .edgeLabel
 *   - Sequence:          .messageLine*, .messageText, .actor-line, .loopLine,
 *                        .loopText, .labelText, .sequenceNumber
 *   - Class / ER / git:  .relation line, .commit-bullets line
 *   - All arrowheads:    defs marker path
 */
function injectDarkEdgeStyles(svgString: string): string {
  if (!svgString) return svgString;

  const overrides = [
    /* ── A. Lines & arrows ─────────────────────────────────────────────── */

    /* Flowchart edge paths */
    `.edgePath .path { stroke: ${DARK_EDGE_COLOR} !important; fill: none !important; }`,
    `.flowchart-link { stroke: ${DARK_EDGE_COLOR} !important; fill: none !important; }`,

    /* Sequence message lines (solid + dashed variants) */
    `.messageLine0, .messageLine1 { stroke: ${DARK_EDGE_COLOR} !important; }`,

    /* Sequence vertical actor-lifeline + loop/alt/opt box borders */
    `.actor-line, .loopLine { stroke: ${DARK_EDGE_COLOR} !important; }`,

    /* Class / ER / git edge lines */
    `.relation line, .commit-bullets line { stroke: ${DARK_EDGE_COLOR} !important; }`,

    /* Arrowhead markers — overrides the inline fill attribute */
    `defs marker path { fill: ${DARK_EDGE_COLOR} !important; stroke: none !important; }`,

    /* ── B. Label & message text ────────────────────────────────────────── */

    /* Sequence diagram: text on message arrows (the most common complaint) */
    `.messageText { fill: ${DARK_TEXT_COLOR} !important; stroke: none !important; font-weight: 500 !important; }`,

    /* Sequence diagram: loop/alt/opt/par/critical description text */
    `.loopText, .loopText > tspan { fill: ${DARK_TEXT_COLOR} !important; stroke: none !important; }`,

    /* Sequence diagram: the "loop" / "alt" / "opt" keyword badge text */
    `.labelText, .labelText > tspan { fill: ${DARK_TEXT_COLOR} !important; stroke: none !important; }`,

    /* Sequence diagram: auto-numbered sequence labels */
    `.sequenceNumber { fill: ${DARK_TEXT_COLOR} !important; }`,

    /* Flowchart: edge labels rendered inside a <foreignObject> */
    `.edgeLabel .label { color: ${DARK_TEXT_COLOR} !important; }`,
    `.edgeLabel foreignObject { color: ${DARK_TEXT_COLOR} !important; }`,

    /* Flowchart edge label background — keep transparent so container shows through */
    `.edgeLabel .label rect { fill: transparent !important; }`,
  ].join(' ');

  const styleBlock = `<style id="ph-dark-edges">${overrides}</style>`;

  // Insert immediately after the opening <svg> tag
  return svgString.replace(/(<svg\b[^>]*>)/, `$1${styleBlock}`);
}

// ─── SVG helpers ─────────────────────────────────────────────────────────────

/**
 * Remove the fixed pixel width/height mermaid bakes into every SVG so the
 * element scales with its container (aspect ratio preserved via viewBox).
 *
 * `fillWidth = true`  (fullscreen) — SVG always fills 100 % of container width.
 * `fillWidth = false` (inline)    — SVG renders at its natural viewBox size but
 *                                   never overflows: max-width caps it at 100 %.
 *                                   Small diagrams stay compact; wide ones shrink.
 */
function makeSvgResponsive(svgString: string, fillWidth = true): string {
  try {
    const parser = new DOMParser();
    const doc = parser.parseFromString(svgString, 'image/svg+xml');
    if (doc.querySelector('parsererror')) return svgString;
    const svg = doc.documentElement;
    svg.removeAttribute('width');
    svg.removeAttribute('height');
    if (fillWidth) {
      // Fullscreen: always stretch to 100% of dialog width
      svg.style.width = '100%';
      svg.style.maxWidth = 'none';
    } else {
      // Inline: render at natural size; only shrink if wider than container
      svg.style.maxWidth = '100%';
    }
    svg.style.height = 'auto';
    return new XMLSerializer().serializeToString(svg);
  } catch {
    return svgString;
  }
}

/**
 * Read the natural diagram dimensions from viewBox (or width/height attrs).
 * Returns 2× values so the exported PNG is retina-quality.
 */
function parseSvgDimensions(svgString: string): { width: number; height: number } {
  try {
    const parser = new DOMParser();
    const doc = parser.parseFromString(svgString, 'image/svg+xml');
    const svg = doc.documentElement;

    const vb = svg.getAttribute('viewBox');
    if (vb) {
      const parts = vb.trim().split(/[\s,]+/).map(Number);
      if (parts.length >= 4 && parts[2] > 0 && parts[3] > 0) {
        return { width: Math.ceil(parts[2]) * 2, height: Math.ceil(parts[3]) * 2 };
      }
    }
    const w = parseFloat(svg.getAttribute('width') ?? '');
    const h = parseFloat(svg.getAttribute('height') ?? '');
    if (w > 0 && h > 0) return { width: Math.ceil(w) * 2, height: Math.ceil(h) * 2 };
  } catch { /* ignore */ }
  return { width: 1600, height: 1200 };
}

/**
 * Convert a mermaid SVG string → PNG Blob via an off-screen canvas.
 *
 * Key implementation notes:
 * - Uses a `data:` URL (not a blob URL) so the canvas is never tainted by
 *   cross-origin concerns, making `toBlob()` reliable.
 * - Inserts a white rect before drawing so the PNG is opaque on any surface.
 * - Awaits `document.fonts.ready` so text renders with correct metrics.
 */
async function svgToPngBlob(svgString: string): Promise<Blob> {
  const { width, height } = parseSvgDimensions(svgString);

  const parser = new DOMParser();
  const doc = parser.parseFromString(svgString, 'image/svg+xml');
  const svgEl = doc.documentElement;
  svgEl.setAttribute('width', String(width));
  svgEl.setAttribute('height', String(height));
  svgEl.style.maxWidth = 'none';

  // Prepend white background so the PNG is not transparent
  const ns = 'http://www.w3.org/2000/svg';
  const bg = doc.createElementNS(ns, 'rect');
  bg.setAttribute('width', '100%');
  bg.setAttribute('height', '100%');
  bg.setAttribute('fill', 'white');
  svgEl.insertBefore(bg, svgEl.firstChild);

  const serialized = new XMLSerializer().serializeToString(svgEl);
  // encodeURIComponent handles Unicode and special chars; avoids blob URL taint.
  const dataUrl = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(serialized)}`;

  await document.fonts.ready;

  const img = await new Promise<HTMLImageElement>((resolve, reject) => {
    const el = new Image();
    el.onload = () => resolve(el);
    el.onerror = () => reject(new Error('SVG → image load failed'));
    el.src = dataUrl;
  });

  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Canvas 2D context not available');
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, width, height);
  ctx.drawImage(img, 0, 0, width, height);

  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error('canvas.toBlob() returned null'))),
      'image/png',
      1.0,
    );
  });
}

/**
 * Trigger a PNG download as a fallback when the Clipboard API is unavailable.
 */
function downloadPng(blob: Blob, filename = 'diagram.png') {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ─── Small layout helpers ─────────────────────────────────────────────────────

function ToolbarSep() {
  return (
    <Box
      style={{ width: '1px', height: '14px', backgroundColor: 'var(--slate-6)', flexShrink: 0 }}
    />
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

/** null = idle, true = success, false = failure */
type CopyFeedback = boolean | null;

interface MermaidDiagramProps {
  chart: string;
}

export function MermaidDiagram({ chart }: MermaidDiagramProps) {
  const uid = useId().replace(/:/g, '');
  const containerId = `mermaid-${uid}`;

  // Drives dark-edge overlay and container styling
  const isDark = useDarkMode();

  const [svgContent, setSvgContent]       = useState<string | null>(null);
  const [responsiveSvg, setResponsiveSvg] = useState<string | null>(null);
  const [error, setError]                 = useState<string | null>(null);
  /** Whether the error banner is showing the raw source code. */
  const [showErrorSource, setShowErrorSource] = useState(false);
  const [open, setOpen]                   = useState(false);
  const [zoom, setZoom]                   = useState(DEFAULT_ZOOM);
  const [copyFeedback, setCopyFeedback]   = useState<CopyFeedback>(null);
  const { t } = useTranslation();

  // Inline view: max-width variant — natural size for compact diagrams,
  // shrinks to fit container for wide ones (no horizontal scroll).
  const inlineSvg = useMemo(
    () => (svgContent ? makeSvgResponsive(svgContent, false) : null),
    [svgContent],
  );

  // In dark mode, inject light-coloured edge/arrow/text styles so the diagram
  // is legible on a dark background. Recomputed on theme change.
  const displayInlineSvg     = isDark && inlineSvg    ? injectDarkEdgeStyles(inlineSvg)    : inlineSvg;
  const displayFullscreenSvg = isDark && responsiveSvg ? injectDarkEdgeStyles(responsiveSvg) : responsiveSvg;

  // ── Render pipeline ──────────────────────────────────────────────────────────
  //
  // Step 1 (always): normalizeMermaid() — proactive visual fixes applied even
  //   on syntactically valid source (subgraph title \n, self-referential edges).
  // Step 2 (on failure): sanitizeMermaid() — error-recovery for truncated /
  //   broken source (dangling arrows, unclosed blocks).
  //
  // Debounced so rapid chart changes during SSE streaming don't trigger
  // repeated render attempts; the spinner stays until the source stabilises.
  useEffect(() => {
    let cancelled = false;
    setSvgContent(null);
    setResponsiveSvg(null);
    setError(null);
    setShowErrorSource(false);

    async function tryRender() {
      await ensureInit();
      const { default: mermaid } = await import('mermaid');

      // Always normalise first — fixes visual bugs in otherwise-valid source
      const normalised = normalizeMermaid(chart);

      // Pass 1 — normalised source
      try {
        const r = await mermaid.render(containerId, normalised);
        if (!cancelled) {
          setSvgContent(r.svg);
          setResponsiveSvg(makeSvgResponsive(r.svg));
        }
        return;
      } catch {
        // mermaid may leave a stale hidden element in the DOM on failure
        document.getElementById(containerId)?.remove();
        document.getElementById(`d${containerId}`)?.remove();
      }

      // Pass 2 — error recovery: remove dangling arrows + close open blocks
      const { result: sanitized, changed } = sanitizeMermaid(normalised);
      if (changed) {
        try {
          const r = await mermaid.render(`${containerId}s`, sanitized);
          if (!cancelled) {
            setSvgContent(r.svg);
            setResponsiveSvg(makeSvgResponsive(r.svg));
          }
          return;
        } catch {
          document.getElementById(`${containerId}s`)?.remove();
          document.getElementById(`d${containerId}s`)?.remove();
        }
      }

      if (!cancelled) setError('render-failed');
    }

    const timer = setTimeout(() => {
      if (!cancelled) tryRender();
    }, RENDER_DEBOUNCE_MS);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [chart]);

  // ── Reset zoom when dialog opens ────────────────────────────────────────────
  useEffect(() => {
    if (open) setZoom(DEFAULT_ZOOM);
  }, [open]);

  // ── Zoom ────────────────────────────────────────────────────────────────────
  const zoomIn    = useCallback(() => setZoom((z) => Math.min(z + ZOOM_STEP, MAX_ZOOM)), []);
  const zoomOut   = useCallback(() => setZoom((z) => Math.max(z - ZOOM_STEP, MIN_ZOOM)), []);
  const zoomReset = useCallback(() => setZoom(DEFAULT_ZOOM), []);

  // ── Copy feedback helper ────────────────────────────────────────────────────
  const flash = useCallback((ok: boolean) => {
    setCopyFeedback(ok);
    setTimeout(() => setCopyFeedback(null), COPY_FEEDBACK_MS);
  }, []);

  // ── Copy as image (PNG → clipboard, fallback to download) ───────────────────
  const handleCopyImage = useCallback(async () => {
    if (!svgContent) return;
    let blob: Blob;
    try {
      blob = await svgToPngBlob(svgContent);
    } catch {
      flash(false);
      return;
    }

    // Prefer clipboard API; fall back to download so the action never silently fails
    const clipboardAvailable =
      typeof ClipboardItem !== 'undefined' && !!navigator.clipboard?.write;

    if (clipboardAvailable) {
      try {
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
        flash(true);
        return;
      } catch {
        // Clipboard API blocked (e.g. Firefox permission) — fall through to download
      }
    }

    // Fallback: download as PNG
    downloadPng(blob);
    flash(true);
  }, [svgContent, flash]);

  // ── Copy image button (shared between inline toolbar and fullscreen header) ──
  const copyImageBtn = svgContent ? (
    <IconButton
      key="copy-img"
      size="1"
      variant="ghost"
      color={copyFeedback === true ? 'green' : copyFeedback === false ? 'red' : 'gray'}
      title={t('chat.copyDiagramImage')}
      style={{ cursor: 'pointer' }}
      onClick={handleCopyImage}
    >
      <MaterialIcon
        name={copyFeedback === true ? 'check' : copyFeedback === false ? 'close' : 'content_copy'}
        size={ICON_SIZES.SECONDARY}
      />
    </IconButton>
  ) : null;

  // ── Copy source (used only in the error banner — not in normal toolbar) ──────
  const [errorCopyFeedback, setErrorCopyFeedback] = useState<boolean | null>(null);
  const handleCopySource = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(chart);
      setErrorCopyFeedback(true);
    } catch {
      setErrorCopyFeedback(false);
    }
    setTimeout(() => setErrorCopyFeedback(null), COPY_FEEDBACK_MS);
  }, [chart]);

  // ── Error UI ─────────────────────────────────────────────────────────────────
  if (error) {
    return (
      <Box
        style={{
          marginBottom: 'var(--space-3)',
          border: '1px solid var(--slate-5)',
          borderRadius: 'var(--radius-2)',
          overflow: 'hidden',
        }}
      >
        {/* Error banner row */}
        <Flex
          align="center"
          gap="2"
          style={{
            padding: 'var(--space-2) var(--space-3)',
            backgroundColor: 'var(--amber-2)',
            borderBottom: showErrorSource ? '1px solid var(--slate-5)' : undefined,
          }}
        >
          <MaterialIcon name="warning_amber" size={ICON_SIZES.SECONDARY} style={{ color: 'var(--amber-9)', flexShrink: 0 }} />
          <Text size="1" style={{ color: 'var(--slate-11)', flex: 1 }}>
            {t('chat.diagramRenderFailed')}
          </Text>

          {/* Toggle source view */}
          <IconButton
            size="1"
            variant="ghost"
            color={showErrorSource ? 'amber' : 'gray'}
            title={showErrorSource ? t('chat.hideDiagramSource') : t('chat.showDiagramSource')}
            style={{ cursor: 'pointer' }}
            onClick={() => setShowErrorSource((v) => !v)}
          >
            <MaterialIcon name="code" size={ICON_SIZES.SECONDARY} />
          </IconButton>

          {/* Copy source — only in error banner */}
          <IconButton
            size="1"
            variant="ghost"
            color={errorCopyFeedback === true ? 'green' : errorCopyFeedback === false ? 'red' : 'gray'}
            title={t('chat.copyDiagramSource')}
            style={{ cursor: 'pointer' }}
            onClick={handleCopySource}
          >
            <MaterialIcon
              name={errorCopyFeedback === true ? 'check' : errorCopyFeedback === false ? 'close' : 'content_copy'}
              size={ICON_SIZES.SECONDARY}
            />
          </IconButton>
        </Flex>

        {/* Collapsible source view */}
        {showErrorSource && (
          <pre
            className="no-scrollbar"
            style={{
              margin: 0,
              padding: 'var(--space-3)',
              backgroundColor: 'var(--slate-2)',
              overflowX: 'auto',
              fontSize: 'var(--font-size-1)',
              lineHeight: 1.6,
              color: 'var(--slate-11)',
            }}
          >
            {chart}
          </pre>
        )}
      </Box>
    );
  }

  // ── Fullscreen zoom: 100% means "SVG fills the dialog width"
  //    We achieve this by:
  //    1. Using a responsive SVG (width:100%) inside a sizing div
  //    2. Setting that div's width to `zoom%` of the scroll container
  //    3. Below 100% the div is centered via margin:auto; above 100% it overflows & scrolls
  const fullscreenSvgWrapperStyle: React.CSSProperties = {
    width: `${zoom}%`,
    // Below 100%: center in the container
    ...(zoom < DEFAULT_ZOOM ? { margin: '0 auto' } : {}),
  };

  return (
    <Box style={{ marginBottom: 'var(--space-3)' }}>

      {/* ── Inline toolbar — only shown once the SVG is ready ──────── */}
      {inlineSvg && (
        <Flex justify="end" align="center" gap="1" style={{ marginBottom: '4px' }}>
          {copyImageBtn}
          <ToolbarSep />
          <IconButton
            size="1"
            variant="ghost"
            color="gray"
            title={t('chat.expandDiagram')}
            style={{ cursor: 'pointer', color: 'var(--slate-9)' }}
            onClick={() => setOpen(true)}
          >
            <MaterialIcon name="open_in_full" size={ICON_SIZES.SECONDARY} />
          </IconButton>
        </Flex>
      )}

      {/* ── Inline diagram ──────────────────────────────────────────── */}
      <Box
        className="no-scrollbar"
        style={{
          backgroundColor: 'var(--slate-2)',
          borderRadius: 'var(--radius-2)',
          padding: 'var(--space-3)',
          // Cap height so tall diagrams don't dominate the message list;
          // the expand button is the path to the full view.
          maxHeight: inlineSvg ? '520px' : undefined,
          overflow: 'auto',
          minHeight: inlineSvg ? undefined : '90px',
          display: 'flex',
          justifyContent: 'center',
          alignItems: inlineSvg ? 'flex-start' : 'center',
        }}
      >
        {inlineSvg ? (
          <div dangerouslySetInnerHTML={{ __html: displayInlineSvg ?? '' }} style={{ width: '100%' }} />
        ) : (
          <Flex align="center" gap="2">
            <Spinner size="2" />
            <Text size="1" style={{ color: 'var(--slate-9)' }}>
              {t('chat.renderingDiagram')}
            </Text>
          </Flex>
        )}
      </Box>

      {/* ── Fullscreen dialog ────────────────────────────────────────── */}
      <Dialog.Root open={open} onOpenChange={setOpen}>
        {open && (
          <Box
            style={{
              position: 'fixed', inset: 0,
              backgroundColor: 'rgba(28, 32, 36, 0.5)',
              zIndex: 999, cursor: 'pointer',
            }}
            onClick={() => setOpen(false)}
          />
        )}

        <Dialog.Content
          style={{
            maxWidth: '92vw', width: '92vw',
            maxHeight: '88vh', height: '88vh',
            padding: 0, overflow: 'hidden',
            display: 'flex', flexDirection: 'column',
            zIndex: 1000,
          }}
        >
          <VisuallyHidden>
            <Dialog.Title>{t('chat.fullDiagramView')}</Dialog.Title>
          </VisuallyHidden>

          {/* ── Header ────────────────────────────────────────────── */}
          <Flex
            align="center"
            justify="between"
            style={{
              padding: 'var(--space-3) var(--space-4)',
              borderBottom: '1px solid var(--slate-6)',
              flexShrink: 0,
            }}
          >
            <Text size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
              {t('chat.fullDiagramView')}
            </Text>

            <Flex align="center" gap="1">
              {/* Zoom controls */}
              <IconButton
                size="1" variant="ghost" color="gray"
                title={t('chat.zoomOut')}
                disabled={zoom <= MIN_ZOOM}
                style={{ cursor: zoom <= MIN_ZOOM ? 'default' : 'pointer' }}
                onClick={zoomOut}
              >
                <MaterialIcon name="remove" size={ICON_SIZES.SECONDARY} />
              </IconButton>

              <Text
                size="1"
                title={t('chat.resetZoom')}
                onClick={zoomReset}
                style={{
                  color: 'var(--slate-11)',
                  minWidth: '42px',
                  textAlign: 'center',
                  fontVariantNumeric: 'tabular-nums',
                  cursor: 'pointer',
                  userSelect: 'none',
                }}
              >
                {zoom}%
              </Text>

              <IconButton
                size="1" variant="ghost" color="gray"
                title={t('chat.zoomIn')}
                disabled={zoom >= MAX_ZOOM}
                style={{ cursor: zoom >= MAX_ZOOM ? 'default' : 'pointer' }}
                onClick={zoomIn}
              >
                <MaterialIcon name="add" size={ICON_SIZES.SECONDARY} />
              </IconButton>

              <ToolbarSep />

              {/* Copy image */}
              {copyImageBtn}

              <ToolbarSep />

              {/* Close */}
              <Dialog.Close>
                <IconButton size="1" variant="ghost" color="gray" style={{ cursor: 'pointer' }}>
                  <MaterialIcon name="close" size={ICON_SIZES.SECONDARY} />
                </IconButton>
              </Dialog.Close>
            </Flex>
          </Flex>

          {/*
           * Scroll container.
           *
           * 100% zoom = SVG fills the dialog width.
           * The responsive SVG (width:100% CSS) fills its wrapper div.
           * The wrapper div's width is `zoom%` of this scroll container.
           * At zoom > 100% the content overflows and the container scrolls.
           * At zoom < 100% the wrapper is narrower than the container and
           * centered via margin:auto.
           *
           * This is simpler and more predictable than the CSS `zoom` property,
           * which changes the element's pixel size without triggering reflow,
           * causing scroll containers to report stale overflow dimensions.
           */}
          <Box
            style={{
              flex: 1, minHeight: 0,
              overflow: 'auto',
              padding: 'var(--space-5)',
              backgroundColor: 'var(--slate-2)',
            }}
          >
            <div style={fullscreenSvgWrapperStyle}>
              <div dangerouslySetInnerHTML={{ __html: displayFullscreenSvg ?? '' }} />
            </div>
          </Box>
        </Dialog.Content>
      </Dialog.Root>
    </Box>
  );
}
