import { config } from "dotenv";
import { applyStoreUmbrella } from "../../../libs/config/storeUmbrella";
config({ quiet: true }); // Load environment variables first
applyStoreUmbrella(); // PIPESHUB_STORE=postgres fills in the per-store settings

import { json, urlencoded, Request, Response } from "express";
import { connect, dropLegacyThreadBotIndex } from "./utils/db";
import { initDocStore } from '../../../libs/repositories/doc-store';
import { usesMongo } from '../../../libs/repositories/store-select';
import { getFromDatabase, saveToDatabase } from "./utils/conversation";
import axios from "axios";
import { marked } from "marked";
// Disable marked's email mangling to prevent HTML entity encoding of email addresses.
marked.setOptions({ mangle: false } as any);
import app from "./slackApp";
import receiver from "./receiver";
// Side-effect import: registers edition-specific Redis providers for this process.
import "../../../redisProviders";
import {
  RedisConnectionProviderFactory,
  getPreparedRedisProvider,
} from "../../../libs/services/redis/connectionProviderFactory";
import { ConfigService } from "../../../modules/tokens_manager/services/cm.service";
import { slackJwtGenerator } from "../../../libs/utils/createJwt";
import { createSlackAGUIEventHandler } from "./utils/agui-stream";
import {
  cleanNarration,
  stripSlackActivityTimeline,
} from "./utils/activity-ui";
import {
  SlackPlanStream,
  SlackTaskPlan,
  type SlackTaskChunk,
} from "./utils/plan-stream";
import {
  formatAskUserQuestionMrkdwn,
  type AskUserQuestionEvent,
} from "./utils/ask-user-format";
import { toolActivityLabel } from "./utils/tool-display";
import {
  parseArtifactMarkers,
  stripArtifactMarkers,
} from "./utils/parse-artifact-markers";
import {
  stripFragmentDirective,
  CitationMarkers,
  splitOpenLinkTail,
  withoutPlaceholderRefs,
  buildNumberedSlackSources,
  stripNumberedCitationLinkTargets,
  citationRows,
  numberedSourcesBlocks,
  type SlackSourceRow,
  type SlackCitationLike,
} from "./utils/citations";

import {
  type SlackBotConfig,
  getCurrentMatchedSlackBot,
} from "./botRegistry";

import {
  type CitationData,
  type ConversationData,
  type SlackMessagePayload,
  type TypedSlackClient,
  type AttachmentRef,
  type SlackAttachmentUploadResult,
  type SlackFile,
  FAILED_RESPONSE_GENERATION_MESSAGE,
  STREAM_UPDATE_THROTTLE_MS,
  STREAM_UPDATE_MAX_CHARS,
  NARRATION_HOLD_CHARS,
  looksLikeAnswer,
  NO_UNFURL_OPTIONS,
  STREAM_FAILURE_MESSAGE,
  BACKEND_STREAM_TIMEOUT_MS,
  truncateForSlack,
  truncateForSlackStreamMarkdown,
  markdownTableStart,
  trailingTableRowsStart,
  buildFinalSlackChunks,
  splitBeforeFirstTable,
  slackBlocksFallbackText,
  splitSlackBlocksByLimit,
  classifySlackFiles,
  resolveSkippedAttachmentsAfterUpload,
  uploadSlackAttachments,
  postSkippedAttachmentsNotice,
  buildSkippedAttachmentsNotice,
  handleIncomingAttachments,
  messageHasQuestionText,
  resolveMentionsInText,
  resolveSlackErrorMessage,
  resolveSlackErrorMessageAsync,
  parseSSEEvents,
  buildChatStreamUrl,
  resolveSlackBotForEvent,
  resolveThreadId,
  sendUserFacingSlackErrorMessage,
  slackCallerDisplayName,
  isIgnoredSlackMessage,
  isThreadFollowUpMessage,
  fetchPriorThread,
  collectThreadFilesSinceLastBotReply,
  resolveOwnBotIdentity,
  type PriorThreadMessages,
  MAX_ATTACHMENTS_PER_QUESTION,
  resolveThreadUserLabels,
  inferThreadMessageSpeaker,
  threadHasOtherParticipants,
  formatUntrustedThreadContextQuery,
  removeContinuousDuplicateMarkdownLinks,
  addSpaceBetweenMarkdownLinks,
  resolveSlackArtifactLink,
  getFrontendBaseUrl,
  buildFrontendRecordUrl,
} from "./helpers";

interface TypedSlackContext {
  botUserId?: string;
  teamId?: string;
  matchedBotId?: string;
  matchedBotUserId?: string;
  matchedBotTeamId?: string;
  matchedBotAgentId?: string | null;
}


async function buildThreadContextualQuery(
  query: string,
  priorMessages: SlackMessagePayload[],
  userLabelsById: Map<string, string>,
  typedClient: TypedSlackClient,
  currentUserId?: string,
  botUserId?: string,
): Promise<string> {
  const contextLines = await Promise.all(
    priorMessages.map(async (message) => {
      const withoutActivity = stripSlackActivityTimeline(message.text || "");
      const normalizedText = await resolveMentionsInText(
        withoutActivity,
        typedClient,
      );
      if (!normalizedText) {
        return null;
      }
      const speaker = inferThreadMessageSpeaker(
        message,
        userLabelsById,
        botUserId,
      );
      return `${speaker}: ${normalizedText}`;
    })
  ).then(lines => lines.filter((line): line is string => Boolean(line)));

  if (contextLines.length === 0) {
    return query;
  }

  // Alone with the bot, the thread is the user's own words: keep the plain format.
  if (!threadHasOtherParticipants(priorMessages, currentUserId, botUserId)) {
    return `Slack thread context:\n${contextLines.join("\n")}\n\nCurrent slack message/query: ${query}`;
  }
  return formatUntrustedThreadContextQuery(query, contextLines);
}

async function fetchThreadContext(
  typedClient: TypedSlackClient,
  typedMessage: SlackMessagePayload,
): Promise<PriorThreadMessages> {
  if (!isThreadFollowUpMessage(typedMessage)) {
    return { messages: [], complete: true };
  }
  try {
    return await fetchPriorThread(typedClient, typedMessage);
  } catch (error) {
    console.error('Failed to fetch Slack thread context:', error);
    return { messages: [], complete: false };
  }
}

