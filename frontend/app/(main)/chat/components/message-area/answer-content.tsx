'use client';

import React, { useState, useMemo, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import type { Schema } from 'hast-util-sanitize';
import 'katex/dist/katex.min.css';
import { Box, Flex, Text, Heading } from '@radix-ui/themes';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneLight, oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { InlineCitationBadge, InlineCitationGroup } from './response-tabs/citations';
import type {
  CitationMaps,
  CitationCallbacks,
  CitationData,
} from './response-tabs/citations';
import { TableFullscreenWrapper } from './table-fullscreen-wrapper';
import { MermaidDiagram } from './mermaid-diagram';
import { parseCsvContent, parseCsvCellContent } from './csv-utils';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useThemeAppearance } from '@/app/components/theme-provider';
import { useTranslation } from 'react-i18next';
import type { Root, Blockquote } from 'mdast';

/**
 * rehype-sanitize schema.
 *
 * Pipeline order: rehypeRaw → rehypeSanitize → rehypeKatex
 *
 * rehypeSanitize runs BEFORE rehypeKatex, so at sanitization time math nodes
 * are still plain `<code class="language-math">` elements — completely harmless.
 * rehypeKatex then converts them to its own HTML which is never sanitized.
 * This avoids having to allowlist the entire KaTeX SVG/MathML output surface.
 *
 * The schema only needs to cover raw HTML that the LLM may embed (callouts,
 * details/summary, styled spans, etc.). Dangerous constructs — <script>,
 * on* event handlers, javascript:/data: URLs — remain blocked by defaultSchema.
 */
const SANITIZE_SCHEMA: Schema = {
  ...defaultSchema,
  tagNames: [
    ...(defaultSchema.tagNames ?? []),
    // Collapsible sections the LLM may emit
    'details', 'summary',
  ],
  attributes: {
    ...defaultSchema.attributes,
    // className on ALL elements (syntax highlighting, callout markers, etc.)
    '*': [...(defaultSchema.attributes?.['*'] ?? []), 'className'],
    // style is intentionally restricted to elements where inline styling is
    // safe and dark-mode neutral.  Structural/semantic wrappers (section,
    // article, nav, header, aside) are excluded because LLMs routinely emit
    // hardcoded light colours (#fafafa backgrounds, #e5e7eb borders) that
    // create jarring white boxes in dark mode.  rehype-sanitize has no
    // built-in CSS-property–level filtering, so we control it at the
    // element level instead.
    'table':      [...(defaultSchema.attributes?.['table']      ?? []), 'style'],
    'thead':      [...(defaultSchema.attributes?.['thead']      ?? []), 'style'],
    'tbody':      [...(defaultSchema.attributes?.['tbody']      ?? []), 'style'],
    'tfoot':      [...(defaultSchema.attributes?.['tfoot']      ?? []), 'style'],
    'tr':         [...(defaultSchema.attributes?.['tr']         ?? []), 'style'],
    'th':         [...(defaultSchema.attributes?.['th']         ?? []), 'style'],
    'td':         [...(defaultSchema.attributes?.['td']         ?? []), 'style'],
    // div: style intentionally omitted.  LLMs routinely use <div> as card
    // wrappers with hardcoded light backgrounds (background:#f8f9fa etc.)
    // that create white boxes in dark mode.  Our table component handles
    // overflow-x:auto internally, so the div wrapper style is not needed.
    'span':       [...(defaultSchema.attributes?.['span']       ?? []), 'style'],
    'pre':        [...(defaultSchema.attributes?.['pre']        ?? []), 'style'],
    'code':       [...(defaultSchema.attributes?.['code']       ?? []), 'style'],
    'img':        [...(defaultSchema.attributes?.['img']        ?? []), 'style'],
    'a':          [...(defaultSchema.attributes?.['a']          ?? []), 'style'],
    'figure':     [...(defaultSchema.attributes?.['figure']     ?? []), 'style'],
    'figcaption': [...(defaultSchema.attributes?.['figcaption'] ?? []), 'style'],
  },
};

/**
 * Convert raw HTML <pre><code> blocks to markdown fenced code blocks.
 *
 * Why this is necessary
 * ---------------------
 * CommonMark type-1 HTML blocks (<pre>) are only recognised when the opening
 * tag appears at the start of a line with ≤3 spaces of indentation.  When the
 * LLM nests <pre><code> inside other HTML elements (e.g. <section>, <div>),
 * the tag is indented more than 3 spaces and is therefore NOT treated as a
 * type-1 block.  The parent type-6 block (<div>, <section>) terminates at the
 * first blank line — which is common inside multiline code examples — causing
 * the tail of the code to leak out as plain text, and the HTML source of the
 * <pre> opening tag to appear inside a code block.
 *
 * Converting to fenced blocks before remark sees the content eliminates both
 * problems:
 *  - fenced code blocks are immune to blank-line termination
 *  - our CodeBlock renderer handles them uniformly (syntax highlighting,
 *    consistent dark/light background via CSS variables)
 *
 * HTML entities (&lt; &gt; &amp; &quot; &#39;) inside the code are decoded
 * so they render as the original source characters.
 */
