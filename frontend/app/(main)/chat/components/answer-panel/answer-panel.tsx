'use client';

import React, { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Box, Flex, Tabs } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { ThreadMessageLike } from '@assistant-ui/react';
import { useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { chatExtension } from '@/config';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { CHAT_MIN_WIDTH_PX } from '../../constants';
import { useSplitWidth } from '../../hooks/use-split-width';
import { useChatStore } from '../../store';
import { extractAnswerMarkers } from '../../utils/parse-download-markers';
import { ArtifactsPanel } from '../message-area/artifacts-panel';
import { CHAT_TOP_BAR_HEIGHT } from '../message-area/chat-top-bar';
import { useLatestArtifactVersions } from '../message-area/chat-response-props';
import type { MessagePair } from '../message-area/message-pairs';
import { useArtifactActions } from '../message-area/use-artifact-actions';
import { CitationsTab, SourcesTab, useCitationActions, type CitationCallbacks } from '../message-area/response-tabs/citations';
import { AnswerPicker } from './answer-picker';
import { onSourcesRequest, pickAnswer, usePanelOpen, useReadingAnswer } from './answer-panel-store';
import { PanelEmpty, PanelPending } from './panel-empty';
import { PanelContext } from './context-list';
import { PanelClose } from './panel-toggle';
import { COLUMN_DEFAULT_PX, COLUMN_MAX_PX, COLUMN_MIN_PX, clampColumnWidth, storeColumnWidth, storedColumnWidth } from './column-width';
import { useSlotPairs } from './use-slot-pairs';

type PanelTab = 'sources' | 'citations' | 'context' | 'outputs';


const NO_MESSAGES: ThreadMessageLike[] = [];

/**
 * The column beside a chat ("About this answer"): everything about the answer being read, moved out of the answer.
 * A card inset in its own gutter, 360px wide, so the conversation keeps the rest. Its header holds the answer picker
 * and the close; below it, four tabs: Sources and Citations (what the answer drew on), Context (what its
 * question was narrowed to) and Outputs (the files it made). Callers fill the tabs, so each kind of chat says what it
 * may show; an answer's own Sources opens the column on the Sources tab.
 */
export function AnswerPanelFrame({
  slotId,
  picker,
  sources,
  citations,
  context,
  outputs,
  testId = 'answer-side-panel',
}: {
  slotId: string;
  picker: ReactNode;
  sources: ReactNode;
  citations: ReactNode;
  context: ReactNode;
  outputs: ReactNode;
  testId?: string;
}) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<PanelTab>('sources');
  useEffect(() => onSourcesRequest(slotId, () => setTab('sources')), [slotId]);
  const [width, setWidth] = useSplitWidth(storedColumnWidth, clampColumnWidth);
  return (
    // The gutter: the chat's own canvas, so the card floats on the chat instead of standing beside it as a third pane.
    // Its inset and hairline come on top of the card's width, and however wide it is set, the chat keeps its minimum.
    <Box
      style={{
        position: 'relative',
        display: 'flex',
        width: `calc(${width}px + var(--space-2) * 2 + 2px)`,
        minWidth: `calc(${COLUMN_MIN_PX}px + var(--space-2) * 2 + 2px)`,
        maxWidth: `calc(100% - ${CHAT_MIN_WIDTH_PX}px)`,
        height: '100%',
        background: 'var(--surface-canvas)',
      }}
    >
      <ColumnResizeHandle width={width} setWidth={setWidth} />
      <Flex
        asChild
        direction="column"
        data-testid={testId}
        style={
          {
            flex: 1,
            minWidth: 0,
            margin: 'var(--space-2)',
            borderRadius: 'var(--radius-4)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--color-panel-solid)',
            boxShadow: 'var(--shadow-4)',
            overflow: 'hidden',
            // What a mask inside must match to read as cut out of the card.
            '--panel-surface': 'var(--color-panel-solid)',
          } as React.CSSProperties
        }
      >
        <aside aria-label={t('chat.answerPanel.label')}>
          {/* The top bar's 40px: the card's header holds the answer picker and the close. */}
          <Flex align="center" style={{ height: CHAT_TOP_BAR_HEIGHT, paddingInline: 'var(--space-3)', boxSizing: 'border-box', flexShrink: 0, borderBottom: '1px solid var(--border-subtle)' }}>
            {picker}
            <Box style={{ marginInlineStart: 'auto', flexShrink: 0 }}>
              <PanelClose />
            </Box>
          </Flex>
          <Tabs.Root value={tab} onValueChange={(value) => setTab(value as PanelTab)} style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
            {/* 16px under the header's hairline, inset to the body's gutter, 8px over the body; the tabs pack left (globals.css). */}
            <Tabs.List
              size="2"
              className="answer-panel-tabs"
              style={{ flexShrink: 0, marginTop: 'var(--space-4)', marginBottom: 'var(--space-2)', marginInline: 'var(--space-4)' }}
            >
              <Tabs.Trigger value="sources">
                <span className="answer-panel-tab-label">{t('chat.answerPanel.sources')}</span>
              </Tabs.Trigger>
              <Tabs.Trigger value="citations">
                <span className="answer-panel-tab-label">{t('chat.answerPanel.citations')}</span>
              </Tabs.Trigger>
              <Tabs.Trigger value="context">
                <span className="answer-panel-tab-label">{t('chat.answerPanel.context')}</span>
              </Tabs.Trigger>
              <Tabs.Trigger value="outputs">
                <span className="answer-panel-tab-label">{t('chat.answerPanel.outputs')}</span>
              </Tabs.Trigger>
            </Tabs.List>
            {/* 12px more, so the first card does not read as part of the tab row. */}
            <Box px="4" pt="3" pb="4" style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
              <Tabs.Content value="sources" style={{ height: '100%' }}>
                {sources}
              </Tabs.Content>
              <Tabs.Content value="citations" style={{ height: '100%' }}>
                {citations}
              </Tabs.Content>
              <Tabs.Content value="context" style={{ height: '100%' }}>
                {context}
              </Tabs.Content>
              <Tabs.Content value="outputs" style={{ height: '100%' }}>
                {outputs}
              </Tabs.Content>
            </Box>
          </Tabs.Root>
        </aside>
      </Flex>
    </Box>
  );
}

