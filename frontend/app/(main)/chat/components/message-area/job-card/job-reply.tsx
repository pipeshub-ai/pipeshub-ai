'use client';

import React, { useEffect, useId, useMemo } from 'react';
import { Box } from '@radix-ui/themes';
import type { RunAnswerCitation } from '../../../types';
import { extractAnswerMarkers } from '../../../utils/parse-download-markers';
import { citedAnswer } from '../../../utils/cited-answer';
import { AnswerContent } from '../answer-content';
import { CitationMessageRowKeyContext, outsideListRowKey } from '../response-tabs/citations/citation-popover-control';
import { useInlineCitationPopoverStore } from '../response-tabs/citations/citation-popover-store';
import type { CitationCallbacks, StreamingCitationData } from '../response-tabs/citations/types';
import { useCitationActions } from '../response-tabs/citations/use-citation-actions';
import { buildCitationMapsFromStreaming } from '../response-tabs/citations/utils';

const NO_CITATIONS: ReadonlyArray<RunAnswerCitation> = [];

/**
 * A job's own words, drawn the way the chat draws an answer (BGUI-11): citation links become the answer's chips and
 * the answer's file directives (`::artifact[…]`) never show as text. The engine's text is model output, so its
 * directives never become cards here (`MarkerSource` 'streamed'): the job's files come from its typed artifacts.
 * Each chip names its source and opens the answer's popover and preview (BGUI-12): `citations` are its answer's, as
 * its timeline carries them, read by the answer's one citation rule (`citedAnswer`, as the chat's answer is).
 * The one place a job's reply is rendered; the report under its card no longer shows it (the chat's answer does).
 */
export function JobReply({
  text,
  citations = NO_CITATIONS,
  testId = 'job-reply',
}: {
  text: string;
  citations?: ReadonlyArray<RunAnswerCitation>;
  testId?: string;
}) {
  const cited = useMemo(
    () => citedAnswer(text, buildCitationMapsFromStreaming([...citations] as StreamingCitationData[])),
    [text, citations],
  );
  const shown = useMemo(() => extractAnswerMarkers(cited.text, 'streamed').text.trim(), [cited.text]);
  const actions = useCitationActions();
  // As the chat's answer does: the preview gets every citation of the record, not only the one clicked.
  const callbacks = useMemo<CitationCallbacks>(
    () => ({
      ...actions,
      onPreview: actions.onPreview ? (citation) => actions.onPreview?.(citation, cited.citationMaps) : undefined,
    }),
    [actions, cited.citationMaps],
  );
  // Its chips' popover is the chat's one; scoped outside the message list, and closed when the reply goes.
  const rowKey = outsideListRowKey(useId());
  useEffect(
    () => () => {
      const { activeKey, close } = useInlineCitationPopoverStore.getState();
      if (activeKey?.startsWith(`${rowKey}::`)) close(activeKey);
    },
    [rowKey],
  );
  if (!shown) return null;
  return (
    <CitationMessageRowKeyContext.Provider value={rowKey}>
      <Box data-testid={testId} style={{ minWidth: 0 }}>
        <AnswerContent content={shown} citationMaps={cited.citationMaps} citationCallbacks={callbacks} />
      </Box>
    </CitationMessageRowKeyContext.Provider>
  );
}
