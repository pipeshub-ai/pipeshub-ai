import type { ComposerValue, MentionRef } from './composer-input.types';

/** Minimal ProseMirror JSON the composer uses: one paragraph of text, hard breaks and mention atoms. */
export interface ComposerDocNode {
  type: string;
  text?: string;
  attrs?: Record<string, unknown>;
  content?: ComposerDocNode[];
}

export type MentionLabels =
  | Record<string, string | undefined>
  | ((mention: MentionRef) => string | undefined);

export const MENTION_TYPES = ['assistant', 'agent', 'user', 'team'] as const;

const TOKEN_SOURCE = `<@(${MENTION_TYPES.join('|')}):([A-Za-z0-9_-]{1,128})>`;
const ESCAPE_RE = /<(\\*)@/g;
const UNESCAPE_RE = /<\\(\\*)@/g;

export const mentionKey = (m: MentionRef): string => `${m.type}:${m.id}`;

export function toToken(m: MentionRef): string {
  return `<@${m.type}:${m.id}>`;
}

/**
 * Makes `<@` in free text inert: it gains a backslash (`<\@`), and any run of backslashes already
 * there gains one more, so `unescapeLiteralTokens` is an exact inverse.
 */
export function escapeLiteralTokens(text: string): string {
  return text.replace(ESCAPE_RE, (_m, slashes: string) => `<\\${slashes}@`);
}

export function unescapeLiteralTokens(text: string): string {
  return text.replace(UNESCAPE_RE, (_m, slashes: string) => `<${slashes}@`);
}

function isMentionType(v: unknown): v is MentionRef['type'] {
  return typeof v === 'string' && (MENTION_TYPES as readonly string[]).includes(v);
}

/** Wire text (`<@type:id>` tokens, escaped literals, `\n` for line breaks) and the mentions it carries, in order. */
export function toWire(doc: ComposerDocNode | null | undefined): ComposerValue {
  const mentions: MentionRef[] = [];
  const seen = new Set<string>();
  let text = '';
  const walk = (node: ComposerDocNode, isFirstBlock: boolean) => {
    switch (node.type) {
      case 'text':
        text += escapeLiteralTokens(node.text ?? '');
        return;
      case 'hardBreak':
        text += '\n';
        return;
      case 'mention': {
        const id = node.attrs?.id;
        const type = node.attrs?.mentionType;
        if (typeof id !== 'string' || !id || !isMentionType(type)) return;
        const ref: MentionRef = { type, id };
        text += toToken(ref);
        if (!seen.has(mentionKey(ref))) {
          seen.add(mentionKey(ref));
          mentions.push(ref);
        }
        return;
      }
      default: {
        if (node.type === 'paragraph' && !isFirstBlock) text += '\n';
        node.content?.forEach((child, i) => walk(child, node.type === 'doc' && i === 0));
      }
    }
  };
  if (doc) walk(doc, true);
  return { text, mentions };
}

function resolveLabel(labels: MentionLabels | undefined, ref: MentionRef): string {
  if (!labels) return '';
  return (typeof labels === 'function' ? labels(ref) : labels[mentionKey(ref)]) ?? '';
}

function textNodes(raw: string): ComposerDocNode[] {
  const out: ComposerDocNode[] = [];
  raw.split('\n').forEach((line, i) => {
    if (i > 0) out.push({ type: 'hardBreak' });
    if (line) out.push({ type: 'text', text: unescapeLiteralTokens(line) });
  });
  return out;
}

/** Inverse of `toWire`, for prefilling the editor (edit, regenerate, drafts). A missing label leaves the chip to render "Unknown". */
export function fromWire(text: string, labels?: MentionLabels): ComposerDocNode {
  const content: ComposerDocNode[] = [];
  const re = new RegExp(TOKEN_SOURCE, 'g');
  let last = 0;
  for (let match = re.exec(text); match; match = re.exec(text)) {
    if (match.index > last) content.push(...textNodes(text.slice(last, match.index)));
    const ref: MentionRef = { type: match[1] as MentionRef['type'], id: match[2] };
    content.push({
      type: 'mention',
      attrs: { id: ref.id, mentionType: ref.type, label: resolveLabel(labels, ref) },
    });
    last = match.index + match[0].length;
  }
  if (last < text.length) content.push(...textNodes(text.slice(last)));
  return { type: 'doc', content: [{ type: 'paragraph', ...(content.length ? { content } : {}) }] };
}
