'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Box, Flex, Heading, Text, Button, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { MentionIcon } from '@/chat/components/mention-icon';
import { locateMentionNames, mentionLabel, type MentionNode } from '@/chat/utils/node-mentions';

export const QUESTION_CHAR_LIMIT = 250;
export const EXPANDED_MAX_HEIGHT_PX = 360;

export interface ExpandableUserQueryProps {
  question: string;
  isMobile?: boolean;
  messageId?: string;
  isStreaming?: boolean;
  onEdit?: () => void;
  /** Nodes the question was scoped to; where the text names one, it shows as a chip. */
  mentions?: readonly MentionNode[];
}

function MentionChip({ node }: { node: MentionNode }) {
  const text = mentionLabel(node);
  // The icon is glued to the first word, so a line never breaks between them.
  const firstBreak = text.search(/\s/);
  const head = firstBreak < 0 ? text : text.slice(0, firstBreak);
  const rest = firstBreak < 0 ? '' : text.slice(firstBreak);
  const chip = (
    <span
      style={{
        background: 'var(--accent-a3)',
        color: 'var(--accent-a11)',
        boxShadow: 'inset 0 0 0 1px var(--accent-a6)',
        borderRadius: 6,
        padding: '0 6px',
        margin: '0 2px',
        boxDecorationBreak: 'clone',
        WebkitBoxDecorationBreak: 'clone',
      }}
    >
      <span style={{ whiteSpace: 'nowrap' }}>
        <span style={{ display: 'inline-flex', verticalAlign: '-0.12em', marginRight: 5, lineHeight: 0 }} aria-hidden>
          <MentionIcon
            nodeType={node.nodeType}
            connector={node.connector}
            extension={node.extension}
            mimeType={node.mimeType}
            name={node.name}
            size={15}
          />
        </span>
        {head}
      </span>
      {rest}
    </span>
  );
  return text === node.name ? chip : <Tooltip content={node.name}>{chip}</Tooltip>;
}

function withMentions(text: string, mentions: readonly MentionNode[]): React.ReactNode {
  const spans = locateMentionNames(text, mentions);
  if (spans.length === 0) return text;
  const out: React.ReactNode[] = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start > cursor) out.push(text.slice(cursor, span.start));
    out.push(
      <MentionChip key={`${span.node.id}-${span.start}`} node={span.node} />
    );
    cursor = span.end;
  }
  if (cursor < text.length) out.push(text.slice(cursor));
  return out;
}

function ActionIconButton({
  ariaLabel,
  icon,
  visible,
  onClick,
  color = 'var(--slate-11)',
}: {
  ariaLabel: string;
  icon: string;
  visible: boolean;
  onClick: () => void;
  color?: string;
}) {
  return (
    <IconButton
      variant="ghost"
      color="gray"
      size="1"
      onClick={onClick}
      aria-label={ariaLabel}
      style={{
        cursor: 'pointer',
        flexShrink: 0,
        opacity: visible ? 1 : 0,
        transition: 'opacity 0.15s ease',
        pointerEvents: visible ? 'auto' : 'none',
      }}
    >
      <MaterialIcon name={icon} size={ICON_SIZES.PRIMARY} color={color} />
    </IconButton>
  );
}

function ToggleButton({
  expanded,
  onToggle,
}: {
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <Button
      color="gray"
      size="2"
      onClick={onToggle}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '2px',
        cursor: 'pointer',
        color: 'var(--slate-11)',
        background: 'none',
        padding: 0,
        fontFamily: 'inherit',
        height: 'auto',
      }}
    >
      {expanded ? 'Show less' : 'Show more'}
      <MaterialIcon
        name={expanded ? 'keyboard_arrow_up' : 'keyboard_arrow_down'}
        size={ICON_SIZES.PRIMARY}
      />
    </Button>
  );
}

function QueryActions({
  question,
  showEdit,
  onEdit,
  visible,
}: {
  question: string;
  showEdit: boolean;
  onEdit?: () => void;
  visible: boolean;
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  const handleCopy = useCallback(() => {
    void navigator.clipboard.writeText(question).then(() => {
      setCopied(true);
    }).catch(() => {});
  }, [question]);

  if (!question.trim() && !showEdit) return null;

  return (
    <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
      {question.trim() ? (
        <ActionIconButton
          ariaLabel={copied ? 'Copied' : 'Copy'}
          icon={copied ? 'check' : 'content_copy'}
          visible={visible}
          onClick={handleCopy}
          color={copied ? 'var(--accent-11)' : 'var(--slate-11)'}
        />
      ) : null}
      {showEdit && onEdit ? (
        <ActionIconButton
          ariaLabel="Edit"
          icon="edit"
          visible={visible}
          onClick={onEdit}
        />
      ) : null}
    </Flex>
  );
}

export function ExpandableUserQuery({
  question,
  isMobile: _isMobile = false,
  messageId,
  isStreaming = false,
  onEdit,
  mentions = [],
}: ExpandableUserQueryProps) {
  const [expanded, setExpanded] = useState(false);
  const [hovered, setHovered] = useState(false);

  useEffect(() => setExpanded(false), [question]);

  const isLong = Boolean(question.trim()) && question.length > QUESTION_CHAR_LIMIT;
  const showEdit = Boolean(!isStreaming && messageId && onEdit);
  const toggle = () => setExpanded((prev) => !prev);

  if (!isLong || !expanded) {
    const displayed =
      isLong && !expanded
        ? question.slice(0, QUESTION_CHAR_LIMIT).trimEnd() + '…'
        : question;

    return (
      <Box
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        <Heading
          size="5"
          weight="medium"
          data-testid="user-query-heading"
          style={{
            color: 'var(--slate-12)',
            lineHeight: 1.3,
            paddingTop: 'var(--space-3)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {withMentions(displayed, mentions)}
          <span
            data-testid="user-query-inline-actions"
            style={{ marginLeft: 'var(--space-2)', verticalAlign: 'middle', display: 'inline-flex' }}
          >
            <QueryActions
              question={question}
              showEdit={showEdit}
              onEdit={onEdit}
              visible={hovered}
            />
          </span>
        </Heading>
        {isLong ? (
          <Box style={{ marginTop: 'var(--space-2)' }}>
            <ToggleButton expanded={false} onToggle={toggle} />
          </Box>
        ) : null}
      </Box>
    );
  }

  return (
    <Box style={{ paddingTop: 'var(--space-3)' }}>
      <Box
        data-testid="user-query-bubble"
        style={{
          backgroundColor: 'var(--slate-3)',
          border: '1px solid var(--slate-a4)',
          borderRadius: 'var(--radius-3)',
          overflow: 'hidden',
        }}
      >
        <Text
          data-testid="user-query-text"
          size="2"
          style={{
            display: 'block',
            color: 'var(--slate-12)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            lineHeight: 1.55,
            padding: 'var(--space-3)',
            maxHeight: `${EXPANDED_MAX_HEIGHT_PX}px`,
            overflowY: 'auto',
          }}
        >
          {withMentions(question, mentions)}
        </Text>
        <Flex
          data-testid="user-query-actions"
          align="center"
          justify="between"
          style={{
            padding: 'var(--space-2) var(--space-3)',
            borderTop: '1px solid var(--slate-a4)',
            backgroundColor: 'var(--slate-2)',
          }}
        >
          <ToggleButton expanded onToggle={toggle} />
          <QueryActions
            question={question}
            showEdit={showEdit}
            onEdit={onEdit}
            visible
          />
        </Flex>
      </Box>
    </Box>
  );
}