/** Thread files for this question; none unless the whole thread was read. Never throws. */
async function collectThreadFiles(
  typedClient: TypedSlackClient,
  typedMessage: SlackMessagePayload,
  thread: PriorThreadMessages,
  botCacheKey: string,
  knownBotUserId: string | undefined,
): Promise<SlackFile[]> {
  if (!thread.complete) {
    return [];
  }
  const prior = thread.messages.filter((m) => m.ts !== typedMessage.ts);
  const hasFiles = prior.some(
    (m) => Array.isArray(m.files) && m.files.length > 0,
  );
  if (!hasFiles) {
    return [];
  }
  const own = await resolveOwnBotIdentity(
    typedClient,
    botCacheKey,
    knownBotUserId,
  );
  return collectThreadFilesSinceLastBotReply(prior, own.userId, own.botId);
}

async function buildQueryWithThreadContext(
  typedClient: TypedSlackClient,
  priorMessages: SlackMessagePayload[],
  query: string,
  currentUserId?: string,
  botUserId?: string,
): Promise<string> {
  if (priorMessages.length === 0) {
    return query;
  }

  try {
    const userLabelsById = await resolveThreadUserLabels(typedClient, priorMessages);
    return await buildThreadContextualQuery(
      query,
      priorMessages,
      userLabelsById,
      typedClient,
      currentUserId,
      botUserId,
    );
  } catch (error) {
    console.error("Failed to fetch Slack thread context:", error);
    return query;
  }
}

/** Upload thread files for this question. Never fails the reply: on error it logs and sends none. */
async function uploadThreadFiles(
  threadFiles: SlackFile[],
  slots: number,
  botToken: string | undefined,
  accessToken: string,
  agentId: string | null | undefined,
): Promise<AttachmentRef[]> {
  const files = threadFiles.slice(0, Math.max(slots, 0));
  if (files.length === 0 || botToken === undefined || botToken === '') {
    return [];
  }
  try {
    const upload = await uploadSlackAttachments(
      files,
      botToken,
      accessToken,
      agentId,
    );
    return upload.attachments;
  } catch (error) {
    console.error(
      'Error uploading Slack thread files:',
      error instanceof Error ? error.message : String(error),
    );
    return [];
  }
}

function getCitationWebUrl(webUrl?: string): string {
  if (!webUrl) {
    return "";
  }
  if (/^https?:\/\//i.test(webUrl)) {
    return webUrl;
  }
  return `${getFrontendBaseUrl()}${webUrl}`;
}

function buildCitationSources(citations?: CitationData[]): any[]  {

  // Deduplicate by recordId, keeping the first occurrence per unique record
  const seenRecordIds = new Set<string>();
  const uniqueRecords: Array<{ name: string; url: string }> = [];

  for (const citation of citations || []) {
    const recordId = citation.citationData.metadata.recordId;
    if (!recordId) continue;

    let webUrl = getCitationWebUrl(citation.citationData.metadata.webUrl);
    if (!webUrl) {
      webUrl = buildFrontendRecordUrl(recordId);
    }

    if (seenRecordIds.has(recordId)) continue;
    seenRecordIds.add(recordId);

    const recordName = citation.citationData.metadata.recordName || "Source";
    // Slack does not implement text fragments, and sources are deduped per record
    const recordUrl = stripFragmentDirective(webUrl);
    uniqueRecords.push({ name: recordName, url: recordUrl });
  }

  let blocks: any[] = [];
  let elements: any[] = [];

  for (const record of uniqueRecords) {
    elements.push({
      "type": "link",
      "url": record.url,
      "text": ` ${record.name}`,
    });
    elements.push({
      "type": "text",
      "text": `\n`,
    });

    if (elements.length === 20) {
      blocks.push({
        "type": "rich_text",
        "elements": [
          {
            "type": "rich_text_section",
            "elements": [
              ...elements,
            ]
          }
        ]
      });
      elements = [];
    }
  }

  if (elements.length > 0) {
    blocks.push({
      "type": "rich_text",
      "elements": [
        {
          "type": "rich_text_section",
          "elements": [
            ...elements,
          ]
        }
      ]
    });
  }

  if (blocks.length > 0) {
    blocks = [ {
      type: "section",
      text: {
        type: "mrkdwn",
        text: "*Sources:*",
      },
    }, ...blocks];
  }
  return blocks;
}

// Middleware setup
receiver.router.use(json());
receiver.router.use(urlencoded());

// Routes
receiver.router.get("/", (_req: Request, res: Response) => {
  res.send("Running");
});


receiver.router.post("slack/command", (req: Request, res: Response) => {
  if (req.body.type === "url_verification") {
    res.send({ challenge: req.body.challenge });
  } else {
    res.status(200).send();
  }
});

export { removeContinuousDuplicateMarkdownLinks };

