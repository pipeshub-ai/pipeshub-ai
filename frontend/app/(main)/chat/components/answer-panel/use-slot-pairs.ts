'use client';

import { useEffect, useMemo, useRef } from 'react';
import type { ThreadMessageLike } from '@assistant-ui/react';
import { useChatStore } from '../../store';
import type { ChatCollectionAttachment } from '../../types';
import { buildMessagePairs, keepUnchangedPairs, type MessagePair } from '../message-area/message-pairs';
import { emptyCitationMaps } from '../message-area/response-tabs/citations';

const NO_MESSAGES: ThreadMessageLike[] = [];
const NO_COLLECTIONS: ChatCollectionAttachment[] = [];
const EMPTY_CITATION_MAPS = emptyCitationMaps();

/** A chat slot's turns (question and answer), as the column beside the chat reads them. */
export function useSlotPairs(slotId: string | null | undefined): MessagePair[] {
  const messages = useChatStore((s) => (slotId ? s.slots[slotId]?.messages : undefined) ?? NO_MESSAGES);
  const isStreaming = useChatStore((s) => (slotId ? s.slots[slotId]?.isStreaming : undefined) ?? false);
  const streamingQuestion = useChatStore((s) => (slotId ? s.slots[slotId]?.streamingQuestion : undefined) ?? '');
  const pendingCollections = useChatStore((s) => (slotId ? s.slots[slotId]?.pendingCollections : undefined) ?? NO_COLLECTIONS);
  const regenerateMessageId = useChatStore((s) => (slotId ? s.slots[slotId]?.regenerateMessageId : undefined) ?? null);
  // A row equal to the last build keeps its object, so memoised rows skip re-rendering.
  const previous = useRef<ReadonlyMap<string, MessagePair>>(new Map());
  const pairs = useMemo(
    () =>
      keepUnchangedPairs(
        // eslint-disable-next-line react-hooks/refs -- intentional: the last build only decides object identity, not what is drawn
        previous.current,
        buildMessagePairs(messages, {
          isStreaming,
          streamingQuestion,
          pendingCollections,
          regenerateMessageId,
          emptyCitationMaps: EMPTY_CITATION_MAPS,
        }),
      ),
    [messages, isStreaming, streamingQuestion, pendingCollections, regenerateMessageId],
  );
  useEffect(() => {
    previous.current = new Map(pairs.map((pair) => [pair.key, pair]));
  }, [pairs]);
  return pairs;
}