function preprocessHtmlCodeBlocks(content: string): string {
  return content.replace(
    // Match <pre ...><code ...>...</code></pre> regardless of indentation or
    // blank lines inside the code body.  The language class is captured from
    // class="language-xxx" on the <code> element when present.
    /<pre[^>]*>\s*<code((?:[^>]*)?)>([\s\S]*?)<\/code>\s*<\/pre>/g,
    (_m, codeAttrs: string, rawCode: string) => {
      // Extract language from class="... language-xxx ..." if present
      const langMatch = codeAttrs.match(/\blanguage-([\w-]+)/);
      const language = langMatch ? langMatch[1] : '';

      // Decode HTML entities in a single pass to prevent double-unescaping.
      // Chained .replace() calls are unsafe: decoding &amp; → & in one step
      // produces &quot; / &#39; / &lt; that subsequent steps would decode a
      // second time (e.g. &amp;quot; → &quot; → ").  A single regex with a
      // callback moves the scanner past each match so replacements are never
      // re-examined, making the result identical to one round of HTML decoding.
      const HTML_ENTITY_MAP: Record<string, string> = {
        amp: '&', lt: '<', gt: '>', quot: '"',
        apos: "'", '#39': "'", '#x27': "'", '#x2F': '/',
      };
      const decoded = rawCode.replace(
        /&(amp|lt|gt|quot|apos|#39|#x27|#x2F);/g,
        (_, entity: string) => HTML_ENTITY_MAP[entity] ?? `&${entity};`,
      );
      // Use enough backticks to avoid colliding with any backtick runs in code
      const maxRun = Math.max(2, ...[...decoded.matchAll(/`+/g)].map(m => m[0].length));
      const fence = '`'.repeat(maxRun + 1);

      // Double newlines (\n\n) are critical.  A single \n is NOT a blank line
      // and will not terminate a surrounding CommonMark type-6 HTML block
      // (e.g. <div>).  Inside an HTML block remark treats fenced markers as
      // raw text, which causes the ``` to appear literally in the output.
      // \n\n guarantees the fence starts in a fresh block context.
      return `\n\n${fence}${language}\n${decoded.trim()}\n${fence}\n\n`;
    }
  );
}

/**
 * Strip excess indentation from HTML tag lines so remark does not
 * misidentify them as indented code blocks.
 *
 * CommonMark rule: a line with ≥4 leading spaces is an indented code block.
 * When LLM output nests HTML inside other HTML (e.g. <section> inside <div>)
 * the inner tags are typically indented by 4–8 spaces.  After the outer
 * type-6 HTML block terminates at a blank line, those deeply-indented tag
 * lines are re-parsed as indented code blocks, causing raw HTML source to
 * appear inside `plaintext` code boxes.
 *
 * Fix: strip any run of ≥4 leading spaces from lines whose first non-space
 * character is `<` (open tag, close tag, or comment).  Lines that are actual
 * code (which never start with `<` unless they're HTML being written as
 * code) are unaffected.  Content inside fenced code blocks is fully
 * protected by the split-and-skip approach.
 */
function preprocessHtmlIndentation(content: string): string {
  // Fast-path: if no line in the entire string starts with ≥4 spaces followed
  // by `<`, there is nothing to do.  This makes the function a strict no-op for
  // pure markdown responses and avoids the expensive split-and-scan entirely.
  //
  // Known limitation: this function can break intentional markdown indented
  // code blocks that show HTML source, e.g.:
  //
  //     <div class="example">   ← 4-space indent = CommonMark indented code block
  //         <p>hello</p>
  //     </div>
  //
  // After this transform those lines lose their indentation and render as real
  // HTML instead of a code block.  Mitigation: LLMs virtually never use
  // indented code blocks (they prefer fenced blocks), so the risk is negligible.
  // If this ever becomes a real issue the fix is to require the line before the
  // indented `<` tag to also be a non-blank indented line (i.e. mid-block, not
  // the first line of an indented code block).
  if (!/^ {4,}<[!/a-zA-Z]/m.test(content)) return content;

  // Protect fenced code blocks — their content must not be de-indented.
  const parts = content.split(/(```[\s\S]*?```|~~~[\s\S]*?~~~)/g);
  return parts
    .map((part, i) => {
      if (i % 2 !== 0) return part; // inside fenced block — leave untouched
      // Remove ≥4 leading spaces from lines that begin an HTML tag or comment.
      return part.replace(/^( {4,})(<[!/a-zA-Z])/gm, '$2');
    })
    .join('');
}

/**
 * Convert LaTeX-style math delimiters to the dollar-sign notation that
 * remark-math v6 requires.  remark-math v6 deliberately dropped \( / \[ /
 * \] / \) support (they were ambiguous with backslash escapes in v5).
 *
 * Skips fenced code blocks, inline code spans, and markdown links/images
 * so we never mangle literal backslash sequences inside code examples or
 * escaped brackets inside link text (e.g. `[Title \[Subtitle\]](url)`).
 *
 *   \(...\)  →  $...$        (inline math)
 *   \[...\]  →  $$\n...\n$$  (display math)
 */
function preprocessMath(content: string): string {
  // Split on fenced code blocks (``` or ~~~), inline code spans (`...`),
  // and markdown links/images whose text may contain escaped brackets.
  // Without the link protection, \[text\] inside [link \[text\]](url)
  // would be misidentified as display math and converted to $$..$$,
  // completely breaking the link structure.
  // Even-indexed segments are outside protected spans; odd-indexed are inside.
  const parts = content.split(
    /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`]*`|!?\[(?:[^\[\]\\]|\\.)*\]\([^)]*\))/g
  );
  return parts
    .map((part, i) => {
      if (i % 2 !== 0) return part; // inside protected span — leave untouched
      return part
        // Block math first (greedy order matters): \[ ... \]
        // Tempered greedy token stops at blank lines (paragraph breaks) so an
        // unclosed \[ never swallows subsequent paragraphs.
        .replace(/\\\[((?:(?!\n\n)[\s\S])*?)\\\]/g, (_m, math: string) => `$$\n${math.trim()}\n$$`)
        // Inline math: \( ... \)
        // Same paragraph-break guard — inline math must not span blank lines.
        .replace(/\\\(((?:(?!\n\n)[\s\S])*?)\\\)/g, (_m, math: string) => `$${math}$`);
    })
    .join('');
}

/**
 * Remark plugin: detects > [!NOTE] / [!WARNING] / … blockquotes that LLMs
 * generate and adds the hast class properties that our blockquote renderer
 * looks for ('markdown-alert markdown-alert-{type}'). Also strips the
 * [!TYPE] prefix text so it doesn't appear in the callout body.
 *
 * Works at the mdast level — before any React rendering — so it's fully
 * reliable regardless of how children are structured downstream.
 */
function remarkCallouts() {
  // Match ANY [!IDENTIFIER] pattern — not limited to known types.
  // Unknown types still get stripped + tagged; Callout renders them with a
  // generic neutral style (same approach as Claude and Notion).
  const PATTERN = /^\[!([A-Z][A-Z0-9_-]*)\]\s*/i;

  function walk(node: Root | Blockquote | { type: string; children?: unknown[] }) {
    if (node.type === 'blockquote') {
      const bq = node as Blockquote;
      const firstPara = bq.children[0];
      if (firstPara?.type === 'paragraph') {
        const firstText = (firstPara as { children: Array<{ type: string; value?: string }> }).children[0];
        if (firstText?.type === 'text' && firstText.value) {
          const m = firstText.value.match(PATTERN);
          if (m) {
            const alertType = m[1].toLowerCase();
            // Strip prefix from text
            firstText.value = firstText.value.slice(m[0].length).trimStart();
            // If the first paragraph is now empty, remove it
            const paraChildren = (firstPara as { children: unknown[] }).children;
            if (firstText.value === '' && paraChildren.length === 1) {
              bq.children.shift();
            }
            // Attach hast class so our blockquote renderer picks it up.
            // Append rather than overwrite so other plugins' classes are preserved.
            const data = bq.data ?? (bq.data = {});
            const hProps = ((data as Record<string, unknown>).hProperties ?? {}) as Record<string, unknown>;
            const existing = Array.isArray(hProps.className)
              ? hProps.className
              : typeof hProps.className === 'string' ? [hProps.className] : [];
            hProps.className = [...existing, 'markdown-alert', `markdown-alert-${alertType}`];
            (data as Record<string, unknown>).hProperties = hProps;
          }
        }
      }
    }
    if ('children' in node && Array.isArray(node.children)) {
      for (const child of node.children) walk(child as typeof node);
    }
  }

  return (tree: Root) => walk(tree);
}

interface AnswerContentProps {
  content: string;
  citationMaps?: CitationMaps;
  citationCallbacks?: CitationCallbacks;
}

interface CitationMatch {
  chunkIndex: number;
  citation?: CitationData;
  index: number;
  length: number;
  key: string;
}

/** Recursively extract plain text from any React node (used for clipboard copy). */
function extractNodeText(node: React.ReactNode): string {
  if (typeof node === 'string') return node;
  if (typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(extractNodeText).join('');
  if (React.isValidElement(node)) {
    const el = node as React.ReactElement<{ children?: React.ReactNode }>;
    return extractNodeText(el.props?.children);
  }
  return '';
}

// ── Callouts ────────────────────────────────────────────────────────────────

type CalloutVariant = {
  icon: string;
  label: string;
  lightAccent: string; lightBg: string; lightBorder: string;
  darkAccent: string;  darkBg: string;  darkBorder: string;
};

// Known callout types. For any unrecognised [!TYPE], Callout falls back to
// '_default' and uses the raw type string (capitalised) as the label —
// matching how Claude and Notion render unknown alert types.
const CALLOUT_VARIANTS: Record<string, CalloutVariant> = {
  // GitHub / GFM spec
  note:      { icon: 'info',          label: 'Note',      lightAccent: '#0969da', lightBg: 'rgba(9,105,218,.08)',   lightBorder: 'rgba(9,105,218,.25)',   darkAccent: '#4493f8', darkBg: 'rgba(68,147,248,.1)',   darkBorder: 'rgba(68,147,248,.3)'   },
  tip:       { icon: 'lightbulb',     label: 'Tip',       lightAccent: '#1a7f37', lightBg: 'rgba(26,127,55,.08)',   lightBorder: 'rgba(26,127,55,.25)',   darkAccent: '#3fb950', darkBg: 'rgba(63,185,80,.1)',    darkBorder: 'rgba(63,185,80,.3)'    },
  important: { icon: 'priority_high', label: 'Important', lightAccent: '#8250df', lightBg: 'rgba(130,80,223,.08)',  lightBorder: 'rgba(130,80,223,.25)',  darkAccent: '#d2a8ff', darkBg: 'rgba(210,168,255,.1)',  darkBorder: 'rgba(210,168,255,.3)'  },
  warning:   { icon: 'warning',       label: 'Warning',   lightAccent: '#9a6700', lightBg: 'rgba(154,103,0,.08)',   lightBorder: 'rgba(154,103,0,.25)',   darkAccent: '#d29922', darkBg: 'rgba(210,153,34,.1)',   darkBorder: 'rgba(210,153,34,.3)'   },
  caution:   { icon: 'error',         label: 'Caution',   lightAccent: '#d1242f', lightBg: 'rgba(209,36,47,.08)',   lightBorder: 'rgba(209,36,47,.25)',   darkAccent: '#f85149', darkBg: 'rgba(248,81,73,.1)',    darkBorder: 'rgba(248,81,73,.3)'    },
  // Common aliases used by Obsidian, Notion, and various LLMs
  info:      { icon: 'info',          label: 'Info',      lightAccent: '#0969da', lightBg: 'rgba(9,105,218,.08)',   lightBorder: 'rgba(9,105,218,.25)',   darkAccent: '#4493f8', darkBg: 'rgba(68,147,248,.1)',   darkBorder: 'rgba(68,147,248,.3)'   },
  success:   { icon: 'check_circle',  label: 'Success',   lightAccent: '#1a7f37', lightBg: 'rgba(26,127,55,.08)',   lightBorder: 'rgba(26,127,55,.25)',   darkAccent: '#3fb950', darkBg: 'rgba(63,185,80,.1)',    darkBorder: 'rgba(63,185,80,.3)'    },
  danger:    { icon: 'error',         label: 'Danger',    lightAccent: '#d1242f', lightBg: 'rgba(209,36,47,.08)',   lightBorder: 'rgba(209,36,47,.25)',   darkAccent: '#f85149', darkBg: 'rgba(248,81,73,.1)',    darkBorder: 'rgba(248,81,73,.3)'    },
  error:     { icon: 'cancel',        label: 'Error',     lightAccent: '#d1242f', lightBg: 'rgba(209,36,47,.08)',   lightBorder: 'rgba(209,36,47,.25)',   darkAccent: '#f85149', darkBg: 'rgba(248,81,73,.1)',    darkBorder: 'rgba(248,81,73,.3)'    },
  question:  { icon: 'help',          label: 'Question',  lightAccent: '#8250df', lightBg: 'rgba(130,80,223,.08)',  lightBorder: 'rgba(130,80,223,.25)',  darkAccent: '#d2a8ff', darkBg: 'rgba(210,168,255,.1)',  darkBorder: 'rgba(210,168,255,.3)'  },
  bug:       { icon: 'bug_report',    label: 'Bug',       lightAccent: '#d1242f', lightBg: 'rgba(209,36,47,.08)',   lightBorder: 'rgba(209,36,47,.25)',   darkAccent: '#f85149', darkBg: 'rgba(248,81,73,.1)',    darkBorder: 'rgba(248,81,73,.3)'    },
  example:   { icon: 'code',          label: 'Example',   lightAccent: '#8250df', lightBg: 'rgba(130,80,223,.08)',  lightBorder: 'rgba(130,80,223,.25)',  darkAccent: '#d2a8ff', darkBg: 'rgba(210,168,255,.1)',  darkBorder: 'rgba(210,168,255,.3)'  },
  abstract:  { icon: 'description',  label: 'Abstract',  lightAccent: '#0969da', lightBg: 'rgba(9,105,218,.08)',   lightBorder: 'rgba(9,105,218,.25)',   darkAccent: '#4493f8', darkBg: 'rgba(68,147,248,.1)',   darkBorder: 'rgba(68,147,248,.3)'   },
  summary:   { icon: 'summarize',     label: 'Summary',   lightAccent: '#0969da', lightBg: 'rgba(9,105,218,.08)',   lightBorder: 'rgba(9,105,218,.25)',   darkAccent: '#4493f8', darkBg: 'rgba(68,147,248,.1)',   darkBorder: 'rgba(68,147,248,.3)'   },
  quote:     { icon: 'format_quote',  label: 'Quote',     lightAccent: '#57606a', lightBg: 'rgba(87,96,106,.08)',   lightBorder: 'rgba(87,96,106,.25)',   darkAccent: '#8b949e', darkBg: 'rgba(139,148,158,.1)',  darkBorder: 'rgba(139,148,158,.3)'  },
  // Generic neutral fallback — used for any unrecognised [!TYPE]
  _default:  { icon: 'info',          label: '',          lightAccent: '#57606a', lightBg: 'rgba(87,96,106,.08)',   lightBorder: 'rgba(87,96,106,.25)',   darkAccent: '#8b949e', darkBg: 'rgba(139,148,158,.1)',  darkBorder: 'rgba(139,148,158,.3)'  },
};

function Callout({ type, children }: { type: string; children?: React.ReactNode }) {
  const { appearance } = useThemeAppearance();
  const isDark = appearance === 'dark';
  const key = type.toLowerCase();
  const v = CALLOUT_VARIANTS[key] ?? CALLOUT_VARIANTS._default;
  // For unknown types use the raw type string, capitalised (e.g. "DANGER" → "Danger")
  const label = v.label || (type.charAt(0).toUpperCase() + type.slice(1).toLowerCase());
  const accent = isDark ? v.darkAccent : v.lightAccent;
  const bg     = isDark ? v.darkBg     : v.lightBg;
  const border = isDark ? v.darkBorder : v.lightBorder;

  // remark-gfm v4 injects a <p class="markdown-alert-title"> as the first child;
  // we render our own title row, so skip it.
  const content = React.Children.toArray(children).filter((child) => {
    if (!React.isValidElement(child)) return true;
    return !(child.props as { className?: string }).className?.includes('markdown-alert-title');
  });

  return (
    <Box
      style={{
        margin: 'var(--space-3) 0',
        padding: 'var(--space-3) var(--space-4)',
        borderRadius: 'var(--radius-2)',
        border: `1px solid ${border}`,
        borderLeft: `4px solid ${accent}`,
        backgroundColor: bg,
      }}
    >
      <Flex align="center" gap="2" style={{ marginBottom: content.length ? 'var(--space-2)' : 0 }}>
        <MaterialIcon name={v.icon} size={15} color={accent} />
        <Text size="2" weight="bold" style={{ color: accent }}>{label}</Text>
      </Flex>
      <Box style={{ color: 'var(--slate-12)', fontSize: '14px', lineHeight: 1.6 }}>
        {content}
      </Box>
    </Box>
  );
}

// ── Anchor headings ──────────────────────────────────────────────────────────

function slugify(text: string): string {
  return text.toLowerCase().replace(/[^\w\s-]/g, '').replace(/[\s_]+/g, '-').replace(/^-+|-+$/g, '');
}

const HEADING_SIZES: Record<number, '7' | '6' | '5' | '4' | '3' | '2'> = {
  1: '7', 2: '6', 3: '5', 4: '4', 5: '3', 6: '2',
};
const HEADING_STYLES: Record<number, React.CSSProperties> = {
  1: { marginTop: 'var(--space-5)', marginBottom: 'var(--space-3)', paddingBottom: 'var(--space-2)', borderBottom: '1px solid var(--slate-4)' },
  2: { marginTop: 'var(--space-4)', marginBottom: 'var(--space-2)' },
  3: { marginTop: 'var(--space-3)', marginBottom: 'var(--space-2)' },
  4: { marginTop: 'var(--space-3)', marginBottom: 'var(--space-1)' },
  5: { marginTop: 'var(--space-2)', marginBottom: 'var(--space-1)' },
  6: { marginTop: 'var(--space-2)', marginBottom: 'var(--space-1)' },
};

function AnchorHeading({ level, children }: { level: 1 | 2 | 3 | 4 | 5 | 6; children?: React.ReactNode }) {
  const [showAnchor, setShowAnchor] = useState(false);
  const id = slugify(extractNodeText(children));
  const tag = `h${level}` as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6';
  return (
    <Heading
      id={id || undefined}
      as={tag}
      size={HEADING_SIZES[level]}
      style={{ ...HEADING_STYLES[level], color: 'var(--slate-12)', position: 'relative', cursor: 'default' }}
      onMouseEnter={() => setShowAnchor(true)}
      onMouseLeave={() => setShowAnchor(false)}
    >
      {children}
      {id && (
        <a
          href={`#${id}`}
          aria-label={`Link to section`}
          style={{
            marginLeft: '8px',
            opacity: showAnchor ? 0.55 : 0,
            transition: 'opacity 0.15s ease',
            color: 'var(--slate-9)',
            textDecoration: 'none',
            fontWeight: 400,
            fontSize: '0.75em',
          }}
        >
          #
        </a>
      )}
    </Heading>
  );
}

/** Table row with a subtle hover highlight. */
function TableRow({ children }: { children?: React.ReactNode }) {
  const [isHovered, setIsHovered] = useState(false);
  return (
    <tr
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        backgroundColor: isHovered ? 'var(--slate-3)' : 'transparent',
        transition: 'background-color 0.12s ease',
      }}
    >
      {children}
    </tr>
  );
}

/**
 * Fenced code block with a header bar (language label + copy button)
 * and syntax-highlighted body that adapts to light / dark theme.
 */
function CodeBlock({ language, codeText }: { language: string; codeText: string }) {
  const [copied, setCopied] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const { appearance } = useThemeAppearance();
  const isDark = appearance === 'dark';
  const { t } = useTranslation();

  const handleCopy = () => {
    navigator.clipboard.writeText(codeText).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }).catch(() => {});
  };

  // CSS variables resolve light/dark automatically — no manual isDark checks needed.
  const headerBg = 'var(--slate-2)';
  const headerBorder = 'var(--slate-6)';
  const bodyBg = 'var(--slate-1)';
  const labelColor = 'var(--slate-11)';
  const btnHoverBg = 'var(--slate-4)';

  return (
    <Box
      style={{
        marginBottom: 'var(--space-3)',
        borderRadius: 'var(--radius-2)',
        overflow: 'hidden',
        border: `1px solid ${headerBorder}`,
      }}
    >
      {/* Header bar */}
      <Flex
        justify="between"
        align="center"
        style={{
          padding: '6px 12px',
          backgroundColor: headerBg,
          borderBottom: `1px solid ${headerBorder}`,
        }}
      >
        <Text
          size="1"
          style={{
            color: labelColor,
            fontFamily: 'monospace',
            letterSpacing: '0.04em',
            textTransform: 'lowercase',
            fontWeight: 500,
          }}
        >
          {language || 'text'}
        </Text>
        <button
          type="button"
          onClick={handleCopy}
          onMouseEnter={() => setIsHovered(true)}
          onMouseLeave={() => setIsHovered(false)}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '4px',
            background: isHovered ? btnHoverBg : 'transparent',
            border: 'none',
            cursor: 'pointer',
            padding: '3px 8px',
            borderRadius: 'var(--radius-1)',
            fontFamily: 'inherit',
            color: copied ? 'var(--accent-11)' : labelColor,
            transition: 'background 0.15s ease, color 0.15s ease',
          }}
        >
          <MaterialIcon
            name={copied ? 'check' : 'content_copy'}
            size={13}
            color={copied ? 'var(--accent-11)' : labelColor}
          />
          <Text size="1" style={{ color: 'inherit' }}>
            {copied ? t('chatStream.copiedCode') : t('chatStream.copyCode')}
          </Text>
        </button>
      </Flex>

      {/* Syntax-highlighted code body */}
      <Box
        className="no-scrollbar"
        style={{
          backgroundColor: bodyBg,
          overflowX: 'auto',
          overflowY: 'auto',
          maxHeight: '480px',
        }}
      >
        <SyntaxHighlighter
          language={language || 'text'}
          style={isDark ? oneDark : oneLight}
          customStyle={{
            margin: 0,
            padding: '16px',
            background: bodyBg,
            fontSize: '13px',
            lineHeight: 1.7,
            fontFamily: '"Fira Code", "Cascadia Code", Consolas, "Courier New", monospace',
          }}
          codeTagProps={{
            style: {
              fontFamily: 'inherit',
              fontSize: 'inherit',
            },
          }}
          wrapLongLines={false}
        >
          {codeText}
        </SyntaxHighlighter>
      </Box>
    </Box>
  );
}