async function processSlackMessage(
  typedMessage: SlackMessagePayload,
  typedClient: TypedSlackClient,
  typedContext: TypedSlackContext,
  query: string,
  resolvedSlackBot: SlackBotConfig | null,
  threadFiles: SlackFile[] = [],
): Promise<void> {

  if (!typedMessage.user || !typedMessage.channel) {
    return;
  }

  const threadId = resolveThreadId(typedMessage);

  const lookupResult = await typedClient.users.info({
    user: typedMessage.user,
  });



  if (!lookupResult.user?.profile?.email) {
    console.error("Failed to get user email");
    await sendUserFacingSlackErrorMessage(
      typedClient,
      typedMessage,
      "I couldn't verify your Slack profile details right now. Please try again in a moment.",
    );
    return;
  }

  const email = lookupResult.user.profile.email;
  const callerDisplayName = slackCallerDisplayName(lookupResult.user);
  // Slack returns the user's IANA timezone (e.g. "America/Los_Angeles") on
  // users.info. Forward it to the AI backend so build_llm_time_context can
  // localize the LLM's time-relative answers to the user's actual zone
  // instead of falling back to the server clock.
  const userTimezone = lookupResult.user.tz || undefined;
  const configService = ConfigService.getInstance();

  const currentAgentId = resolvedSlackBot?.agentId || null;
  const currentBotId = resolvedSlackBot?.botId;
  if (!currentBotId) {
    throw new Error("Unable to resolve Slack bot id for conversation persistence.");
  }

  const accessToken = slackJwtGenerator(
    email,
    await configService.getScopedJwtSecret(),
    undefined,
    { configId: currentBotId },
  );

  const conversation = await getFromDatabase(
    threadId,
    currentBotId,
    email,
  );
  const answerPlan = new SlackTaskPlan();
  // Numbers citation placeholders as [n] while the answer streams, to match the
  // Sources list: one counter for the reply, so a number never means two refs.
  const citationMarkers = new CitationMarkers();
  // Null when Slack can't stream here: the answer is then posted under a status message.
  let answerStream: SlackPlanStream | null = null;
  let waitingMessageTs: string | null = null;

  const sendOrUpdateNonStreamMessage = async (
    text: string,
    blocks?: any[],
  ): Promise<void> => {

    const truncatedText = truncateForSlack(text);
    if (!text && (!blocks || blocks.length === 0)) {
      return;
    }
    if (waitingMessageTs) {
      try {
        await typedClient.chat.update({
          channel: typedMessage.channel!,
          ts: waitingMessageTs,
          text: truncatedText,
          ...(blocks && blocks.length > 0 ? { blocks } : {}),
          ...NO_UNFURL_OPTIONS,
        });
        return;
      } catch (error) {
        console.error("Error updating Slack waiting message:", error);
        if (blocks && blocks.length > 0) {
          throw error;
        }
        else {
          try {
            await typedClient.chat.update({
              channel: typedMessage.channel!,
              ts: waitingMessageTs,
              text: truncatedText,
              ...NO_UNFURL_OPTIONS,
            });
            return;
          } catch (fallbackError) {
            console.error(
              "Error updating Slack waiting message with text fallback:",
              fallbackError,
            );
            throw fallbackError;
          }
        }
      }
    }

    try {
      await typedClient.chat.postMessage({
        channel: typedMessage.channel!,
        thread_ts: threadId,
        text: truncatedText,
        ...(blocks && blocks.length > 0 ? { blocks } : {}),
        ...NO_UNFURL_OPTIONS,
      });
    } catch (error) {
      if (blocks && blocks.length > 0) {
        throw error;
      }
      console.error("Error posting Slack non-stream blocks message:", error);
      await typedClient.chat.postMessage({
        channel: typedMessage.channel!,
        thread_ts: threadId,
        text: truncatedText,
        ...NO_UNFURL_OPTIONS,
      });
    }
  };

  const postThreadChunkMessage = async (chunk: any[]): Promise<void> => {
    try {
      await typedClient.chat.postMessage({
        channel: typedMessage.channel!,
        thread_ts: threadId,
        text: slackBlocksFallbackText(chunk),
        blocks: chunk,
        ...NO_UNFURL_OPTIONS,
      });
    } catch (error) {
      console.error("Error posting Slack chunk as blocks, retrying with text:", error);
      await typedClient.chat.postMessage({
        channel: typedMessage.channel!,
        thread_ts: threadId,
        text: FAILED_RESPONSE_GENERATION_MESSAGE,
        ...NO_UNFURL_OPTIONS,
      });
    }
  };

  const stopSlackStream = async (markdownText?: string): Promise<boolean> =>
    answerStream
      ? answerStream.stop(
          markdownText
            ? { markdown: truncateForSlackStreamMarkdown(markdownText) }
            : {},
        )
      : true;

  try {
    const streamRecipientPayload: Record<string, unknown> = {};
    streamRecipientPayload.recipient_user_id = typedMessage.user;
    if (typedContext.teamId) {
      streamRecipientPayload.recipient_team_id = typedContext.teamId;
    }

    // Opens on the first step or answer text, so a run with neither shows no plan.
    answerStream = new SlackPlanStream(
      typedClient,
      typedMessage.channel!,
      threadId,
      streamRecipientPayload,
      answerPlan,
    );

    // Handle file attachments for agents
    let attachmentRefs: AttachmentRef[] = [];
    if (typedMessage.files && typedMessage.files.length > 0) {
      const classification = classifySlackFiles(typedMessage.files);
      const supportedFiles = classification.supported;
      if (supportedFiles.length > 0) {
        try {
          let upload: SlackAttachmentUploadResult = { attachments: [], unreadable: [], oversized: [] };
          const botToken = resolvedSlackBot?.botToken;
          if (botToken) {
            upload = await uploadSlackAttachments(supportedFiles, botToken, accessToken, currentAgentId);
            attachmentRefs = upload.attachments;
            console.log(`Uploaded ${attachmentRefs.length} attachment(s) for chat`);
          }
          const { skipped, followUp, hasSkipped } = resolveSkippedAttachmentsAfterUpload(
            classification,
            upload,
            messageHasQuestionText(typedMessage.text, typedContext.botUserId),
          );
          if (hasSkipped && followUp === "none") {
            // Attachment-only and nothing usable is left: the notice is the reply.
            await answerStream?.discard();
            answerStream = null;
            await sendOrUpdateNonStreamMessage(buildSkippedAttachmentsNotice(skipped, followUp));
            return;
          }
          await postSkippedAttachmentsNotice(typedClient, typedMessage, skipped, followUp);
        } catch (uploadError) {
          const errData = (uploadError as any).response?.data;
          const errMsg = errData
            ? JSON.stringify(errData)
            : (uploadError as any).message ?? String(uploadError);
          console.error("Error uploading attachments:", errMsg);
          await answerStream?.discard();
          answerStream = null;
          await sendUserFacingSlackErrorMessage(typedClient, typedMessage, uploadError);
          return;
        }
      }
    }

    if (threadFiles.length > 0) {
      const threadRefs = await uploadThreadFiles(
        threadFiles,
        MAX_ATTACHMENTS_PER_QUESTION - attachmentRefs.length,
        resolvedSlackBot.botToken,
        accessToken,
        currentAgentId,
      );
      attachmentRefs = attachmentRefs.concat(threadRefs);
    }

    const url = buildChatStreamUrl(conversation, currentAgentId);
    const response = await axios.post(
      url,
      {
        query,
        chatMode: currentAgentId? "quick" : "agent",
        currentTime: new Date().toISOString(),
        ...(userTimezone ? { timezone: userTimezone } : {}),
        ...(callerDisplayName ? { callerDisplayName } : {}),
        callerEmail: email,
        ...(attachmentRefs.length > 0 ? { attachments: attachmentRefs } : {}),
      },
      {
        headers: {
          Authorization: `Bearer ${accessToken}`,
          "Content-Type": "application/json",
          Accept: "text/event-stream",
          // Gates Python has_ui_client so CUSTOM ask_user_question SSE is emitted.
          "client-name": "slack",
        },
        responseType: "stream",
        timeout: BACKEND_STREAM_TIMEOUT_MS,
      },
    );

    const responseStream = response.data as NodeJS.ReadableStream;
    let sseBuffer = "";
    let pendingAppendText = "";
    let streamErrorMessage: string | null = null;
    let completionConversation: ConversationData["conversation"] | null = null;
    // Plan updates and answer text share one queue: they go to the same stream, in order.
    let queuedStreamAppend: Promise<void> = Promise.resolve();
    let tableStreamingDisabled = false;
    let streamTableProbeText = "";
    let streamedAnswerLength = 0;
    let ignoreAnswerChunks = false;
    let askUserShown = false;
    let conversationPersisted = Boolean(conversation);
    let conversationPersistPromise: Promise<void> = Promise.resolve();
    // Raw answer text handed to the stream, and the latest full answer: a table
    // stops streaming, and the part after it is posted as blocks at the end.
    let pushedAnswerLength = 0;
    let lastAnswerText = "";
    let answerTextStreamed = false;
    // The end of a flushed piece that may be half a link, sent with the next one.
    let heldAnswerText = "";
    // As it was when the current model call's text began, to restore on a discard.
    let streamedBeforeSegment = false;
    let answerFlushTimer: ReturnType<typeof setTimeout> | null = null;
    const postedArtifactKeys = new Set<string>();

    const updateActivityMessage = async (
      text: string,
      options?: { allowAfterAskUser?: boolean },
    ): Promise<void> => {
      if (!waitingMessageTs || !text) {
        return;
      }
      // Drop stale activity flushes once ask-user questions own this message.
      if (askUserShown && !options?.allowAfterAskUser) {
        return;
      }
      try {
        await typedClient.chat.update({
          channel: typedMessage.channel!,
          ts: waitingMessageTs,
          text: truncateForSlack(text),
          ...NO_UNFURL_OPTIONS,
        });
      } catch (error) {
        console.error("Error updating Slack activity message:", error);
      }
    };

    // Slack refused to stream here: say "Thinking" in a plain status message instead.
    const fallBackToStatusMessage = async (): Promise<void> => {
      if (!answerStream?.unavailable) {
        return;
      }
      answerStream = null;
      if (waitingMessageTs) {
        return;
      }
      try {
        const waitingMessage = await typedClient.chat.postMessage({
          channel: typedMessage.channel!,
          thread_ts: threadId,
          text: "_Thinking..._",
          ...NO_UNFURL_OPTIONS,
        });
        waitingMessageTs = waitingMessage.ts || null;
      } catch (error) {
        console.error("Error posting Slack waiting message:", error);
      }
    };

    let pendingStreamText = "";
    // The current model call's citations as the backend refreshes them: they give [n] its link.
    let liveCitationRows: ReadonlyMap<number, SlackSourceRow> = new Map();
    let textSendQueued = false;
    const pendingPlanRows = new Map<string, SlackTaskChunk>();
    let planSendQueued = false;
    const pushPlan = (
      chunks: (SlackTaskChunk | SlackTaskChunk[] | null)[],
    ): void => {
      // Rows sealed since the last send carry their details and output, once.
      const ready = [...answerPlan.takeSealed(), ...chunks.flat()].filter(
        (chunk): chunk is SlackTaskChunk => chunk !== null,
      );
      if (!answerStream || askUserShown || ready.length === 0) {
        return;
      }
      // Rapid updates coalesce: one queued send carries each changed row's latest state.
      for (const chunk of ready) {
        pendingPlanRows.delete(chunk.id);
        pendingPlanRows.set(chunk.id, chunk);
      }
      if (planSendQueued) {
        return;
      }
      planSendQueued = true;
      queuedStreamAppend = queuedStreamAppend
        .then(async () => {
          planSendQueued = false;
          const rows = [...pendingPlanRows.values()];
          pendingPlanRows.clear();
          await answerStream?.tasks(rows);
          await fallBackToStatusMessage();
        })
        .catch((error: unknown) => {
          console.error("Error updating Slack plan:", error);
        });
    };

    // Text the agent wrote before a step: its own rows in the plan, once, in full.
    const pushNarration = (text: string): void => {
      const narration = withoutPlaceholderRefs(
        cleanNarration(parseArtifactMarkers(text).text),
      );
      answerPlan.endGroup();
      if (!answerStream || askUserShown || narration === "") {
        return;
      }
      // Reasoning came before this text, so its row goes first.
      pushPlan([answerPlan.flushReasoning(), answerPlan.narration(narration)]);
    };

    const deleteStreamMessages = async (): Promise<void> => {
      await answerStream?.discard();
    };

    const persistConversationId = async (conversationId: string): Promise<void> => {
      if (conversationPersisted || !conversationId) {
        return;
      }
      try {
        await saveToDatabase({
          threadId: threadId,
          conversationId,
          botId: currentBotId,
          email: email,
        });
        conversationPersisted = true;
      } catch (error) {
        console.error("Error persisting Slack conversation mapping:", error);
      }
    };

    const clearAnswerFlushTimer = (): void => {
      if (answerFlushTimer) {
        clearTimeout(answerFlushTimer);
        answerFlushTimer = null;
      }
    };

    const enqueueAnswerDelta = (nextChunk: string): void => {
      if (ignoreAnswerChunks || nextChunk.length === 0) {
        return;
      }
      if (tableStreamingDisabled) {
        return;
      }

      streamTableProbeText += nextChunk;
      const tableAt = markdownTableStart(streamTableProbeText);
      if (tableAt >= 0) {
        // The text before the table shows as text, even the part in this delta; the table and the rest go below it as blocks.
        const unsent = heldAnswerText + pendingAppendText + nextChunk;
        const unsentAt = streamTableProbeText.length - unsent.length;
        heldAnswerText = "";
        pendingAppendText = unsent.slice(0, Math.max(0, tableAt - unsentAt));
        flushPendingAppend(true);
        tableStreamingDisabled = true;
        return;
      }

      pendingAppendText += nextChunk;
      // Large buffer → flush now. Otherwise arm a short idle timer so a pause
      // in token arrival still pushes what's buffered (avoids multi-second stalls).
      // Until a model call's text is clearly the answer it waits, so text
      // before a tool call can still become a row of the plan. Its first
      // paragraph break, heading, list, table or fence releases it early.
      const held = pushedAnswerLength === 0;
      const flushAt = held ? NARRATION_HOLD_CHARS : STREAM_UPDATE_MAX_CHARS;
      if (
        pendingAppendText.length >= flushAt ||
        (held && looksLikeAnswer(heldAnswerText + pendingAppendText))
      ) {
        clearAnswerFlushTimer();
        flushPendingAppend();
        return;
      }
      // Text before a tool call becomes a row of the plan, so each model
      // call's short opening waits until it is clearly answer text.
      if (!answerFlushTimer && pushedAnswerLength > 0) {
        answerFlushTimer = setTimeout(() => {
          answerFlushTimer = null;
          flushPendingAppend();
        }, STREAM_UPDATE_THROTTLE_MS);
      }
    };

    const handleAnswerAccumulated = (accumulated: string): void => {
      if (ignoreAnswerChunks) {
        return;
      }
      if (accumulated.length < streamedAnswerLength) {
        // Preamble cleared — reset stream before applying the new buffer.
        resetAnswerStream();
      }
      lastAnswerText = accumulated;
      if (accumulated.length <= streamedAnswerLength) {
        streamedAnswerLength = accumulated.length;
        return;
      }
      const nextChunk = accumulated.slice(streamedAnswerLength);
      streamedAnswerLength = accumulated.length;
      enqueueAnswerDelta(nextChunk);
    };

    const handleAskUserQuestion = (payload: AskUserQuestionEvent): void => {
      // TOOL_CALL_ARGS + CUSTOM + TOOL_CALL_RESULT may all try to surface this.
      if (askUserShown) {
        return;
      }
      const questionText = formatAskUserQuestionMrkdwn(payload);
      if (!questionText) {
        console.error(
          "Slack ask-user payload had no questions:",
          JSON.stringify(payload).slice(0, 500),
        );
        return;
      }

      ignoreAnswerChunks = true;
      askUserShown = true;
      clearAnswerFlushTimer();
      pendingAppendText = "";
      streamedAnswerLength = 0;

      // The questions replace the plan or status message, so "Thinking" is not
      // left sitting above the clarification.
      queuedStreamAppend = queuedStreamAppend
        .then(async () => {
          await deleteStreamMessages();
          if (waitingMessageTs) {
            await updateActivityMessage(questionText, { allowAfterAskUser: true });
          } else {
            await typedClient.chat.postMessage({
              channel: typedMessage.channel!,
              thread_ts: threadId,
              text: truncateForSlack(questionText),
              ...NO_UNFURL_OPTIONS,
            });
          }
        })
        .catch((error) => {
          console.error("Error posting Slack ask-user question:", error);
        });
    };

    const handleArtifact = (artifact: {
      fileName?: string;
      downloadUrl?: string;
      recordId?: string;
      visibility?: string;
    }): void => {
      if (artifact.visibility === "STAGING") {
        return;
      }
      const fileName =
        typeof artifact.fileName === "string" && artifact.fileName.trim()
          ? artifact.fileName.trim()
          : "artifact";
      const recordId =
        typeof artifact.recordId === "string" ? artifact.recordId.trim() : "";
      const link = resolveSlackArtifactLink({
        recordId,
        downloadUrl:
          typeof artifact.downloadUrl === "string"
            ? artifact.downloadUrl
            : undefined,
      });
      if (!link) {
        return;
      }
      const dedupeKey = recordId || link;
      if (postedArtifactKeys.has(dedupeKey) || postedArtifactKeys.has(link)) {
        return;
      }
      postedArtifactKeys.add(dedupeKey);
      postedArtifactKeys.add(link);
      const text = `*Artifact:* <${link}|${fileName}>`;
      queuedStreamAppend = queuedStreamAppend
        .then(async () => {
          await typedClient.chat.postMessage({
            channel: typedMessage.channel!,
            thread_ts: threadId,
            text: truncateForSlack(text),
            ...NO_UNFURL_OPTIONS,
          });
        })
        .catch((error) => {
          console.error("Error posting Slack artifact link:", error);
        });
    };

    // Text already shown stays (no delete and repost); the answer goes on after it.
    function resetAnswerStream(): void {
      clearAnswerFlushTimer();
      pendingAppendText = '';
      heldAnswerText = '';
      streamedAnswerLength = 0;
      pushedAnswerLength = 0;
      lastAnswerText = '';
      startAnswerSegment();
    }

    function startAnswerSegment(): void {
      citationMarkers.checkpoint();
      liveCitationRows = new Map();
      streamedBeforeSegment = answerTextStreamed;
      queuedStreamAppend = queuedStreamAppend
        .then(() => {
          answerStream?.markSegment();
        })
        .catch((error: unknown) => {
          console.error('Error marking Slack answer segment:', error);
        });
    }

    // A tool call ends the text before it: send what is still buffered, then start over after it.
    function settleAnswerPreamble(): void {
      if (pushedAnswerLength > 0) {
        flushPendingAppend(true);
        // Its citations are known only while this call lasts: link them now.
        const [ready] = citationMarkers.resolve(
          pendingStreamText,
          liveCitationRows,
          true,
        );
        pendingStreamText = "";
        if (ready !== "") {
          queuedStreamAppend = queuedStreamAppend
            .then(async () => pushTextToSlackStream(ready))
            .catch((error: unknown) => {
              console.error("Error appending Slack stream text:", error);
            });
        }
      }
      resetAnswerStream();
    }

    // The backend dropped the model call and retries it: its shown text must go.
    function discardAnswerSegment(): void {
      const shown = pushedAnswerLength > 0;
      clearAnswerFlushTimer();
      pendingAppendText = '';
      heldAnswerText = '';
      streamedAnswerLength = 0;
      pushedAnswerLength = 0;
      lastAnswerText = '';
      answerTextStreamed = streamedBeforeSegment;
      // The discarded call's numbers are free again, so [n] keeps matching the Sources list.
      citationMarkers.rollback();
      liveCitationRows = new Map();
      pendingStreamText = "";
      if (!shown) {
        return;
      }
      queuedStreamAppend = queuedStreamAppend
        .then(async () => {
          await answerStream?.dropSegment();
        })
        .catch((error: unknown) => {
          console.error('Error taking back a discarded Slack answer:', error);
        });
    }

    const aguiHandler = createSlackAGUIEventHandler({
      onReasoning: (delta, done) => {
        if (delta) {
          answerPlan.reasoningDelta(delta);
        }
        if (done) {
          answerPlan.reasoningDone();
        }
      },
      onModelCallDiscarded: () => {
        answerPlan.dropReasoning();
        // The retry streams its answer again, and may have no table.
        discardAnswerSegment();
        tableStreamingDisabled = false;
        streamTableProbeText = '';
      },
      onNarration: (text) => {
        // Already shown as streamed text: don't repeat it.
        if (pushedAnswerLength === 0) {
          pushNarration(text);
        }
      },
      onToolProgress: (toolName, progress) => {
        pushPlan([answerPlan.toolProgress(toolName, progress)]);
      },
      onToolStart: (toolName, displayName, toolCallId, call) => {
        // The dashboard's label from the start, so a row doesn't change its wording when the call ends.
        pushPlan([
          answerPlan.flushReasoning(),
          answerPlan.toolStarted(
            toolName,
            toolActivityLabel(toolName, displayName),
            toolCallId,
            call,
          ),
        ]);
      },
      onToolArgs: (toolName, args, toolCallId) => {
        pushPlan([answerPlan.toolArgs(toolName, args, toolCallId)]);
      },
      onToolResult: (toolName, displayName, status, toolCallId, result) => {
        pushPlan([
          answerPlan.toolFinished(
            toolName,
            toolActivityLabel(toolName, displayName),
            status,
            toolCallId,
            result,
          ),
        ]);
      },
      onSubAgent: (role, phase) => {
        pushPlan([answerPlan.flushReasoning(), answerPlan.subAgent(role, phase)]);
      },
      onAnswerAccumulated: handleAnswerAccumulated,
      onClearAnswerPreamble: settleAnswerPreamble,
      onAskUserQuestion: handleAskUserQuestion,
      onArtifact: handleArtifact,
      onConversationCreated: (conversationId) => {
        conversationPersistPromise = persistConversationId(conversationId);
      },
      onCitations: (citations) => {
        // A known citation keeps its link for the rest of the call: the backend clears them before a tool call.
        liveCitationRows = new Map([
          ...liveCitationRows,
          ...citationRows(
            citations as SlackCitationLike[],
            getFrontendBaseUrl(),
          ),
        ]);
        if (pendingStreamText === "" || textSendQueued) {
          return;
        }
        // A citation came in: text that waited for it can go now.
        textSendQueued = true;
        queuedStreamAppend = queuedStreamAppend
          .then(async () => {
            textSendQueued = false;
            const [ready, waiting] = citationMarkers.resolve(
              pendingStreamText,
              liveCitationRows,
              false,
            );
            pendingStreamText = waiting;
            if (ready !== "") {
              await pushTextToSlackStream(ready);
            }
          })
          .catch((error: unknown) => {
            console.error("Error appending Slack stream text:", error);
          });
      },
      onComplete: (conv) => {
        completionConversation = conv as ConversationData["conversation"];
      },
      onError: (message) => {
        streamErrorMessage = resolveSlackErrorMessage(message);
      },
    });

    const pushTextToSlackStream = async (text: string): Promise<void> => {
      // No stream (status-message reply) or an error was recorded: nothing to append.
      if (!answerStream || streamErrorMessage) {
        return;
      }

      if (text.length === 0) {
        return;
      }

      // Slack renders markdown_text as markdown: send it as written.
      text = stripArtifactMarkers(text);
      if (text.length === 0) {
        return;
      }

      await answerStream.answer(text);
      await fallBackToStatusMessage();
    };

    const flushPendingAppend = (final = false): void => {
      clearAnswerFlushTimer();
      const unsent = heldAnswerText + pendingAppendText;
      let [ready, held] = final
        ? [unsent, ""]
        : splitOpenLinkTail(unsent);
      // Lines starting with `|` may be a table's first rows: they wait until it is known, so no row goes out as text.
      const rowsAt = final ? -1 : trailingTableRowsStart(streamTableProbeText);
      const cut = rowsAt - (streamTableProbeText.length - unsent.length);
      if (rowsAt >= 0 && cut < ready.length) {
        [ready, held] = [unsent.slice(0, Math.max(0, cut)), unsent.slice(Math.max(0, cut))];
      }
      pendingAppendText = "";
      heldAnswerText = held;
      const textToAppend = ready;
      if (!textToAppend) {
        return;
      }

      answerPlan.endGroup();
      pushPlan([answerPlan.flushReasoning()]);
      pushedAnswerLength += textToAppend.length;
      answerTextStreamed = true;
      // Numbered now, in order: a reset may come before this is sent.
      pendingStreamText += citationMarkers.mark(textToAppend);
      if (textSendQueued) {
        return;
      }
      // Pieces that arrive while a send waits go out together in it.
      textSendQueued = true;
      queuedStreamAppend = queuedStreamAppend
        .then(async () => {
          textSendQueued = false;
          // Text from a citation whose link isn't known yet waits for the next refresh.
          const [ready, waiting] = citationMarkers.resolve(
            pendingStreamText,
            liveCitationRows,
            false,
          );
          pendingStreamText = waiting;
          if (ready !== "") {
            await pushTextToSlackStream(ready);
          }
        })
        .catch((error) => {
          console.error("Error appending Slack stream text:", error);
          if (!streamErrorMessage) {
            streamErrorMessage = STREAM_FAILURE_MESSAGE;
          }
        });
    };


    await new Promise<void>((resolve, reject) => {
      responseStream.setEncoding("utf8");
      let settled = false;

      const cleanupListeners = (): void => {
        responseStream.removeListener("data", onData);
        responseStream.removeListener("end", onEnd);
        responseStream.removeListener("error", onError);
      };

      const resolveOnce = (): void => {
        if (settled) {
          return;
        }
        settled = true;
        cleanupListeners();
        resolve();
      };

      const rejectOnce = (error: unknown): void => {
        if (settled) {
          return;
        }
        settled = true;
        cleanupListeners();
        reject(error);
      };

      const onData = (chunk: string): void => {
        sseBuffer += chunk;
        const { events, remainder } = parseSSEEvents(sseBuffer);
        sseBuffer = remainder;

        for (const evt of events) {
          try {
            aguiHandler(evt);
          } catch (error) {
            console.error("Error handling AG-UI stream event:", error);
            continue;
          }
          if (streamErrorMessage) {
            resolveOnce();
            return;
          }
        }
      };

      const onEnd = (): void => {
        resolveOnce();
      };

      const onError = (error: unknown): void => {
        rejectOnce(error);
      };

      responseStream.on("data", onData);
      responseStream.on("end", onEnd);
      responseStream.on("error", onError);
    });

    clearAnswerFlushTimer();
    flushPendingAppend(true);
    await queuedStreamAppend;

    if (streamErrorMessage) {
      if (answerStream) {
        const stopStreamSucceeded = await stopSlackStream(streamErrorMessage);
        if (!stopStreamSucceeded) {
          await sendOrUpdateNonStreamMessage(streamErrorMessage);
        }
      } else {
        await sendOrUpdateNonStreamMessage(streamErrorMessage);
      }
      return;
    }

    const conversationData =
      completionConversation as ConversationData["conversation"] | null;

    if (conversationData?._id) {
      await persistConversationId(conversationData._id);
    }

    // Ask-user parks the turn on clarification — no bot_response required.
    // Only live SSE ask-user events count (TOOL_CALL_ARGS / CUSTOM). Do not
    // re-read historical ask_user tool_calls from conversation.messages — that
    // re-posts old questions on the next turn instead of the real answer.
    if (askUserShown) {
      await deleteStreamMessages();
      await conversationPersistPromise;
      if (!conversationData && !conversationPersisted) {
        await sendOrUpdateNonStreamMessage(
          "Received an incomplete response from the backend. Please try again later.",
        );
      }
      return;
    }

    if (!conversationData) {
      const incompleteResponseMessage =
        "Received an incomplete response from the backend. Please try again later.";
      if (answerStream) {
        const stopStreamSucceeded = await stopSlackStream(incompleteResponseMessage);
        if (!stopStreamSucceeded) {
          await sendOrUpdateNonStreamMessage(incompleteResponseMessage);
        }
      } else {
        await sendOrUpdateNonStreamMessage(incompleteResponseMessage);
      }
      return;
    }

    const botResponses = conversationData.messages;
    const botResponse =
      [...botResponses].reverse().find((msg) => msg.messageType === "bot_response") ??
      null;
    if (!botResponse) {
      const invalidResponseMessage =
        "Received an unexpected response format from the backend. Please try again later.";
      if (answerStream) {
        const stopStreamSucceeded = await stopSlackStream(invalidResponseMessage);
        if (!stopStreamSucceeded) {
          await sendOrUpdateNonStreamMessage(invalidResponseMessage);
        }
      } else {
        await sendOrUpdateNonStreamMessage(invalidResponseMessage);
      }
      return;
    }

    const citationBlocks = buildCitationSources(botResponse.citations);
    const citationBlockChunks = splitSlackBlocksByLimit(citationBlocks);
    // Frontend strips ::artifact markers into an Artifacts panel; Slack must
    // not show the raw wire syntax in the final answer body.
    const { artifacts: contentArtifacts } = parseArtifactMarkers(
      botResponse.content || "",
    );
    // `plainMarkers`: keep [N] as streamed, so the tail matches the numbered sources.
    const finalRows = citationRows(botResponse.citations, getFrontendBaseUrl());
    const toSlackBlocks = async (
      markdown: string,
      plainMarkers = false,
    ): ReturnType<typeof buildFinalSlackChunks> => {
      const text = parseArtifactMarkers(markdown).text;
      // Citations as clickable [n] to absolute links, numbered like the Sources list.
      let body = plainMarkers
        ? citationMarkers.resolve(
            citationMarkers.mark(text),
            finalRows,
            true,
          )[0]
        : stripNumberedCitationLinkTargets(
            text,
            botResponse.citations,
            getFrontendBaseUrl(),
          );
      body = removeContinuousDuplicateMarkdownLinks(body);
      body = addSpaceBetweenMarkdownLinks(body);
      return buildFinalSlackChunks(body);
    };

    // Reasoning with no step after it still gets its place in the plan, above the answer.
    pushPlan([answerPlan.flushReasoning()]);
    await queuedStreamAppend;

    // Text still waiting for a citation goes out with the finished answer's citations.
    if (pendingStreamText !== "") {
      const [ready] = citationMarkers.resolve(
        pendingStreamText,
        finalRows,
        true,
      );
      pendingStreamText = "";
      if (ready !== "") {
        await pushTextToSlackStream(ready);
      }
    }

    if (answerStream && pushedAnswerLength === 0 && !tableStreamingDisabled && botResponse.content) {
      // An answer that came only with the run's end still gets its tables as blocks.
      // Its citations are numbered like the markers shown before it, so they match the Sources list.
      const finalText = citationMarkers.renumber(
        botResponse.content,
        botResponse.citations,
        getFrontendBaseUrl(),
      );
      const [beforeTable, fromTable] = splitBeforeFirstTable(finalText);
      if (fromTable !== "") {
        tableStreamingDisabled = true;
        lastAnswerText = finalText;
      }
      pushedAnswerLength = beforeTable.length;
      if (beforeTable.trim() !== "") {
        await pushTextToSlackStream(citationMarkers.mark(beforeTable));
      }
    }

    if (answerStream) {
      // A table stops the text stream; the rest of the answer goes below it as blocks.
      const unstreamed = tableStreamingDisabled
        ? (lastAnswerText || botResponse.content || "").slice(pushedAnswerLength)
        : "";
      // The markers actually shown, numbered as they were; else the finished answer's.
      const numberedSources =
        citationMarkers.sources().size > 0
          ? numberedSourcesBlocks(citationMarkers.sources())
          : buildNumberedSlackSources(
              botResponse.citations,
              getFrontendBaseUrl(),
            );
      const tailChunks = [
        ...(unstreamed.trim() ? await toSlackBlocks(unstreamed, true) : []),
        ...(numberedSources.length > 0
          ? splitSlackBlocksByLimit(numberedSources)
          : citationBlockChunks),
      ];
      const [stopBlocks, ...moreChunks] = tailChunks;
      const answered = pushedAnswerLength > 0 || tailChunks.length > 0;
      const stopped = await answerStream.stop(
        stopBlocks
          ? { blocks: stopBlocks }
          : answered
            ? {}
            : { markdown: FAILED_RESPONSE_GENERATION_MESSAGE },
      );
      if (!stopped && !answered) {
        await sendOrUpdateNonStreamMessage(FAILED_RESPONSE_GENERATION_MESSAGE);
      }
      for (const chunk of stopped ? moreChunks : tailChunks) {
        await postThreadChunkMessage(chunk);
      }
    } else {
      const finalChunks = await toSlackBlocks(botResponse.content || "");
      if (finalChunks.length === 0) {
        // Nothing to post: the failure notice replaces the status message.
        await sendOrUpdateNonStreamMessage(FAILED_RESPONSE_GENERATION_MESSAGE);
        return;
      }
      const fallbackSources = buildNumberedSlackSources(
        botResponse.citations,
        getFrontendBaseUrl(),
      );
      for (const chunk of [
        ...finalChunks,
        ...(fallbackSources.length > 0
          ? splitSlackBlocksByLimit(fallbackSources)
          : citationBlockChunks),
      ]) {
        await postThreadChunkMessage(chunk);
      }
      if (waitingMessageTs) {
        try {
          await typedClient.apiCall("chat.delete", {
            channel: typedMessage.channel!,
            ts: waitingMessageTs,
          });
        } catch (error) {
          const code = (error as { data?: { error?: string } }).data?.error;
          if (code !== "message_not_found") {
            console.error("Error deleting Slack status message:", error);
          }
        }
        waitingMessageTs = null;
      }
    }

    // Post artifact record links after the answer (mirrors frontend Artifacts panel).
    // Markers often use `record:<id>` placeholders rather than signed download URLs.
    for (const artifact of contentArtifacts) {
      const link = resolveSlackArtifactLink(artifact);
      if (!link) {
        continue;
      }
      const dedupeKey = artifact.recordId || link;
      if (postedArtifactKeys.has(dedupeKey) || postedArtifactKeys.has(link)) {
        continue;
      }
      postedArtifactKeys.add(dedupeKey);
      postedArtifactKeys.add(link);
      await typedClient.chat.postMessage({
        channel: typedMessage.channel!,
        thread_ts: threadId,
        text: truncateForSlack(`*Artifact:* <${link}|${artifact.fileName}>`),
        ...NO_UNFURL_OPTIONS,
      });
    }
  } catch (error) {
    try {
      const errorMessage = await resolveSlackErrorMessageAsync(error);
      if (answerStream) {
        const stopStreamSucceeded = await stopSlackStream(errorMessage);
        if (!stopStreamSucceeded) {
          await sendOrUpdateNonStreamMessage(errorMessage);
        }
      } else {
        await sendOrUpdateNonStreamMessage(errorMessage);
      }
    } catch (handlerError) {
      console.error("Error in Slack message error handler:", handlerError);
    }
  }
}

