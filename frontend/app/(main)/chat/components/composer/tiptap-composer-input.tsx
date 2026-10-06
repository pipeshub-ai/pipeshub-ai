'use client';

import React, {
  forwardRef,
  useCallback,
  useEffect,
  useId,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useTranslation } from 'react-i18next';
import { EditorContent, ReactNodeViewRenderer, useEditor, type Editor } from '@tiptap/react';
import { Extension } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import Document from '@tiptap/extension-document';
import Mention from '@tiptap/extension-mention';
import Placeholder from '@tiptap/extension-placeholder';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { findSuggestionMatch, type SuggestionKeyDownProps, type SuggestionProps } from '@tiptap/suggestion';
import type { ComposerInputHandle, ComposerInputProps, MentionRef } from './composer-input.types';
import { MentionChipView } from './mention-chip';
import { useAddPeopleStore } from '@/chat/mentions/add-people-store';
import { MentionPopover, mentionOptionId, type MentionAnchorRect } from './mention-popover';
import { fromWire, toWire, type ComposerDocNode } from './mention-serializer';
import {
  cachedMentionLabel,
  rememberMentionLabels,
  useMentionables,
  type Mentionable,
} from './use-mentionables';

const DEFAULT_MAX_HEIGHT_PX = 120;

export interface TiptapComposerInputProps extends ComposerInputProps {
  /** Dimmed text, used while the composer sits underneath an open overlay panel. Not part of the port. */
  muted?: boolean;
  /**
   * Whether the container will turn this paste into an attachment chip (files, or text large enough to
   * collapse). The editor then inserts nothing and leaves the event to the container's `onPaste`.
   */
  shouldDeferPaste?: (data: DataTransfer | null) => boolean;
}

interface SuggestionState {
  /** Deletes the `@query` text and returns the caret to where it was typed. */
  remove(): void;
  query: string;
  rect: MentionAnchorRect | null;
  from: number;
  select(item: Mentionable): void;
}

interface ChosenMention {
  id: string;
  label: string;
  mentionType: MentionRef['type'];
}

/** A name has a first, middle and last part and maybe a suffix; longer text is a sentence. */
const MAX_QUERY_WORDS = 4;
const MAX_QUERY_CHARS = 64;
const INTERIM_KEY = new PluginKey('composerInterim');
const NAV_KEYS = new Set(['ArrowDown', 'ArrowUp', 'Enter', 'Tab', 'Escape']);

function toRect(rect: DOMRect | null | undefined): MentionAnchorRect | null {
  return rect ? { left: rect.left, top: rect.top, width: rect.width, height: rect.height } : null;
}

function plainTextNodes(text: string): ComposerDocNode[] {
  const nodes: ComposerDocNode[] = [];
  text
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .forEach((line, i) => {
      if (i > 0) nodes.push({ type: 'hardBreak' });
      if (line) nodes.push({ type: 'text', text: line });
    });
  return nodes;
}

function agentIdsIn(editor: Editor): string[] {
  const ids = toWire(editor.getJSON() as ComposerDocNode).mentions.filter((m) => m.type === 'agent').map((m) => m.id);
  return [...new Set(ids)];
}

function hasUnlabelledMention(editor: Editor): boolean {
  let found = false;
  editor.state.doc.descendants((node) => {
    if (node.type.name === 'mention' && !node.attrs.label) found = true;
    return !found;
  });
  return found;
}

/**
 * Single-paragraph rich input behind the `ComposerInput` port. Its `value` is wire text (`<@type:id>` tokens),
 * so the container's state, drafts and `message.trim()` checks work exactly as with the textarea.
 */
