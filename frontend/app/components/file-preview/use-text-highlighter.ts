'use client';

import { useRef, useCallback } from 'react';
import type { PreviewCitation } from './types';

// ── Constants ────────────────────────────────────────────────────────────────

const SIMILARITY_THRESHOLD = 0.55;

/** CSS class prefix used on all highlight spans */
const HL_BASE = 'ph-highlight';
const HL_ACTIVE = `${HL_BASE}-active`;
const HL_FUZZY = `${HL_BASE}-fuzzy`;

/**
 * Candidate element selector – targets leaf text containers.
 * Used inside the rendered DOM to find elements whose textContent can be
 * compared against citation content.
 */
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

/** Collapse all whitespace to single spaces and trim. */
function normalizeText(text: string | null | undefined): string {
  if (!text) return '';
  return text.trim().replace(/\s+/g, ' ');
}

/** Jaccard word-level similarity (0 → 1). */
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

// ── DOM highlight engine ─────────────────────────────────────────────────────

interface HighlightResult {
  success: boolean;
  cleanup?: () => void;
}

/**
 * Walk the text nodes inside `scope`, find `normalizedSearch`, and wrap the
 * matching range in a `<span>` with proper highlight classes.
 */
function highlightTextInScope(
  scope: Element,
  normalizedSearch: string,
  highlightId: string,
  matchType: 'exact' | 'fuzzy',
  onClickHighlight?: (id: string) => void,
): HighlightResult {
  if (!scope || !normalizedSearch || normalizedSearch.length < 3) {
    return { success: false };
  }

  const idClass = `highlight-${highlightId}`;
  const typeClass = matchType === 'fuzzy' ? HL_FUZZY : '';
  const fullClass = [HL_BASE, idClass, typeClass].filter(Boolean).join(' ');

  // Try exact text-node match via TreeWalker
  const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent) return NodeFilter.FILTER_REJECT;
      if (['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(parent.tagName)) return NodeFilter.FILTER_REJECT;
      if (parent.closest(`.${HL_BASE}.${idClass}`)) return NodeFilter.FILTER_REJECT;
      if (!node.textContent?.trim()) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });

  let node: Node | null;
  let found = false;
  let wrappedSpan: HTMLSpanElement | null = null;

  while (!found) {
    node = walker.nextNode();
    if (!node) break;

    const textNode = node as Text;
    const nodeText = textNode.nodeValue || '';
    const normalizedNodeText = normalizeText(nodeText);

    const idx = normalizedNodeText.toLowerCase().indexOf(normalizedSearch.toLowerCase());
    if (idx === -1) continue;

    // Map the normalized index back to the original string
    let origStart = -1;
    let normI = 0;
    let inWS = false;
    for (let j = 0; j < nodeText.length && normI <= idx; j++) {
      const isWS = /\s/.test(nodeText[j]);
      if (isWS) {
        if (!inWS) { normI += 1; inWS = true; }
      } else {
        normI += 1;
        inWS = false;
      }
      if (normI > idx && origStart === -1) origStart = j;
    }
    // Fallback: direct indexOf
    if (origStart === -1) {
      origStart = nodeText.toLowerCase().indexOf(normalizedSearch.split(' ')[0].toLowerCase());
    }
    if (origStart === -1) continue;

    // Walk forward through original text to cover normalizedSearch.length normalized chars
    let origEnd = origStart;
    let covered = 0;
    inWS = false;
    while (origEnd < nodeText.length && covered < normalizedSearch.length) {
      const isWS = /\s/.test(nodeText[origEnd]);
      if (isWS) {
        if (!inWS) { covered += 1; inWS = true; }
      } else {
        covered += 1;
        inWS = false;
      }
      origEnd += 1;
    }

    const safeEnd = Math.min(origEnd, nodeText.length);
    if (origStart >= safeEnd) continue;

    try {
      const range = document.createRange();
      range.setStart(textNode, origStart);
      range.setEnd(textNode, safeEnd);

      const span = document.createElement('span');
      span.className = fullClass;
      span.dataset.highlightId = highlightId;

      span.addEventListener('click', (e) => {
        e.stopPropagation();
        onClickHighlight?.(highlightId);
      });

      range.surroundContents(span);
      wrappedSpan = span;
      found = true;
    } catch {
      // surroundContents can fail across node boundaries – skip
    }
  }

  if (found && wrappedSpan) {
    const spanRef = wrappedSpan;
    return {
      success: true,
      cleanup: () => {
        if (spanRef.parentNode) {
          const text = document.createTextNode(spanRef.textContent || '');
          try { spanRef.parentNode.replaceChild(text, spanRef); } catch { /* noop */ }
        }
      },
    };
  }

  // Fuzzy fallback: wrap the entire element
  if (matchType === 'fuzzy' && !found) {
    if (scope.querySelector(`.${idClass}`) || scope.classList.contains(idClass)) {
      return { success: false };
    }
    try {
      const wrapper = document.createElement('span');
      wrapper.className = fullClass;
      wrapper.dataset.highlightId = highlightId;
      wrapper.addEventListener('click', (e) => {
        e.stopPropagation();
        onClickHighlight?.(highlightId);
      });
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

  return { success: false };
}

// ── Public hook ──────────────────────────────────────────────────────────────

interface UseTextHighlighterOptions {
  /** Citations to highlight in the rendered content */
  citations?: PreviewCitation[];
  /** ID of the currently active/selected citation */
  activeCitationId?: string | null;
  /** Called when user clicks a highlight span in the document */
  onHighlightClick?: (citationId: string) => void;
}

interface UseTextHighlighterResult {
  /** Call after the DOM content has been rendered/updated.
   *  Pass the root element that contains the rendered document. */
  applyHighlights: (root: Element | null) => void;

  /** Remove all highlight spans and restore original text nodes */
  clearHighlights: () => void;

  /** Scroll the highlight for a given citation into view and mark it active */
  scrollToHighlight: (citationId: string, root: Element | null) => void;
}

/**
 * Shared hook for text-based citation highlighting.
 *
 * Works with any renderer that produces DOM text content:
 * DOCX (docx-preview), HTML (DOMPurify), Markdown (ReactMarkdown), Text (pre).
 *
 * Usage:
 * ```ts
 * const { applyHighlights, clearHighlights, scrollToHighlight } = useTextHighlighter({
 *   citations,
 *   activeCitationId,
 *   onHighlightClick: (id) => setActive(id),
 * });
 *
 * // after content renders:
 * useEffect(() => { applyHighlights(containerRef.current); }, [content]);
 * ```
 */
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
          // Fallback: use root itself if no candidates found
          if (candidates.length === 0 && root.hasChildNodes()) {
            candidates.push(root);
          }

          const newCleanups = new Map<string, () => void>();

          for (const citation of citations) {
            const normalized = normalizeText(citation.content);
            if (!normalized || normalized.length < 3) continue;

            const id = citation.id;

            // 1. Try exact match
            let matched = false;
            for (const el of candidates) {
              if (el.querySelector(`.highlight-${CSS.escape(id)}`) || el.classList.contains(`highlight-${id}`)) continue;
              const elText = normalizeText(el.textContent);
              if (!elText.includes(normalized)) continue;

              const result = highlightTextInScope(el, normalized, id, 'exact', onHighlightClick);
              if (result.success) {
                if (result.cleanup) newCleanups.set(id, result.cleanup);
                matched = true;
                break;
              }
            }

            // 2. Fuzzy fallback
            if (!matched && candidates.length > 0) {
              const scored = candidates
                .map((el) => ({ el, score: jaccardSimilarity(normalized, el.textContent) }))
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

      // Clear previous active
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
