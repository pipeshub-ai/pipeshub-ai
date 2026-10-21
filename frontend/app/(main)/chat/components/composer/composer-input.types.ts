import type React from 'react';
import type { ChatKnowledgeFilters } from '@/chat/types';
import type { MentionParent } from '@/chat/components/mention-composer-field';
import type { MentionNode } from '@/chat/utils/node-mentions';

export interface ComposerValue {
  text: string;
}

export interface ComposerInputHandle {
  focus(): void;
  clear(): void;
  setText(t: string): void;
  /** Types text at the caret as the user would. */
  insertText(text: string): void;
  getValue(): ComposerValue;
}

export interface ComposerInputProps {
  value: string;
  interim?: string;
  readOnly?: boolean;
  placeholder: string;
  maxHeightPx?: number;
  onChange(text: string): void;
  onKeyDown(e: React.KeyboardEvent): void;
  onFocus?(): void;
  onBlur?(): void;
  ariaLabel: string;
  /** Knowledge nodes embedded with `@`. Without `onMentionsChange`, `@` is plain text. */
  mentions?: readonly MentionNode[];
  onMentionsChange?(mentions: MentionNode[]): void;
  /** `@` searches under this node; omitted, it searches every node the user can reach. */
  mentionParent?: MentionParent | null;
  /** `@` is plain text: no menu opens. */
  mentionsOff?: boolean;
  /** The chat's scope: with it, the `@` menu's checkboxes tick nodes into it. */
  mentionSelection?: ChatKnowledgeFilters;
  onMentionSelectionChange?(next: ChatKnowledgeFilters): void;
}