/**
 * Emit either an InlineCitationGroup (2+ consecutive same-record markers) or an
 * InlineCitationBadge (single marker) for a run of citation matches.
 */
function emitRun(
  run: CitationMatch[],
  citationCallbacks?: CitationCallbacks,
): React.ReactNode {
  if (run.length >= 2 && run.every((m) => m.citation)) {
    return (
      <InlineCitationGroup
        key={`cite-group-${run[0].key}`}
        items={run.map((m) => ({
          chunkIndex: m.chunkIndex,
          occurrenceKey: m.key,
          citation: m.citation as CitationData,
        }))}
        callbacks={citationCallbacks}
      />
    );
  }

  const only = run[0];
  return (
    <InlineCitationBadge
      key={`cite-${only.key}`}
      chunkIndex={only.chunkIndex}
      occurrenceKey={only.key}
      citation={only.citation}
      callbacks={citationCallbacks}
    />
  );
}

/**
 * Parse a single text string, replacing `[N]` markers with citation components.
 * Consecutive markers pointing at the same recordId — separated only by
 * whitespace — are collapsed into a single InlineCitationGroup.
 */
function parseInlineCitations(
  text: string,
  citationMaps?: CitationMaps,
  citationCallbacks?: CitationCallbacks,
): React.ReactNode[] {
  const citationRegex = /\[{1,2}(\d+)\]{1,2}/g;
  const parts: React.ReactNode[] = [];

  const matches: CitationMatch[] = [];
  let m: RegExpExecArray | null;
  while ((m = citationRegex.exec(text)) !== null) {
    const chunkIndex = parseInt(m[1], 10);
    const citationId = citationMaps?.citationsOrder[chunkIndex];
    const citation = citationId ? citationMaps?.citations[citationId] : undefined;
    matches.push({
      chunkIndex,
      citation,
      index: m.index,
      length: m[0].length,
      key: `${m.index}-${chunkIndex}`,
    });
  }

  if (matches.length === 0) {
    return [text];
  }

  let cursor = 0;
  let i = 0;
  while (i < matches.length) {
    const runStart = matches[i];

    // Emit any plain text before this run starts
    if (runStart.index > cursor) {
      parts.push(text.slice(cursor, runStart.index));
    }

    // Build a run of consecutive markers sharing a recordId, separated only
    // by whitespace. Single markers (or markers whose citation data isn't
    // loaded yet) form runs of length 1.
    const run: CitationMatch[] = [runStart];
    const anchorRecordId = runStart.citation?.recordId;

    if (anchorRecordId) {
      while (i + 1 < matches.length) {
        const prev = run[run.length - 1];
        const next = matches[i + 1];
        const nextRecordId = next.citation?.recordId;
        if (!nextRecordId || nextRecordId !== anchorRecordId) break;

        const gap = text.slice(prev.index + prev.length, next.index);
        // Only whitespace between markers counts as "adjacent"
        if (gap.length > 0 && !/^\s*$/.test(gap)) break;

        run.push(next);
        i += 1;
      }
    }

    const runEnd = run[run.length - 1];
    let afterIndex = runEnd.index + runEnd.length;

    // Emit the run, then handle punctuation-swap on the last marker so
    // "reports[1][2]." reads as "reports. [group]"
    const runNode = emitRun(run, citationCallbacks);
    const nextChar = text[afterIndex];
    if (nextChar && /^[.!?;:,]/.test(nextChar)) {
      parts.push(nextChar);
      parts.push(runNode);
      afterIndex += 1;
    } else {
      parts.push(runNode);
    }

    cursor = afterIndex;
    i += 1;
  }

  if (cursor < text.length) {
    parts.push(text.slice(cursor));
  }

  return parts.length > 0 ? parts : [text];
}