/**
 * The column's left edge, in the gutter: drag it, or focus it and use the arrow keys (16px a step), Home for the
 * widest and End or a double-click for the default. A 2px accent line shows while it is hovered, focused or dragged.
 */
function ColumnResizeHandle({ width, setWidth }: { width: number; setWidth: (px: number) => void }) {
  const { t } = useTranslation();
  const [lit, setLit] = useState(false);
  const drag = useRef<{ x: number; w: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  // The widest the layout allows here, the chat keeping its minimum beside the sidebar: what is said and set.
  const [room, setRoom] = useState<number>(COLUMN_MAX_PX);
  useEffect(() => {
    const gutter = ref.current?.parentElement;
    const split = gutter?.parentElement;
    if (!gutter || !split || typeof ResizeObserver === 'undefined') return undefined;
    const measure = () => {
      // Not laid out (yet): nothing to go by.
      if (!split.clientWidth) return;
      const card = ref.current?.nextElementSibling;
      const insets = card ? gutter.getBoundingClientRect().width - card.getBoundingClientRect().width : 0;
      setRoom(Math.max(COLUMN_MIN_PX, Math.floor(split.clientWidth - CHAT_MIN_WIDTH_PX - insets)));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(split);
    return () => observer.disconnect();
  }, []);
  const shown = Math.min(width, room);
  const set = (px: number, store: boolean) => {
    const next = Math.min(clampColumnWidth(px, window.innerWidth), Math.max(room, COLUMN_MIN_PX));
    setWidth(next);
    if (store) storeColumnWidth(next);
  };
  const endDrag = () => {
    if (!drag.current) return;
    drag.current = null;
    storeColumnWidth(width);
  };
  return (
    <Box
      role="separator"
      aria-orientation="vertical"
      aria-label={t('chat.answerPanel.resize')}
      ref={ref}
      aria-valuenow={shown}
      aria-valuemin={COLUMN_MIN_PX}
      aria-valuemax={Math.min(COLUMN_MAX_PX, room)}
      tabIndex={0}
      data-testid="answer-panel-resize"
      onPointerDown={(e) => {
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        // From the width shown, which the chat's minimum may hold below the one set.
        const card = e.currentTarget.nextElementSibling;
        drag.current = { x: e.clientX, w: card ? card.getBoundingClientRect().width - 2 : width };
      }}
      // Dragging left widens: the column grows toward the chat.
      onPointerMove={(e) => drag.current && set(drag.current.w + drag.current.x - e.clientX, false)}
      onPointerUp={endDrag}
      onLostPointerCapture={endDrag}
      onDoubleClick={() => set(COLUMN_DEFAULT_PX, true)}
      onKeyDown={(e) => {
        const to = { ArrowLeft: shown + 16, ArrowRight: shown - 16, Home: COLUMN_MAX_PX, End: COLUMN_DEFAULT_PX }[e.key];
        if (to === undefined) return;
        e.preventDefault();
        set(to, true);
      }}
      onPointerEnter={() => setLit(true)}
      onPointerLeave={() => setLit(false)}
      onFocus={() => setLit(true)}
      onBlur={() => setLit(false)}
      style={{
        position: 'absolute',
        top: 0,
        bottom: 0,
        // 6px, centred on the gutter's edge, with a 2px line in its middle.
        left: 'calc(var(--space-1) * -0.75)',
        width: 'calc(var(--space-1) * 1.5)',
        padding: '0 calc(var(--space-1) / 2)',
        boxSizing: 'border-box',
        backgroundClip: 'content-box',
        backgroundColor: lit || drag.current ? 'var(--accent-8)' : 'transparent',
        transition: 'background-color var(--motion-micro) var(--motion-ease)',
        cursor: 'col-resize',
        touchAction: 'none',
        outline: 'none',
        zIndex: 1,
      }}
    />
  );
}

/** The files an answer made, or the empty state that says how to get some. */
export function PanelOutputs({ pair, latestArtifactVersions }: { pair: MessagePair; latestArtifactVersions: ReadonlyMap<string, number> }) {
  const { t } = useTranslation();
  // A streaming answer's text is model output: its markers never become cards.
  const artifacts = useMemo(
    () => (pair.isStreaming ? [] : extractAnswerMarkers(pair.answer, 'persisted').artifacts),
    [pair.answer, pair.isStreaming],
  );
  const { onPreview, onViewSource } = useArtifactActions(latestArtifactVersions);
  if (artifacts.length === 0) {
    return <PanelEmpty icon="draft" title={t('chat.answerPanel.noOutputsTitle')} body={t('chat.answerPanel.noOutputsBody')} />;
  }
  return (
    <Box>
      <ArtifactsPanel artifacts={artifacts} latestArtifactVersions={latestArtifactVersions} onPreview={onPreview} onViewSource={onViewSource} />
    </Box>
  );
}

/** A citation previews with every citation of its answer, as the answer's own tabs give it. */
export function previewWithAnswer(callbacks: CitationCallbacks, pair: MessagePair): CitationCallbacks {
  const { onPreview } = callbacks;
  return { ...callbacks, onPreview: onPreview ? (citation) => onPreview(citation, pair.citationMaps) : undefined };
}

/**
 * The column beside a one-person or agent chat, from 1024px while it is open. It describes the answer being read
 * (the list's scroll spy) or the one picked, and starts shut on every chat.
 */
export function AnswerSidePanel({ slotId }: { slotId: string | null }) {
  const open = usePanelOpen();
  const wide = !useIsBelowDesktop();
  return open && wide && slotId ? <OpenAnswerPanel key={slotId} slotId={slotId} /> : null;
}

function OpenAnswerPanel({ slotId }: { slotId: string }) {
  const { t } = useTranslation();
  const pairs = useSlotPairs(slotId);
  const reading = useReadingAnswer(slotId);
  const citationActions = useCitationActions();
  const messages = useChatStore((s) => s.slots[slotId]?.messages ?? NO_MESSAGES);
  const latestArtifactVersions = useLatestArtifactVersions(messages, null);
  // The plain text of a question can depend on flags that load after the chat; read again once they do.
  const flags = useFeatureFlagsStore((s) => s.flags);
  const answers = useMemo(
    () => pairs.map((pair) => ({ key: pair.key, question: chatExtension.question.toPlainText(pair.question) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `flags` is read inside toPlainText
    [pairs, flags],
  );
  if (pairs.length === 0) return null;

  // Before the reader has scrolled, or once the answer read has gone, the newest.
  const pair = pairs.find((p) => p.key === reading) ?? pairs[pairs.length - 1];
  const pending = pair.isStreaming || pair.unanswered === true;
  const callbacks = previewWithAnswer(citationActions, pair);
  const pendingHint = <PanelPending>{t('chat.answerPanel.pending')}</PanelPending>;
  return (
    <AnswerPanelFrame
      slotId={slotId}
      picker={<AnswerPicker answers={answers} currentKey={pair.key} onPick={(key) => pickAnswer(slotId, key)} />}
      sources={pending ? pendingHint : <SourcesTab flush citationMaps={pair.citationMaps} callbacks={callbacks} />}
      citations={pending ? pendingHint : <CitationsTab flush citationMaps={pair.citationMaps} callbacks={callbacks} />}
      context={<PanelContext pair={pair} slotId={slotId} />}
      outputs={pending ? pendingHint : <PanelOutputs pair={pair} latestArtifactVersions={latestArtifactVersions} />}
    />
  );
}
