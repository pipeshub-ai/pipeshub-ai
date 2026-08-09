'use client';

import React, { useEffect, useRef } from 'react';
import { EditorView, keymap } from '@codemirror/view';
import { EditorState } from '@codemirror/state';
import { python } from '@codemirror/lang-python';
import { oneDark } from '@codemirror/theme-one-dark';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';

interface WorkflowEditorProps {
  source: string;
  readOnly?: boolean;
  onChange?: (value: string) => void;
  height?: string;
  /**
   * 1-based line to scroll to and select. Changing it re-reveals the line, so
   * clicking the same graph node twice still brings the reader back to it.
   */
  revealLine?: { line: number; nonce: number } | null;
}

export function WorkflowEditor({
  source,
  readOnly = false,
  onChange,
  height = '400px',
  revealLine = null,
}: WorkflowEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const extensions = [
      python(),
      oneDark,
      history(),
      keymap.of([...defaultKeymap, ...historyKeymap]),
    ];

    if (readOnly) {
      extensions.push(EditorView.editable.of(false));
    } else if (onChange) {
      extensions.push(
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            onChange(update.state.doc.toString());
          }
        })
      );
    }

    const state = EditorState.create({
      doc: source,
      extensions,
    });

    viewRef.current = new EditorView({ state, parent: containerRef.current });

    return () => {
      viewRef.current?.destroy();
    };
    // intentionally omit `source` and `onChange` — editor owns the doc after mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync external source changes into the editor without destroying the instance
  useEffect(() => {
    if (!viewRef.current) return;
    const currentContent = viewRef.current.state.doc.toString();
    if (currentContent !== source) {
      viewRef.current.dispatch({
        changes: {
          from: 0,
          to: currentContent.length,
          insert: source,
        },
      });
    }
  }, [source]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view || !revealLine) return;
    const lineNumber = Math.min(Math.max(revealLine.line, 1), view.state.doc.lines);
    const line = view.state.doc.line(lineNumber);
    view.dispatch({
      selection: { anchor: line.from, head: line.to },
      effects: EditorView.scrollIntoView(line.from, { y: 'center' }),
    });
    view.focus();
  }, [revealLine]);

  return (
    <div
      ref={containerRef}
      style={{
        height,
        overflow: 'auto',
        borderRadius: 'var(--radius-3)',
        border: '1px solid var(--gray-a6)',
        fontSize: '13px',
      }}
    />
  );
}
