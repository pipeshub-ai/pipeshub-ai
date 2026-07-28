'use client';

import { useEffect, useRef } from 'react';
import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Link from '@tiptap/extension-link';
import Placeholder from '@tiptap/extension-placeholder';
import { Markdown } from 'tiptap-markdown';
import { Flex, IconButton, Separator, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

// ========================================
// Props
// ========================================

interface MarkdownEditorProps {
  /** SKILL.md body — GitHub-flavored markdown, no YAML frontmatter (that lives in the metadata tab). */
  value: string;
  onChange: (markdown: string) => void;
  placeholder?: string;
  editable?: boolean;
  minHeight?: number;
}

// ========================================
// Component
// ========================================

/**
 * Rich-text editor over a skill's SKILL.md body, serialized to/from GFM
 * markdown via `tiptap-markdown` — the editor never sees or produces YAML
 * frontmatter; the backend (`_build_content` in `api/routes/skills.py`)
 * is the single place structured fields + body become a full SKILL.md file.
 */
export function MarkdownEditor({
  value,
  onChange,
  placeholder,
  editable = true,
  minHeight = 280,
}: MarkdownEditorProps) {
  // Tracks whether the last `setContent` was from an external `value` change
  // (e.g. switching skills) vs. our own `onUpdate` echoing back — avoids a
  // feedback loop that would otherwise reset the cursor on every keystroke.
  const lastEmitted = useRef<string>(value);

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ heading: { levels: [1, 2, 3] } }),
      Link.configure({ openOnClick: false, autolink: true }),
      Placeholder.configure({ placeholder: placeholder ?? 'Describe how to perform this skill…' }),
      Markdown.configure({ html: false, transformCopiedText: true }),
    ],
    content: value,
    editable,
    immediatelyRender: false,
    onUpdate: ({ editor: e }) => {
      const markdown = e.storage.markdown.getMarkdown();
      lastEmitted.current = markdown;
      onChange(markdown);
    },
  });

  useEffect(() => {
    if (!editor) return;
    if (value === lastEmitted.current) return;
    lastEmitted.current = value;
    editor.commands.setContent(value, false);
  }, [value, editor]);

  useEffect(() => {
    editor?.setEditable(editable);
  }, [editable, editor]);

  if (!editor) return null;

  return (
    <Flex
      direction="column"
      style={{
        border: '1px solid var(--olive-4)',
        borderRadius: 'var(--radius-2)',
        overflow: 'hidden',
        flex: 1,
        transition: 'border-color 150ms ease',
      }}
      className="ph-skill-editor-container"
    >
      {editable && <MarkdownEditorToolbar editor={editor} />}
      <div
        style={{
          minHeight,
          padding: 'var(--space-3) var(--space-4)',
          cursor: 'text',
          overflowY: 'auto',
        }}
        onClick={() => editor.chain().focus().run()}
      >
        <EditorContent editor={editor} className="ph-skill-markdown-editor" />
      </div>
    </Flex>
  );
}

// ========================================
// Toolbar
// ========================================

function MarkdownEditorToolbar({ editor }: { editor: ReturnType<typeof useEditor> }) {
  if (!editor) return null;

  const items: { icon: string; label: string; action: () => void; isActive: () => boolean }[] = [
    { icon: 'format_bold', label: 'Bold (Ctrl+B)', action: () => editor.chain().focus().toggleBold().run(), isActive: () => editor.isActive('bold') },
    { icon: 'format_italic', label: 'Italic (Ctrl+I)', action: () => editor.chain().focus().toggleItalic().run(), isActive: () => editor.isActive('italic') },
    { icon: 'code', label: 'Inline code', action: () => editor.chain().focus().toggleCode().run(), isActive: () => editor.isActive('code') },
    { icon: 'title', label: 'Heading 2', action: () => editor.chain().focus().toggleHeading({ level: 2 }).run(), isActive: () => editor.isActive('heading', { level: 2 }) },
    { icon: 'format_list_bulleted', label: 'Bullet list', action: () => editor.chain().focus().toggleBulletList().run(), isActive: () => editor.isActive('bulletList') },
    { icon: 'format_list_numbered', label: 'Numbered list', action: () => editor.chain().focus().toggleOrderedList().run(), isActive: () => editor.isActive('orderedList') },
    { icon: 'code_blocks', label: 'Code block', action: () => editor.chain().focus().toggleCodeBlock().run(), isActive: () => editor.isActive('codeBlock') },
    { icon: 'format_quote', label: 'Blockquote', action: () => editor.chain().focus().toggleBlockquote().run(), isActive: () => editor.isActive('blockquote') },
    { icon: 'horizontal_rule', label: 'Horizontal rule', action: () => editor.chain().focus().setHorizontalRule().run(), isActive: () => false },
  ];

  return (
    <Flex
      align="center"
      gap="1"
      wrap="wrap"
      style={{
        padding: 'var(--space-1) var(--space-2)',
        borderBottom: '1px solid var(--olive-3)',
        background: 'var(--gray-a2)',
      }}
    >
      {items.map((item) => (
        <Tooltip key={item.icon} content={item.label}>
          <IconButton
            type="button"
            variant={item.isActive() ? 'solid' : 'ghost'}
            color={item.isActive() ? undefined : 'gray'}
            size="1"
            onClick={item.action}
            style={{ cursor: 'pointer' }}
          >
            <MaterialIcon name={item.icon} size={15} color={item.isActive() ? 'white' : 'var(--slate-11)'} />
          </IconButton>
        </Tooltip>
      ))}
      <Separator orientation="vertical" size="1" style={{ margin: '0 4px' }} />
      <Tooltip content="Undo">
        <IconButton type="button" variant="ghost" color="gray" size="1" onClick={() => editor.chain().focus().undo().run()} style={{ cursor: 'pointer' }}>
          <MaterialIcon name="undo" size={15} color="var(--slate-11)" />
        </IconButton>
      </Tooltip>
      <Tooltip content="Redo">
        <IconButton type="button" variant="ghost" color="gray" size="1" onClick={() => editor.chain().focus().redo().run()} style={{ cursor: 'pointer' }}>
          <MaterialIcon name="redo" size={15} color="var(--slate-11)" />
        </IconButton>
      </Tooltip>
    </Flex>
  );
}