/**
 * Recursively walk React children, replacing `[N]` citations in any string segments.
 */
function processChildren(
  children: React.ReactNode,
  citationMaps?: CitationMaps,
  citationCallbacks?: CitationCallbacks,
): React.ReactNode {
  if (typeof children === 'string') {
    return parseInlineCitations(children, citationMaps, citationCallbacks);
  }

  if (Array.isArray(children)) {
    return children.map((child, i) => {
      if (typeof child === 'string') {
        const parsed = parseInlineCitations(child, citationMaps, citationCallbacks);
        // Wrap in a fragment with key if we produced multiple nodes
        return parsed.length === 1 ? parsed[0] : <React.Fragment key={i}>{parsed}</React.Fragment>;
      }
      return child;
    });
  }

  return children;
}

export function AnswerContent({
  content,
  citationMaps,
  citationCallbacks,
}: AnswerContentProps) {
  // Keep refs to the latest citationMaps/citationCallbacks so the `components`
  // object below can be fully stable (empty useMemo deps). Without this,
  // `components` recreates on every streaming chunk that brings new citation
  // data, causing react-markdown to unmount+remount ALL DOM elements — including
  // table scroll containers (resetting horizontal scroll position) and citation
  // badge buttons (breaking the inline-citation popover anchor).
  const citationMapsRef = useRef(citationMaps);
  citationMapsRef.current = citationMaps;
  const citationCallbacksRef = useRef(citationCallbacks);
  citationCallbacksRef.current = citationCallbacks;

  // Stable `components` — never recreated after mount. Callbacks read from refs
  // at call-time so they always use the latest citationMaps/citationCallbacks.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const components = useMemo(
    () => ({
    h1: ({ children }: { children?: React.ReactNode }) => <AnchorHeading level={1}>{children}</AnchorHeading>,
    h2: ({ children }: { children?: React.ReactNode }) => <AnchorHeading level={2}>{children}</AnchorHeading>,
    h3: ({ children }: { children?: React.ReactNode }) => <AnchorHeading level={3}>{children}</AnchorHeading>,
    h4: ({ children }: { children?: React.ReactNode }) => <AnchorHeading level={4}>{children}</AnchorHeading>,
    h5: ({ children }: { children?: React.ReactNode }) => <AnchorHeading level={5}>{children}</AnchorHeading>,
    h6: ({ children }: { children?: React.ReactNode }) => <AnchorHeading level={6}>{children}</AnchorHeading>,
    p: ({ children }: { children?: React.ReactNode }) => (
      <Text size="2" as="div" style={{ marginBottom: 'var(--space-3)', lineHeight: 1.6, color: 'var(--slate-12)' }}>
        {processChildren(children, citationMapsRef.current, citationCallbacksRef.current)}
      </Text>
    ),
    ul: ({ children, className }: { children?: React.ReactNode; className?: string }) => (
      <ul
        style={{
          paddingLeft: 'var(--space-4)',
          marginBottom: 'var(--space-3)',
          listStyleType: className?.includes('contains-task-list') ? 'none' : 'disc',
        }}
      >
        {children}
      </ul>
    ),
    ol: ({ children }: { children?: React.ReactNode }) => (
      <ol
        style={{
          paddingLeft: 'var(--space-4)',
          marginBottom: 'var(--space-3)',
          listStyleType: 'decimal',
        }}
      >
        {children}
      </ol>
    ),
    li: ({ children, className }: { children?: React.ReactNode; className?: string }) => {
      const isTask = className?.includes('task-list-item');
      return (
        <li
          style={{
            marginBottom: 'var(--space-1)',
            lineHeight: 1.7,
            color: 'var(--slate-12)',
            fontSize: '14px',
            listStyleType: isTask ? 'none' : undefined,
            display: isTask ? 'flex' : undefined,
            alignItems: isTask ? 'flex-start' : undefined,
            gap: isTask ? '6px' : undefined,
          }}
        >
          {processChildren(children, citationMapsRef.current, citationCallbacksRef.current)}
        </li>
      );
    },
    input: ({ type, checked }: { type?: string; checked?: boolean }) => {
      if (type === 'checkbox') {
        return (
          <span
            aria-checked={checked}
            role="checkbox"
            style={{
              display: 'inline-flex',
              width: '15px',
              height: '15px',
              minWidth: '15px',
              borderRadius: '3px',
              border: `1.5px solid ${checked ? 'var(--accent-9)' : 'var(--slate-7)'}`,
              backgroundColor: checked ? 'var(--accent-9)' : 'transparent',
              alignItems: 'center',
              justifyContent: 'center',
              marginTop: '3px',
              cursor: 'default',
              flexShrink: 0,
              transition: 'background-color 0.1s ease, border-color 0.1s ease',
            }}
          >
            {checked && <MaterialIcon name="check" size={10} color="white" />}
          </span>
        );
      }
      return null;
    },
    strong: ({ children }: { children?: React.ReactNode }) => (
      <Text weight="bold" style={{ color: 'var(--slate-12)' }}>
        {processChildren(children, citationMapsRef.current, citationCallbacksRef.current)}
      </Text>
    ),
    em: ({ children }: { children?: React.ReactNode }) => (
      <Text style={{ fontStyle: 'italic' }}>
        {processChildren(children, citationMapsRef.current, citationCallbacksRef.current)}
      </Text>
    ),
    code: ({ children }: { children?: React.ReactNode }) => (
      <code
        style={{
          background: 'var(--slate-4)',
          border: '1px solid var(--slate-6)',
          color: 'var(--slate-12)',
          fontWeight: 400,
          padding: '1px 5px',
          borderRadius: '4px',
          fontFamily: '"Fira Code", "Cascadia Code", Consolas, "Courier New", monospace',
          fontSize: '0.83em',
          lineHeight: 1.6,
          verticalAlign: 'middle',
          whiteSpace: 'pre-wrap',
          letterSpacing: '0.01em',
          wordBreak: 'break-word',
        }}
      >
        {children}
      </code>
    ),
    pre: ({ children }: { children?: React.ReactNode }) => {
      // Detect fenced code blocks by inspecting the inner <code> element
      if (React.isValidElement(children)) {
        const codeEl = children as React.ReactElement<{
          className?: string;
          children?: React.ReactNode;
        }>;
        const lang = typeof codeEl.props?.className === 'string' ? codeEl.props.className : '';

        // ```mermaid — render as an interactive SVG diagram
        if (lang.includes('language-mermaid')) {
          const chartText =
            typeof codeEl.props?.children === 'string' ? codeEl.props.children.trim() : '';
          if (chartText) {
            return <MermaidDiagram chart={chartText} />;
          }
        }

        // ```csv — render as a proper table
        if (lang.includes('language-csv')) {
          const csvText =
            typeof codeEl.props?.children === 'string' ? codeEl.props.children : '';
          if (csvText.trim()) {
            const rows = parseCsvContent(csvText);
            if (rows.length >= 1) {
              const [header, ...dataRows] = rows;
              return (
                <TableFullscreenWrapper>
                  <Box
                    style={{
                      overflowX: 'auto',
                      overflowY: 'auto',
                      maxHeight: 'var(--table-wrapper-max-height, 55vh)',
                      borderRadius: 'var(--radius-2)',
                      border: '1px solid var(--slate-6)',
                    }}
                  >
                    <table
                      style={{
                        minWidth: 'max-content',
                        width: '100%',
                        borderCollapse: 'collapse',
                        fontSize: 'var(--font-size-2)',
                        tableLayout: 'auto',
                      }}
                    >
                      <thead style={{ backgroundColor: 'var(--slate-3)' }}>
                        <tr style={{ borderBottom: '1px solid var(--slate-6)' }}>
                          {header.map((cell, i) => (
                            <th
                              key={i}
                              style={{
                                padding: 'var(--space-2) var(--space-3)',
                                textAlign: 'left',
                                fontWeight: 600,
                                color: 'var(--slate-12)',
                                maxWidth: 'var(--table-cell-max-width, 350px)',
                                wordBreak: 'break-word',
                                overflowWrap: 'anywhere',
                                position: 'sticky',
                                top: 0,
                                zIndex: 2,
                                backgroundColor: 'var(--slate-3)',
                                boxShadow: '0 1px 0 var(--slate-6)',
                              }}
                            >
                              <Text size="2" weight="bold">
                                {parseCsvCellContent(
                                  cell,
                                  citationMapsRef.current,
                                  citationCallbacksRef.current,
                                )}
                              </Text>
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {dataRows.map((row, rowIdx) => (
                          <tr
                            key={rowIdx}
                            style={{ borderBottom: '1px solid var(--slate-6)' }}
                          >
                            {row.map((cell, cellIdx) => (
                              <td
                                key={cellIdx}
                                style={{
                                  padding: 'var(--space-2) var(--space-3)',
                                  color: 'var(--slate-12)',
                                  lineHeight: 1.5,
                                  maxWidth: 'var(--table-cell-max-width, 350px)',
                                  wordBreak: 'break-word',
                                  overflowWrap: 'anywhere',
                                }}
                              >
                                <Text size="2">
                                  {parseCsvCellContent(
                                    cell,
                                    citationMapsRef.current,
                                    citationCallbacksRef.current,
                                  )}
                                </Text>
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </Box>
                </TableFullscreenWrapper>
              );
            }
          }
        }

        // All other fenced code blocks — render with header + copy button
        const rawCode =
          typeof codeEl.props?.children === 'string'
            ? codeEl.props.children
            : extractNodeText(codeEl.props?.children);
        const language = lang.replace('language-', '');
        return <CodeBlock language={language} codeText={rawCode} />;
      }

      // Fallback for bare <pre> without a <code> child (rare)
      return (
        <pre
          className="no-scrollbar"
          style={{
            backgroundColor: 'var(--slate-2)',
            padding: 'var(--space-4)',
            borderRadius: 'var(--radius-2)',
            overflowX: 'auto',
            overflowY: 'auto',
            marginBottom: 'var(--space-3)',
            maxHeight: '420px',
            border: '1px solid var(--slate-5)',
            color: 'var(--slate-12)',
            fontFamily: '"Fira Code", "Cascadia Code", Consolas, "Courier New", monospace',
            fontSize: '13px',
            lineHeight: 1.7,
          }}
        >
          {children}
        </pre>
      );
    },
    blockquote: ({ children, className }: { children?: React.ReactNode; className?: string }) => {
      // remarkCallouts plugin adds 'markdown-alert markdown-alert-{type}' class.
      // Extract type dynamically — never limit to a fixed list.
      if (className?.includes('markdown-alert')) {
        const m = className.match(/markdown-alert-([a-z][a-z0-9_-]*)/i);
        const type = m ? m[1] : '_default';
        return <Callout type={type}>{children}</Callout>;
      }
      return (
        <blockquote
          style={{
            borderLeft: '3px solid var(--accent-9)',
            paddingLeft: 'var(--space-3)',
            marginLeft: 0,
            marginTop: 'var(--space-1)',
            marginBottom: 'var(--space-3)',
            color: 'var(--slate-11)',
            fontSize: 'var(--font-size-2)',
            lineHeight: 1.6,
          }}
        >
          {children}
        </blockquote>
      );
    },
    del: ({ children }: { children?: React.ReactNode }) => (
      <del>{children}</del>
    ),
    hr: () => (
      <hr
        style={{
          border: 'none',
          borderTop: '1px solid var(--slate-5)',
          margin: 'var(--space-4) 0',
        }}
      />
    ),
    img: ({ src, alt }: { src?: string; alt?: string }) => (
      <Box as="span" style={{ margin: 'var(--space-3) 0', textAlign: 'center', display: 'block' }}>
        <img
          src={src}
          alt={alt ?? ''}
          loading="lazy"
          style={{
            maxWidth: '100%',
            height: 'auto',
            borderRadius: 'var(--radius-2)',
            border: '1px solid var(--slate-5)',
            display: 'inline-block',
          }}
        />
        {alt && (
          <Text
            size="1"
            as="span"
            style={{ color: 'var(--slate-10)', marginTop: 'var(--space-1)', fontStyle: 'italic', display: 'block' }}
          >
            {alt}
          </Text>
        )}
      </Box>
    ),
    a: ({ href, children }: { href?: string; children?: React.ReactNode }) => (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        style={{
          color: 'var(--accent-11)',
          textDecoration: 'underline',
          fontSize: 'var(--font-size-2)',
        }}
      >
        {processChildren(children, citationMapsRef.current, citationCallbacksRef.current)}
      </a>
    ),
    table: ({ children }: { children?: React.ReactNode }) => (
      <TableFullscreenWrapper>
        <Box
          style={{
            overflowX: 'auto',
            overflowY: 'auto',
            maxHeight: 'var(--table-wrapper-max-height, 55vh)',
            borderRadius: 'var(--radius-2)',
            border: '1px solid var(--slate-5)',
          }}
        >
          <table
            style={{
              minWidth: 'max-content',
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: 'var(--font-size-2)',
              tableLayout: 'auto',
            }}
          >
            {children}
          </table>
        </Box>
      </TableFullscreenWrapper>
    ),
    thead: ({ children }: { children?: React.ReactNode }) => (
      <thead
        style={{
          backgroundColor: 'var(--slate-4)',
          borderBottom: '1px solid var(--slate-6)',
        }}
      >
        {children}
      </thead>
    ),
    tbody: ({ children }: { children?: React.ReactNode }) => (
      <tbody>{children}</tbody>
    ),
    tr: ({ children }: { children?: React.ReactNode }) => (
      <TableRow>{children}</TableRow>
    ),
    th: ({ children }: { children?: React.ReactNode }) => (
      <th
        style={{
          padding: '8px 12px',
          textAlign: 'left',
          fontWeight: 600,
          color: 'var(--slate-11)',
          maxWidth: 'var(--table-cell-max-width, 320px)',
          position: 'sticky',
          top: 0,
          zIndex: 2,
          backgroundColor: 'var(--slate-4)',
          fontSize: '11px',
          letterSpacing: '0.06em',
          textTransform: 'uppercase',
          whiteSpace: 'nowrap',
          borderRight: '1px solid var(--slate-5)',
          borderBottom: '2px solid var(--slate-6)',
        }}
      >
        {processChildren(children, citationMapsRef.current, citationCallbacksRef.current)}
      </th>
    ),
    td: ({ children }: { children?: React.ReactNode }) => (
      <td
        style={{
          padding: '7px 12px',
          color: 'var(--slate-12)',
          lineHeight: 1.6,
          maxWidth: 'var(--table-cell-max-width, 320px)',
          wordBreak: 'break-word',
          overflowWrap: 'anywhere',
          fontSize: '13px',
          verticalAlign: 'middle',
          borderBottom: '1px solid var(--slate-5)',
          borderRight: '1px solid var(--slate-5)',
        }}
      >
        {processChildren(children, citationMapsRef.current, citationCallbacksRef.current)}
      </td>
    ),
  }),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  []);
  // ^ empty deps: components is stable for the lifetime of this instance.
  // Citation data is read from citationMapsRef/citationCallbacksRef at call-time.

  // Pipeline order matters:
  // 1. preprocessHtmlCodeBlocks  — convert <pre><code> to fenced blocks
  // 2. preprocessHtmlIndentation — strip ≥4-space indent from HTML tag lines
  //    so remark treats them as HTML blocks, not indented code blocks
  // 3. preprocessMath            — \[..\] / \(..\) → $$..$$  /  $..$ 
  //    (skips fenced blocks created by step 1)
  const normalizedContent = preprocessMath(
    preprocessHtmlIndentation(
      preprocessHtmlCodeBlocks(content)
    )
  );

  return (
    <Box>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath, remarkCallouts]}
        rehypePlugins={[rehypeRaw, [rehypeSanitize, SANITIZE_SCHEMA], rehypeKatex]}
        components={components}
      >
        {normalizedContent}
      </ReactMarkdown>
    </Box>
  );
}