// Handle DMs via message.im events.
app.message(async ({ message, client, context }) => {
  if (!message || typeof message !== "object") {
    return;
  }

  const typedMessage = message as SlackMessagePayload;
  const typedClient = client as unknown as TypedSlackClient;
  const typedContext = context as TypedSlackContext;

  if (isIgnoredSlackMessage(typedMessage, typedContext)) {
    return;
  }

  const isDirectMessage = typedMessage.channel?.startsWith("D") || false;
  if (!isDirectMessage) {
    return;
  }

  const resolvedSlackBot = await resolveSlackBotForEvent(getCurrentMatchedSlackBot);
  const { shouldAnswer, hasSupported } = await handleIncomingAttachments(
    typedClient,
    typedMessage,
    typedContext.botUserId,
  );
  if (!shouldAnswer) return;

  let query = await resolveMentionsInText(typedMessage.text, typedClient);
  if (!query) {
    if (hasSupported) query = "See below attached file(s).";
    else query = "Hi";
  }

  try {
    await processSlackMessage(
      typedMessage,
      typedClient,
      typedContext,
      query,
      resolvedSlackBot,
    );
  } catch (error) {
    console.error("Error handling DM message:", error);
    await sendUserFacingSlackErrorMessage(typedClient, typedMessage, error);
  }
});

