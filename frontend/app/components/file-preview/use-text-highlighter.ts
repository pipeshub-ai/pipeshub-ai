'use client';

import { useRef, useCallback } from 'react';
import type { PreviewCitation } from './types';

// ── Constants ────────────────────────────────────────────────────────────────

const SIMILARITY_THRESHOLD = 0.55;
export const SCROLL_RETRY_ATTEMPTS = 12;
export const SCROLL_RETRY_INTERVAL_MS = 120;
export const SCROLL_INITIAL_DELAY_MS = 150;

const HL_BASE = 'ph-highlight';
const HL_ACTIVE = `${HL_BASE}-active`;
const HL_FUZZY = `${HL_BASE}-fuzzy`;

const CANDIDATE_SELECTOR = [
  'p', 'li', 'blockquote',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'td', 'th', 'pre code',
  'span:not(:has(*))',
  'div:not(:has(div, p, ul, ol, blockquote, h1, h2, h3, h4, h5, h6, table, pre))',
].join(', ');

// ── Style injection ──────────────────────────────────────────────────────────

const STYLE_ID = 'ph-highlight-styles';

function ensureHighlightStyles(): void {
  if (typeof document === 'undefined') return;
  if (document.getElementById(STYLE_ID)) return;

  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    .${HL_BASE} {
      background-color: rgba(16, 185, 129, 0.18);
      border-radius: 2px;
      padding: 0.05em 0.15em;
      cursor: pointer;
      transition: background-color 0.2s ease, box-shadow 0.2s ease;
      display: inline;
      position: relative;
      z-index: 1;
      box-shadow: 0 0 0 1px rgba(16, 185, 129, 0.25);
      border-bottom: 1px solid rgba(16, 185, 129, 0.35);
    }
    .${HL_BASE}:hover {
      background-color: rgba(16, 185, 129, 0.28);
      box-shadow: 0 0 0 1px rgba(16, 185, 129, 0.45);
      z-index: 2;
    }
    .${HL_ACTIVE} {
      background-color: rgba(16, 185, 129, 0.35) !important;
      box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.65) !important;
      border-bottom: 2px solid rgba(16, 185, 129, 0.8) !important;
      z-index: 3 !important;
      animation: phHighlightPulse 0.7s ease-out 1;
    }
    @keyframes phHighlightPulse {
      0%   { box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.65); }
      50%  { box-shadow: 0 0 0 5px rgba(16, 185, 129, 0.35); }
      100% { box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.65); }
    }
    .${HL_FUZZY} {
      background-color: rgba(234, 179, 8, 0.15);
      box-shadow: 0 0 0 1px rgba(234, 179, 8, 0.25);
      border-bottom: 1px dashed rgba(234, 179, 8, 0.4);
    }
    .${HL_FUZZY}:hover {
      background-color: rgba(234, 179, 8, 0.25);
      box-shadow: 0 0 0 1px rgba(234, 179, 8, 0.45);
    }
    .${HL_FUZZY}.${HL_ACTIVE} {
      background-color: rgba(234, 179, 8, 0.32) !important;
      box-shadow: 0 0 0 2px rgba(234, 179, 8, 0.6) !important;
      border-bottom: 2px dashed rgba(234, 179, 8, 0.8) !important;
    }
    .${HL_BASE}::selection { background-color: rgba(16, 185, 129, 0.4); }
    @media (prefers-reduced-motion: reduce) {
      .${HL_BASE}, .${HL_ACTIVE}, .${HL_FUZZY} {
        transition: none; animation: none;
      }
    }
  `;
  document.head.appendChild(style);
}

// ── Pure helpers ─────────────────────────────────────────────────────────────

function normalizeText(text: string | null | undefined): string {
  if (!text) return '';
  return text.trim().replace(/\s+/g, ' ');
}

function jaccardSimilarity(a: string, b: string | null): number {
  const n1 = normalizeText(a).toLowerCase();
  const n2 = normalizeText(b).toLowerCase();
  if (!n1 || !n2) return 0;
  const words1 = new Set(n1.split(/\s+/).filter((w) => w.length > 2));
  const words2 = new Set(n2.split(/\s+/).filter((w) => w.length > 2));
  if (words1.size === 0 || words2.size === 0) return 0;
  let intersection = 0;
  words1.forEach((w) => { if (words2.has(w)) intersection += 1; });
  const union = words1.size + words2.size - intersection;
  return union === 0 ? 0 : intersection / union;
}

// ── Text node collection ─────────────────────────────────────────────────────

interface TextNodeEntry {
  node: Text;
  start: number;
  end: number;
}

/**
 * Collect all text nodes under `scope`. Only skips nodes already inside a
 * highlight for the *same* citation id, so overlapping citations from
 * different ids still get collected and can match.
 */
function collectTextNodes(
  scope: Element,
  skipHighlightId?: string,
): { fullText: string; entries: TextNodeEntry[] } {
  const entries: TextNodeEntry[] = [];
  let offset = 0;

  const skipSelector = skipHighlightId
    ? `.${HL_BASE}.highlight-${CSS.escape(skipHighlightId)}`
    : null;

  const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent) return NodeFilter.FILTER_REJECT;
      if (['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(parent.tagName)) return NodeFilter.FILTER_REJECT;
      if (skipSelector && parent.closest(skipSelector)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });

  let current: Node | null;
  while ((current = walker.nextNode())) {
    const textNode = current as Text;
    const value = textNode.nodeValue || '';
    if (value.length === 0) continue;
    entries.push({ node: textNode, start: offset, end: offset + value.length });
    offset += value.length;
  }

  const fullText = entries.map((e) => e.node.nodeValue || '').join('');
  return { fullText, entries };
}

function buildNormalizedMap(original: string): { normalized: string; toOriginal: number[] } {
  const toOriginal: number[] = [];
  let normalized = '';
  let inWS = true;

  for (let i = 0; i < original.length; i++) {
    const ch = original[i];
    const isWS = /\s/.test(ch);

    if (isWS) {
      if (!inWS && normalized.length > 0) {
        normalized += ' ';
        toOriginal.push(i);
        inWS = true;
      }
    } else {
      normalized += ch;
      toOriginal.push(i);
      inWS = false;
    }
  }

  if (normalized.endsWith(' ')) {
    normalized = normalized.slice(0, -1);
    toOriginal.pop();
  }

  return { normalized, toOriginal };
}

function resolveRange(
  entries: TextNodeEntry[],
  globalStart: number,
  globalEnd: number,
): { startNode: Text; startOffset: number; endNode: Text; endOffset: number } | null {
  let startNode: Text | null = null;
  let startOffset = 0;
  let endNode: Text | null = null;
  let endOffset = 0;

  for (const entry of entries) {
    if (!startNode && globalStart < entry.end) {
      startNode = entry.node;
      startOffset = globalStart - entry.start;
    }
    if (globalEnd <= entry.end) {
      endNode = entry.node;
      endOffset = globalEnd - entry.start;
      break;
    }
  }

  if (!startNode || !endNode) return null;
  startOffset = Math.max(0, Math.min(startOffset, (startNode.nodeValue || '').length));
  endOffset = Math.max(0, Math.min(endOffset, (endNode.nodeValue || '').length));

  return { startNode, startOffset, endNode, endOffset };
}

/**
 * Resolve the original-string end offset for a normalized match. When the
 * last matched normalized char maps to the start of a collapsed whitespace
 * run, advance past the run so the range boundary doesn't land mid-WS.
 */
function resolveOrigEnd(
  matchEndNorm: number,
  toOriginal: number[],
  fullText: string,
): number {
  if (matchEndNorm >= toOriginal.length) return fullText.length;
  let origEnd = toOriginal[matchEndNorm] + 1;
  while (origEnd < fullText.length && /\s/.test(fullText[origEnd - 1]) && /\s/.test(fullText[origEnd])) {
    origEnd++;
  }
  return Math.min(origEnd, fullText.length);
}

// ── DOM highlight engine ─────────────────────────────────────────────────────

interface HighlightResult {
  success: boolean;
  cleanup?: () => void;
}

interface ScopeTextData {
  fullText: string;
  entries: TextNodeEntry[];
  normalized: string;
  normalizedLower: string;
  toOriginal: number[];
}

function buildScopeTextData(scope: Element, skipHighlightId?: string): ScopeTextData | null {
  const { fullText, entries } = collectTextNodes(scope, skipHighlightId);
  if (!fullText || entries.length === 0) return null;
  const { normalized, toOriginal } = buildNormalizedMap(fullText);
  return {
    fullText,
    entries,
    normalized,
    normalizedLower: normalized.toLowerCase(),
    toOriginal,
  };
}

/**
 * Highlight matching text within `scope`. For cross-node matches, wraps each
 * participating text node individually with `surroundContents` rather than
 * using `extractContents`/`insertNode`, which would restructure the DOM and
 * break React's reconciler in React-owned subtrees.
 */
function highlightTextInScope(
  scope: Element,
  normalizedSearch: string,
  highlightId: string,
  matchType: 'exact' | 'fuzzy',
  onClickHighlight?: (id: string) => void,
  precomputed?: ScopeTextData | null,
): HighlightResult {
  if (!scope || !normalizedSearch || normalizedSearch.length < 3) {
    return { success: false };
  }

  const idClass = `highlight-${highlightId}`;
  const typeClass = matchType === 'fuzzy' ? HL_FUZZY : '';
  const fullClass = [HL_BASE, idClass, typeClass].filter(Boolean).join(' ');

  if (scope.querySelector(`.${HL_BASE}.${CSS.escape(idClass)}`)) {
    return { success: false };
  }

  const data = precomputed ?? buildScopeTextData(scope, highlightId);
  if (!data) return { success: false };

  const searchLower = normalizedSearch.toLowerCase();
  const matchIdx = data.normalizedLower.indexOf(searchLower);

  if (matchIdx === -1) {
    return highlightFuzzyFallback(scope, highlightId, fullClass, matchType, onClickHighlight);
  }

  const origStart = data.toOriginal[matchIdx];
  const matchEndNorm = matchIdx + searchLower.length - 1;
  const origEnd = resolveOrigEnd(matchEndNorm, data.toOriginal, data.fullText);

  const resolved = resolveRange(data.entries, origStart, origEnd);
  if (!resolved) {
    return highlightFuzzyFallback(scope, highlightId, fullClass, matchType, onClickHighlight);
  }

  try {
    const range = document.createRange();
    range.setStart(resolved.startNode, resolved.startOffset);
    range.setEnd(resolved.endNode, resolved.endOffset);

    if (resolved.startNode === resolved.endNode) {
      const span = createHighlightSpan(fullClass, highlightId, onClickHighlight);
      range.surroundContents(span);
      return {
        success: true,
        cleanup: () => unwrapSpan(span),
      };
    }

    // Cross-node: wrap each text node's matching segment individually so we
    // never restructure React's DOM tree.
    const wrappedSpans: HTMLSpanElement[] = [];

    for (const entry of data.entries) {
      if (entry.end <= origStart) continue;
      if (entry.start >= origEnd) break;

      const nodeStart = Math.max(0, origStart - entry.start);
      const nodeEnd = Math.min((entry.node.nodeValue || '').length, origEnd - entry.start);
      if (nodeStart >= nodeEnd) continue;

      try {
        const nodeRange = document.createRange();
        nodeRange.setStart(entry.node, nodeStart);
        nodeRange.setEnd(entry.node, nodeEnd);

        const span = createHighlightSpan(fullClass, highlightId, onClickHighlight);
        nodeRange.surroundContents(span);
        wrappedSpans.push(span);
      } catch (segErr) {
        console.warn(`[useTextHighlighter] surroundContents failed for segment in citation ${highlightId}:`, segErr);
      }
    }

    if (wrappedSpans.length > 0) {
      return {
        success: true,
        cleanup: () => {
          for (const span of wrappedSpans) unwrapSpan(span);
        },
      };
    }

    return highlightFuzzyFallback(scope, highlightId, fullClass, matchType, onClickHighlight);
  } catch (err) {
    console.warn(`[useTextHighlighter] Range manipulation failed for citation ${highlightId}:`, err);
    return highlightFuzzyFallback(scope, highlightId, fullClass, matchType, onClickHighlight);
  }
}

function createHighlightSpan(
  className: string,
  highlightId: string,
  onClickHighlight?: (id: string) => void,
): HTMLSpanElement {
  const span = document.createElement('span');
  span.className = className;
  span.dataset.highlightId = highlightId;
  span.addEventListener('click', (e) => {
    e.stopPropagation();
    onClickHighlight?.(highlightId);
  });
  return span;
}

/**
 * Remove a highlight span and restore its children. Only merges the
 * immediately adjacent text siblings — avoids `parent.normalize()` which
 * would walk the entire subtree, invalidate sibling Text node references,
 * and mutate React-owned DOM.
 */
function unwrapSpan(span: HTMLSpanElement): void {
  const parent = span.parentNode;
  if (!parent) return;
  try {
    const prev = span.previousSibling;
    const next = span.nextSibling;
    while (span.firstChild) {
      parent.insertBefore(span.firstChild, span);
    }
    parent.removeChild(span);
    if (next instanceof Text && next.previousSibling instanceof Text) {
      next.previousSibling.nodeValue += next.nodeValue || '';
      next.remove();
    }
    if (prev instanceof Text && prev.nextSibling instanceof Text) {
      prev.nodeValue += prev.nextSibling.nodeValue || '';
      prev.nextSibling.remove();
    }
  } catch { /* noop */ }
}

function highlightFuzzyFallback(
  scope: Element,
  highlightId: string,
  fullClass: string,
  matchType: 'exact' | 'fuzzy',
  onClickHighlight?: (id: string) => void,
): HighlightResult {
  if (matchType !== 'fuzzy') return { success: false };

  const idClass = `highlight-${highlightId}`;
  if (scope.querySelector(`.${CSS.escape(idClass)}`) || scope.classList.contains(idClass)) {
    return { success: false };
  }

  try {
    const wrapper = createHighlightSpan(fullClass, highlightId, onClickHighlight);
    while (scope.firstChild) wrapper.appendChild(scope.firstChild);
    scope.appendChild(wrapper);
    return {
      success: true,
      cleanup: () => {
        if (wrapper.parentNode === scope) {
          while (wrapper.firstChild) scope.insertBefore(wrapper.firstChild, wrapper);
          try { scope.removeChild(wrapper); } catch { /* noop */ }
        }
      },
    };
  } catch {
    return { success: false };
  }
}

// ── Public hook ──────────────────────────────────────────────────────────────

interface UseTextHighlighterOptions {
  citations?: PreviewCitation[];
  activeCitationId?: string | null;
  onHighlightClick?: (citationId: string) => void;
}

interface UseTextHighlighterResult {
  applyHighlights: (root: Element | null) => void;
  clearHighlights: () => void;
  scrollToHighlight: (citationId: string, root: Element | null) => void;
}

export function useTextHighlighter({
  citations,
  activeCitationId: _activeCitationId,
  onHighlightClick,
}: UseTextHighlighterOptions): UseTextHighlighterResult {
  const cleanupsRef = useRef<Map<string, () => void>>(new Map());
  const isHighlightingRef = useRef(false);

  const clearHighlights = useCallback(() => {
    cleanupsRef.current.forEach((fn) => {
      try { fn(); } catch { /* noop */ }
    });
    cleanupsRef.current.clear();
  }, []);

  const applyHighlights = useCallback(
    (root: Element | null) => {
      if (!root || !citations?.length || isHighlightingRef.current) return;

      isHighlightingRef.current = true;
      ensureHighlightStyles();
      clearHighlights();

      requestAnimationFrame(() => {
        try {
          if (!root) { isHighlightingRef.current = false; return; }

          const candidates = Array.from(root.querySelectorAll(CANDIDATE_SELECTOR));
          if (candidates.length === 0 && root.hasChildNodes()) {
            candidates.push(root);
          }

          const candidateTexts = candidates.map((el) => normalizeText(el.textContent).toLowerCase());

          let rootTextData: ScopeTextData | null | undefined;
          const getRootTextData = () => {
            if (rootTextData === undefined) rootTextData = buildScopeTextData(root);
            return rootTextData;
          };

          const newCleanups = new Map<string, () => void>();

          for (const citation of citations) {
            const normalized = normalizeText(citation.content);
            if (!normalized || normalized.length < 3) continue;

            const id = citation.id;
            const searchLower = normalized.toLowerCase();

            let matched = false;
            for (let i = 0; i < candidates.length; i++) {
              const el = candidates[i];
              if (el.querySelector(`.highlight-${CSS.escape(id)}`) || el.classList.contains(`highlight-${id}`)) continue;
              if (!candidateTexts[i].includes(searchLower)) continue;

              const result = highlightTextInScope(el, normalized, id, 'exact', onHighlightClick);
              if (result.success) {
                if (result.cleanup) newCleanups.set(id, result.cleanup);
                matched = true;
                break;
              }
            }

            if (!matched) {
              const rtd = getRootTextData();
              if (rtd && rtd.normalizedLower.includes(searchLower)) {
                const result = highlightTextInScope(root, normalized, id, 'exact', onHighlightClick, rtd);
                if (result.success) {
                  if (result.cleanup) newCleanups.set(id, result.cleanup);
                  matched = true;
                }
              }
            }

            if (!matched && candidates.length > 0) {
              const scored = candidates
                .map((el, i) => ({ el, score: jaccardSimilarity(normalized, candidateTexts[i]) }))
                .filter((x) => x.score > SIMILARITY_THRESHOLD)
                .sort((a, b) => b.score - a.score);

              if (scored.length > 0) {
                const best = scored[0];
                if (!best.el.querySelector(`.highlight-${CSS.escape(id)}`) && !best.el.classList.contains(`highlight-${id}`)) {
                  const result = highlightTextInScope(best.el, normalized, id, 'fuzzy', onHighlightClick);
                  if (result.success && result.cleanup) {
                    newCleanups.set(id, result.cleanup);
                  }
                }
              }
            }
          }

          cleanupsRef.current = newCleanups;
        } catch (e) {
          console.error('[useTextHighlighter] applyHighlights error:', e);
        } finally {
          isHighlightingRef.current = false;
        }
      });
    },
    [citations, clearHighlights, onHighlightClick],
  );

  const scrollToHighlight = useCallback(
    (citationId: string, root: Element | null) => {
      if (!root || !citationId) return;

      root.querySelectorAll(`.${HL_ACTIVE}`).forEach((el) => el.classList.remove(HL_ACTIVE));

      const el = root.querySelector(`.highlight-${CSS.escape(citationId)}`);
      if (el) {
        el.classList.add(HL_ACTIVE);
        el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
      }
    },
    [],
  );

  return { applyHighlights, clearHighlights, scrollToHighlight };
}