export const TiptapComposerInput = forwardRef<ComposerInputHandle, TiptapComposerInputProps>(
  function TiptapComposerInput(
    {
      value,
      interim,
      readOnly,
      placeholder,
      maxHeightPx = DEFAULT_MAX_HEIGHT_PX,
      onChange,
      onKeyDown,
      onFocus,
      onBlur,
      ariaLabel,
      muted,
      shouldDeferPaste,
    },
    ref,
  ) {
    const { t } = useTranslation();
    const listboxId = useId().replace(/:/g, '');
    const assistantLabel = t('chat.mentions.assistant', { defaultValue: 'Assistant' });

    const latest = useRef({ onChange, onKeyDown, shouldDeferPaste, placeholder, interim: interim ?? '' });
    useEffect(() => {
      latest.current = { onChange, onKeyDown, shouldDeferPaste, placeholder, interim: interim ?? '' };
    });

    const lastEmitted = useRef(value);
    const editorRef = useRef<Editor | null>(null);
    const pendingFocus = useRef(false);
    const consumedKey = useRef<Event | null>(null);

    const [suggestion, setSuggestion] = useState<SuggestionState | null>(null);
    const [dismissedFrom, setDismissedFrom] = useState<number | null>(null);
    const [activeIndex, setActiveIndex] = useState(0);
    const [labelsWanted, setLabelsWanted] = useState(false);

    const wanted = suggestion !== null && suggestion.from !== dismissedFrom;
    // Read when the popover opens or its query changes, which is when the doc last changed.
    const agentKey = wanted && editorRef.current ? agentIdsIn(editorRef.current).join('\u0000') : '';
    const selectedAgentIds = useMemo(() => (agentKey ? agentKey.split('\u0000') : []), [agentKey]);
    const { items, loading, settled } = useMentionables({
      query: wanted ? (suggestion?.query ?? '') : '',
      enabled: wanted || labelsWanted,
      assistantLabel,
      selectedAgentIds,
    });
    // A name with spaces keeps the list open only while someone matches it; "@assistant thanks" is a message, not a search.
    const matched = items.filter((i) => !i.action).length;
    const open = wanted && !(/\s/.test(suggestion?.query.trimStart() ?? '') && settled && matched === 0);
    const visibleItems = open ? items : [];
    // Only the "add people" row is left: it is not the default, so Enter still sends what was typed unless the user moved onto it.
    const [navigated, setNavigated] = useState(false);
    const shownIndex = matched === 0 && !navigated ? -1 : activeIndex;

    const view = useRef({ open, items: visibleItems, activeIndex, suggestion, navigated, matched });
    useEffect(() => {
      view.current = { open, items: visibleItems, activeIndex, suggestion, navigated, matched };
    });

    const addPeople = useCallback(() => {
      const query = view.current.suggestion?.query.trim() ?? '';
      view.current.suggestion?.remove();
      useAddPeopleStore.getState().request(query);
    }, []);

    const selectItem = useCallback(
      (item: Mentionable) => {
        if (item.disabled) return;
        if (item.action) addPeople();
        else view.current.suggestion?.select(item);
      },
      [addPeople],
    );

    const bridge = useRef({
      onStart: (p: SuggestionProps) => {
        setSuggestion({
          query: p.query,
          rect: toRect(p.clientRect?.()),
          from: p.range.from,
          remove: () => {
            p.editor.chain().focus().deleteRange(p.range).run();
          },
          select: (item) => {
            p.command({ id: item.ref.id, label: item.label, mentionType: item.ref.type });
          },
        });
        setActiveIndex(0);
        setNavigated(false);
      },
      onUpdate: (p: SuggestionProps) => {
        setSuggestion({
          query: p.query,
          rect: toRect(p.clientRect?.()),
          from: p.range.from,
          remove: () => {
            p.editor.chain().focus().deleteRange(p.range).run();
          },
          select: (item) => {
            p.command({ id: item.ref.id, label: item.label, mentionType: item.ref.type });
          },
        });
        setActiveIndex(0);
        setNavigated(false);
      },
      onExit: () => {
        setSuggestion(null);
        setDismissedFrom(null);
      },
      onKeyDown: ({ event }: SuggestionKeyDownProps): boolean => {
        const v = view.current;
        if (!v.open) return false;
        const n = v.items.length;
        switch (event.key) {
          case 'ArrowDown':
            if (n === 0) return false;
            setNavigated(true);
            setActiveIndex((i) => (i + 1) % n);
            break;
          case 'ArrowUp':
            if (n === 0) return false;
            setNavigated(true);
            setActiveIndex((i) => (i - 1 + n) % n);
            break;
          case 'Enter':
          case 'Tab': {
            const item = v.items[Math.min(v.activeIndex, n - 1)];
            if (!item) return false;
            if (item.action && !v.navigated && v.matched === 0) return false;
            if (item.action) addPeople();
            else if (!item.disabled) v.suggestion?.select(item);
            break;
          }
          case 'Escape':
            setDismissedFrom(v.suggestion?.from ?? null);
            break;
          default:
            return false;
        }
        consumedKey.current = event;
        return true;
      },
    });

    const extensions = useMemo(() => {
      const Interim = Extension.create({
        name: 'composerInterim',
        addProseMirrorPlugins() {
          return [
            new Plugin({
              key: INTERIM_KEY,
              props: {
                decorations(state) {
                  const text = latest.current.interim;
                  if (!text) return null;
                  const end = state.doc.content.size - 1;
                  const lead = state.doc.textContent.length > 0 ? ' ' : '';
                  return DecorationSet.create(state.doc, [
                    Decoration.widget(
                      end,
                      () => {
                        const span = document.createElement('span');
                        span.className = 'ph-composer-interim';
                        span.contentEditable = 'false';
                        span.textContent = lead + text;
                        return span;
                      },
                      { side: 1, key: `interim:${lead}${text}` },
                    ),
                  ]);
                },
              },
            }),
          ];
        },
      });

      const ComposerMention = Mention.extend({
        addAttributes() {
          return {
            id: { default: null },
            label: { default: null },
            mentionType: { default: 'user' },
          };
        },
        addKeyboardShortcuts() {
          // The stock Backspace handler of the mention extension also eats the character after the chip.
          return {
            Backspace: () =>
              this.editor.commands.command(({ tr, state }) => {
                const { empty, anchor } = state.selection;
                if (!empty) return false;
                let removed = false;
                state.doc.nodesBetween(anchor - 1, anchor, (node, pos) => {
                  if (node.type.name !== this.name) return;
                  tr.delete(pos, pos + node.nodeSize);
                  removed = true;
                  return false;
                });
                return removed;
              }),
          };
        },
        renderText({ node }) {
          return `@${node.attrs.label || ''}`;
        },
        renderHTML({ node }) {
          return ['span', { 'data-type': 'mention', 'data-mention-type': node.attrs.mentionType }, `@${node.attrs.label || ''}`];
        },
        addNodeView() {
          return ReactNodeViewRenderer(MentionChipView, { as: 'span' });
        },
      });

      return [
        Document.extend({ content: 'paragraph' }),
        StarterKit.configure({
          document: false,
          heading: false,
          bulletList: false,
          orderedList: false,
          listItem: false,
          codeBlock: false,
          blockquote: false,
          horizontalRule: false,
          bold: false,
          italic: false,
          strike: false,
          code: false,
          dropcursor: false,
          gapcursor: false,
        }),
        Placeholder.configure({ placeholder: () => latest.current.placeholder }),
        Interim,
        ComposerMention.configure({
          suggestion: {
            char: '@',
            allowSpaces: true,
            findSuggestionMatch: (config) => {
              const match = findSuggestionMatch(config);
              return match && match.query.trim().split(/\s+/).length <= MAX_QUERY_WORDS && match.query.length <= MAX_QUERY_CHARS ? match : null;
            },
            items: () => [],
            command: ({ editor, range, props: raw }) => {
              const props = raw as unknown as ChosenMention;
              editor
                .chain()
                .focus()
                .insertContentAt(range, [
                  { type: 'mention', attrs: { id: props.id, label: props.label, mentionType: props.mentionType } },
                  { type: 'text', text: ' ' },
                ])
                .run();
              rememberMentionLabels([{ ref: { type: props.mentionType, id: props.id }, label: props.label }]);
            },
            render: () => ({
              onStart: (p) => bridge.current.onStart(p),
              onUpdate: (p) => bridge.current.onUpdate(p),
              onExit: () => bridge.current.onExit(),
              onKeyDown: (p) => bridge.current.onKeyDown(p),
            }),
          },
        }),
      ];
    }, []);

    const labelFor = useCallback((m: MentionRef) => cachedMentionLabel(m), []);

    const editor = useEditor({
      extensions,
      content: fromWire(value, labelFor),
      editable: !readOnly,
      immediatelyRender: false,
      shouldRerenderOnTransaction: false,
      editorProps: {
        attributes: {
          role: 'textbox',
          'aria-multiline': 'true',
          'data-testid': 'chat-composer',
          'aria-label': ariaLabel,
        },
        handleKeyDown: (_view, event) => {
          if (event.isComposing || event.keyCode === 229) return false;
          if (view.current.open && NAV_KEYS.has(event.key)) return false;
          return event.key === 'Enter' && !event.shiftKey;
        },
        handlePaste: (_view, event) => {
          const data = event.clipboardData;
          if (latest.current.shouldDeferPaste?.(data)) return true;
          const text = data?.getData('text/plain') ?? '';
          if (!text) return false;
          editorRef.current?.commands.insertContent(plainTextNodes(text));
          return true;
        },
      },
      onUpdate: ({ editor: e }) => {
        const wire = toWire(e.getJSON() as ComposerDocNode).text;
        lastEmitted.current = wire;
        latest.current.onChange(wire);
      },
      onFocus: () => onFocus?.(),
      onBlur: () => {
        setDismissedFrom(view.current.suggestion?.from ?? null);
        onBlur?.();
      },
      onCreate: ({ editor: e }) => {
        editorRef.current = e;
        if (hasUnlabelledMention(e)) setLabelsWanted(true);
        if (pendingFocus.current) {
          pendingFocus.current = false;
          e.commands.focus('end');
        }
      },
      onDestroy: () => {
        editorRef.current = null;
      },
    });

    useEffect(() => {
      if (!editor || value === lastEmitted.current) return;
      lastEmitted.current = value;
      editor.commands.setContent(fromWire(value, labelFor), false);
      if (editor.isFocused) editor.commands.focus('end');
      setLabelsWanted(hasUnlabelledMention(editor));
    }, [editor, value, labelFor]);

    useEffect(() => {
      editor?.setEditable(!readOnly);
    }, [editor, readOnly]);

    useEffect(() => {
      if (!editor) return;
      editor.view.dom.setAttribute('aria-label', ariaLabel);
      editor.view.dom.setAttribute('aria-readonly', readOnly ? 'true' : 'false');
    }, [editor, ariaLabel, readOnly]);

    useEffect(() => {
      if (!editor || editor.isDestroyed) return;
      editor.view.dispatch(editor.state.tr.setMeta(INTERIM_KEY, interim ?? ''));
    }, [editor, interim, placeholder]);

    useEffect(() => {
      if (!editor) return;
      const dom = editor.view.dom;
      if (open) {
        dom.setAttribute('aria-controls', listboxId);
        dom.setAttribute('aria-haspopup', 'listbox');
        dom.setAttribute('aria-autocomplete', 'list');
        if (visibleItems.length > 0 && shownIndex >= 0) {
          dom.setAttribute('aria-activedescendant', mentionOptionId(listboxId, shownIndex));
        } else {
          dom.removeAttribute('aria-activedescendant');
        }
      } else {
        ['aria-controls', 'aria-haspopup', 'aria-autocomplete', 'aria-activedescendant'].forEach((a) => dom.removeAttribute(a));
      }
    }, [editor, open, listboxId, shownIndex, visibleItems.length]);

    useEffect(() => {
      if (!editor || items.length === 0) return;
      rememberMentionLabels(items);
      if (!labelsWanted) return;
      const { tr } = editor.state;
      let changed = false;
      editor.state.doc.descendants((node, pos) => {
        if (node.type.name !== 'mention' || node.attrs.label) return;
        const label = cachedMentionLabel({ type: node.attrs.mentionType as MentionRef['type'], id: node.attrs.id as string });
        if (!label) return;
        tr.setNodeMarkup(pos, undefined, { ...node.attrs, label });
        changed = true;
      });
      if (changed) editor.view.dispatch(tr.setMeta('addToHistory', false));
      setLabelsWanted(hasUnlabelledMention(editor));
    }, [editor, items, labelsWanted]);

    useEffect(() => {
      if (open && activeIndex >= visibleItems.length) setActiveIndex(0);
    }, [open, activeIndex, visibleItems.length]);

    useImperativeHandle(
      ref,
      () => ({
        focus: () => {
          const e = editorRef.current;
          if (e) e.commands.focus('end');
          else pendingFocus.current = true;
        },
        clear: () => {
          lastEmitted.current = '';
          editorRef.current?.commands.clearContent(false);
          latest.current.onChange('');
        },
        setText: (text) => {
          lastEmitted.current = text;
          editorRef.current?.commands.setContent(fromWire(text, labelFor), false);
          latest.current.onChange(text);
        },
        insertMention: (m, label) => {
          rememberMentionLabels([{ ref: m, label }]);
          editorRef.current
            ?.chain()
            .focus()
            .insertContent([
              { type: 'mention', attrs: { id: m.id, label, mentionType: m.type } },
              { type: 'text', text: ' ' },
            ])
            .run();
        },
        insertText: (text) => {
          editorRef.current?.chain().focus().insertContent(text).run();
        },
        getValue: () => {
          const e = editorRef.current;
          return e ? toWire(e.getJSON() as ComposerDocNode) : { text: lastEmitted.current, mentions: [] };
        },
      }),
      [labelFor],
    );

    return (
      <div
        className="ph-composer-editor"
        data-muted={muted ? 'true' : undefined}
        data-readonly={readOnly ? 'true' : undefined}
        style={{ maxHeight: maxHeightPx, overflowY: 'auto' }}
        onKeyDown={(e) => {
          if (consumedKey.current === e.nativeEvent) return;
          latest.current.onKeyDown(e);
        }}
      >
        <EditorContent editor={editor} />
        <MentionPopover
          open={open}
          anchorRect={suggestion?.rect ?? null}
          items={visibleItems}
          activeIndex={shownIndex}
          listboxId={listboxId}
          loading={loading}
          onSelect={selectItem}
          onActiveChange={setActiveIndex}
          onDismiss={() => setDismissedFrom(suggestion?.from ?? null)}
          onEscape={(event) => {
            consumedKey.current = event;
            setDismissedFrom(view.current.suggestion?.from ?? null);
          }}
        />
      </div>
    );
  },
);