// Handle @mentions in channels via app_mention events.
app.event("app_mention", async ({ event, client, context }) => {
  const typedMessage = event as unknown as SlackMessagePayload;
  const typedClient = client as unknown as TypedSlackClient;
  const typedContext = context as TypedSlackContext;
  if (isIgnoredSlackMessage(typedMessage, typedContext)) {
    return;
  }

  const resolvedSlackBot = await resolveSlackBotForEvent(getCurrentMatchedSlackBot);
  const { shouldAnswer, hasSupported } = await handleIncomingAttachments(
    typedClient,
    typedMessage,
    typedContext.botUserId,
  );
  if (!shouldAnswer) return;

  let query = await resolveMentionsInText(typedMessage.text, typedClient);
  if (!query) {
    if (hasSupported) query = "Attached file(s).";
    else query = "Hi";
  }

  try {
    const thread = await fetchThreadContext(typedClient, typedMessage);
    const contextualQuery = await buildQueryWithThreadContext(
      typedClient,
      thread.messages,
      query,
      typedMessage.user,
      typedContext.botUserId,
    );
    const threadFiles = await collectThreadFiles(
      typedClient,
      typedMessage,
      thread,
      resolvedSlackBot?.botId ?? 'default',
      typedContext.botUserId,
    );
    await processSlackMessage(
      typedMessage,
      typedClient,
      typedContext,
      contextualQuery,
      resolvedSlackBot,
      threadFiles,
    );
  } catch (error) {
    console.error("Error handling app mention:", error);
    await sendUserFacingSlackErrorMessage(typedClient, typedMessage, error);
  }
});

(async () => {
  // Same Redis bootstrap contract as Application.initialize(): this is its
  // own process, so without it ConfigService -> KV store resolves REDIS_MODE
  // against a registry that only knows the OSS modes.
  await RedisConnectionProviderFactory.ensureProviderModuleLoaded();
  await getPreparedRedisProvider();

  // No MongoDB connection (or legacy-index cleanup) when the document store is PostgreSQL.
  if (usesMongo()) await connect();
  // Same document-store wiring as the API: a no-op on Mongo, the PostgreSQL runtime under DOC_STORE=postgres.
  // the bot runs no dispatcher or sweeper, so it needs no jobs login
  await initDocStore(process.env.APP_VERSION ?? 'dev', { jobs: false });

  // Drop legacy threadId + botId index if it exists
  if (usesMongo()) await dropLegacyThreadBotIndex();

  await app.start(process.env.SLACK_BOT_PORT || 3020);
  console.log("Bolt app is running on 3020.");
})();
