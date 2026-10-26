import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, waitFor, act, fireEvent } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import type { Editor } from '@tiptap/core';
import { MarkdownEditor } from '../markdown-editor';

afterEach(() => cleanup());

// jsdom has no layout, and ProseMirror measures the selection range when a
// toolbar command scrolls it into view.
if (!Range.prototype.getClientRects) {
  Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
  Range.prototype.getBoundingClientRect = () => new DOMRect();
}

const h = React.createElement;

// Covers every construct the toolbar can produce plus a document that ends in
// a fenced block, which is where trailing-paragraph handling would show up.
const SKILL_BODY = [
  '# Title',
  '',
  '## Section',
  '',
  'Plain text with **bold**, *italic*, `code` and a [link](https://example.com/a).',
  '',
  '- one',
  '- two',
  '',
  '1. first',
  '2. second',
  '',
  '> quoted',
  '',
  '---',
  '',
  '```python',
  'print("hi")',
  '```',
].join('\n');

function renderEditor(value: string, onChange = vi.fn()) {
  const utils = render(h(Theme, null, h(MarkdownEditor, { value, onChange })));
  return { ...utils, onChange };
}

// Tiptap attaches the instance to its root element; the component does not
// expose it any other way.
async function getEditor(container: HTMLElement): Promise<Editor> {
  let editor: Editor | undefined;
  await waitFor(() => {
    const dom = container.querySelector('.ProseMirror') as (HTMLElement & { editor?: Editor }) | null;
    editor = dom?.editor;
    expect(editor).toBeDefined();
  });
  return editor as Editor;
}

function markdownOf(editor: Editor): string {
  return (editor.storage as unknown as { markdown: { getMarkdown: () => string } }).markdown.getMarkdown();
}

function toolbarButton(container: HTMLElement, icon: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === icon);
  if (!button) throw new Error(`no toolbar button for ${icon}`);
  return button;
}

describe('MarkdownEditor', () => {
  it('round-trips a SKILL.md body byte for byte', async () => {
    const { container, onChange } = renderEditor(SKILL_BODY);
    const editor = await getEditor(container);

    expect(markdownOf(editor)).toBe(SKILL_BODY);
    // tiptap-markdown re-parses the initial content once on create, which may
    // emit; it must never hand back a normalised copy of what was loaded.
    for (const [markdown] of onChange.mock.calls) expect(markdown).toBe(SKILL_BODY);
  });

  it('does not emit onChange when the value changes from outside', async () => {
    const onChange = vi.fn();
    const { container, rerender } = renderEditor('first skill', onChange);
    const editor = await getEditor(container);
    onChange.mockClear();

    rerender(h(Theme, null, h(MarkdownEditor, { value: SKILL_BODY, onChange })));

    await waitFor(() => expect(markdownOf(editor)).toBe(SKILL_BODY));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('bolds the selection from the toolbar and keeps the button state in step with the caret', async () => {
    const { container, onChange } = renderEditor('alpha beta');
    const editor = await getEditor(container);
    const bold = () => toolbarButton(container, 'format_bold');

    act(() => {
      editor.commands.setTextSelection({ from: 1, to: 6 });
    });
    fireEvent.click(bold());

    await waitFor(() => expect(onChange).toHaveBeenLastCalledWith('**alpha** beta'));
    await waitFor(() => expect(bold().className).toContain('rt-variant-solid'));

    act(() => {
      editor.commands.setTextSelection(9);
    });
    await waitFor(() => expect(bold().className).toContain('rt-variant-ghost'));
  });

  it('configures a single link extension that does not open on click and autolinks', async () => {
    const { container } = renderEditor('text');
    const editor = await getEditor(container);

    const links = editor.extensionManager.extensions.filter((e) => e.name === 'link');
    expect(links).toHaveLength(1);
    expect(links[0].options).toMatchObject({ openOnClick: false, autolink: true });
  });

  it('has no underline mark, since markdown has no syntax to save it', async () => {
    const { container } = renderEditor('text');
    const editor = await getEditor(container);

    expect(editor.schema.marks.underline).toBeUndefined();
  });
});
