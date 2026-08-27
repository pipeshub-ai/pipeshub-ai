import { config } from "dotenv";
config(); // Load environment variables first

import { json, urlencoded, Request, Response } from "express";
import { connect, dropLegacyThreadBotIndex } from "./utils/db";
import { getFromDatabase, saveToDatabase } from "./utils/conversation";
import axios from "axios";
import { marked } from "marked";
// Disable marked's email mangling to prevent HTML entity encoding of email addresses.
// @tryfabric/mack uses the same marked instance internally.
marked.setOptions({ mangle: false } as any);
import app from "./slackApp";
import receiver from "./receiver";
import { ConfigService } from "../../../modules/tokens_manager/services/cm.service";
import { slackJwtGenerator } from "../../../libs/utils/createJwt";
import { markdownToSlackMrkdwn } from "./utils/md_to_mrkdwn";
import { createSlackAGUIEventHandler } from "./utils/agui-stream";
import {
  SlackActivityBuilder,
  stripSlackActivityTimeline,
} from "./utils/activity-ui";
import {
  formatAskUserQuestionMrkdwn,
  type AskUserQuestionEvent,
} from "./utils/ask-user-format";
import {
  toolActivityLabel,
  toolStatusLabel,
} from "./utils/tool-display";
import { parseArtifactMarkers } from "./utils/parse-artifact-markers";
import { rewriteCitationsForSlack, stripTinyRefCitationLinks } from "./utils/citations";

import {
  type SlackBotConfig,
} from "./botRegistry";

import {
  type CitationData,
  type ConversationData,
  type StreamStartResult,
  type SlackMessagePayload,
  type TypedSlackClient,
  type AttachmentRef,
  FAILED_RESPONSE_GENERATION_MESSAGE,
  STREAM_UPDATE_THROTTLE_MS,
  STREAM_UPDATE_MAX_CHARS,
  ACTIVITY_UPDATE_THROTTLE_MS,
  SLACK_STREAM_MARKDOWN_LIMIT,
  SLACK_STREAM_MESSAGE_CHAR_LIMIT,
  NO_UNFURL_OPTIONS,
  STREAM_FAILURE_MESSAGE,
  BACKEND_STREAM_TIMEOUT_MS,
  TABLE_STREAMING_PAUSED_HINT,
  truncateForSlack,
  truncateForSlackStreamMarkdown,
  splitByLengthPreferringNewlines,
  hasMarkdownTableStartOutsideCodeFences,
  buildFinalSlackChunks,
  splitSlackBlocksByLimit,
  classifySlackFiles,
  extractSupportedAttachments,
  uploadSlackAttachments,
  postUnsupportedAttachmentsNotice,
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
  fetchPriorThreadMessages,
  resolveThreadUserLabels,
  inferThreadMessageSpeaker,
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

/** Mid-stream: hide normalized record citation links (final message rewrites them). */
const INLINE_RECORD_CITATION_LINK_PATTERN =
  /\[(\d+)\]\(([^)]*?\/record\/[^)]*?)\)/g;

async function buildThreadContextualQuery(
  query: string,
  priorMessages: SlackMessagePayload[],
  userLabelsById: Map<string, string>,
  typedClient: TypedSlackClient,
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
      const speaker = inferThreadMessageSpeaker(message, userLabelsById);
      return `${speaker}: ${normalizedText}`;
    })
  ).then(lines => lines.filter((line): line is string => Boolean(line)));

  if (contextLines.length === 0) {
    return query;
  }

  return `Slack thread context:\n${contextLines.join("\n")}\n\nCurrent slack message/query: ${query}`;
}

async function buildQueryWithThreadContext(
  typedClient: TypedSlackClient,
  typedMessage: SlackMessagePayload,
  query: string,
): Promise<string> {
  if (!isThreadFollowUpMessage(typedMessage)) {
    return query;
  }

  try {
    const priorMessages = await fetchPriorThreadMessages(typedClient, typedMessage);
    const userLabelsById = await resolveThreadUserLabels(typedClient, priorMessages);
    return await buildThreadContextualQuery(query, priorMessages, userLabelsById, typedClient);
  } catch (error) {
    console.error("Failed to fetch Slack thread context:", error);
    return query;
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

function rewriteInlineRecordCitationsForSlack(
  answerBody: string,
  citations?: CitationData[],
): string {
  return rewriteCitationsForSlack(
    answerBody,
    citations,
    getFrontendBaseUrl(),
  );
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
    // Strip text fragment directive (#:~:text=...) but preserve other fragments
    const recordUrl = webUrl.replace(/#:~:text=[^#]*/, '');
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
receiver.router.get("/", (req: Request, res: Response) => {
  console.log(req);
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
  const accessToken = slackJwtGenerator(email, await configService.getScopedJwtSecret());

  const currentAgentId = resolvedSlackBot?.agentId || null;
  console.log("currentAgentId", currentAgentId);
  const currentBotId = resolvedSlackBot?.botId;
  if (!currentBotId) {
    throw new Error("Unable to resolve Slack bot id for conversation persistence.");
  }

  const conversation = await getFromDatabase(
    threadId,
    currentBotId,
    email,
  );
  let streamTs: string | null = null;
  let streamStopped = false;
  let streamCharCount = 0;
  let rolledOverStreamTs: string[] = [];
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
        text: "",
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

  const stopSlackStream = async (markdownText?: string): Promise<boolean> => {
    if (!streamTs || streamStopped) {
      return true;
    }

    const payload: Record<string, unknown> = {
      channel: typedMessage.channel!,
      ts: streamTs,
    };
    if (typeof markdownText === "string" && markdownText.length > 0) {
      payload.markdown_text = truncateForSlackStreamMarkdown(markdownText);
    }

    try {
      await typedClient.apiCall("chat.stopStream", payload);
      streamStopped = true;
      return true;
    } catch (error) {
      if (
        error &&
        typeof error === "object" &&
        "data" in error &&
        (error as { data?: { error?: string } }).data?.error ===
          "message_not_in_streaming_state"
      ) {
        streamStopped = true;
        return true;
      }
      console.error("Error stopping Slack stream:", error);
      return false;
    }
  };

  const rolloverSlackStream = async (): Promise<void> => {
    if (!streamTs) return;
    try {
      await typedClient.apiCall("chat.stopStream", {
        channel: typedMessage.channel!,
        ts: streamTs,
      });
    } catch (error) {
      const code = (error as { data?: { error?: string } }).data?.error;
      if (code !== "message_not_in_streaming_state") throw error;
    }
    rolledOverStreamTs.push(streamTs);
    streamTs = null;
    streamCharCount = 0;
    // streamStopped stays false — the overall session is still active
  };

  try {
    const streamRecipientPayload: Record<string, unknown> = {};
    streamRecipientPayload.recipient_user_id = typedMessage.user;
    if (typedContext.teamId) {
      streamRecipientPayload.recipient_team_id = typedContext.teamId;
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

    // Handle file attachments for agents
    let attachmentRefs: AttachmentRef[] = [];
    if (typedMessage.files && typedMessage.files.length > 0) {
      const supportedFiles = extractSupportedAttachments(typedMessage.files);
      if (supportedFiles.length > 0) {
        try {
          const botToken = resolvedSlackBot?.botToken;
          if (botToken) {
            attachmentRefs = await uploadSlackAttachments(supportedFiles, botToken, accessToken, currentAgentId);
            console.log(`Uploaded ${attachmentRefs.length} attachment(s) for chat`);
          }
        } catch (uploadError) {
          const errData = (uploadError as any).response?.data;
          const errMsg = errData
            ? JSON.stringify(errData)
            : (uploadError as any).message ?? String(uploadError);
          console.error("Error uploading attachments:", errMsg);
          await sendUserFacingSlackErrorMessage(typedClient, typedMessage, uploadError);
          return;
        }
      }
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
    let lastActivityAt = 0;
    let streamErrorMessage: string | null = null;
    let completionConversation: ConversationData["conversation"] | null = null;
    // Answer appends and activity updates use separate queues so chat.update
    // latency cannot stall chat.appendStream mid-answer.
    let queuedStreamAppend: Promise<void> = Promise.resolve();
    let queuedActivityUpdate: Promise<void> = Promise.resolve();
    let tableStreamingDisabled = false;
    let streamTableProbeText = "";
    let tablePauseHintSent = false;
    let streamedAnswerLength = 0;
    let ignoreAnswerChunks = false;
    let askUserShown = false;
    let conversationPersisted = Boolean(conversation);
    let conversationPersistPromise: Promise<void> = Promise.resolve();
    const activityBuilder = new SlackActivityBuilder();
    let pendingActivityUpdate = false;
    let answerFlushTimer: ReturnType<typeof setTimeout> | null = null;
    let activityFlushTimer: ReturnType<typeof setTimeout> | null = null;
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

    const flushActivityUpdate = (): void => {
      if (!pendingActivityUpdate || askUserShown) {
        pendingActivityUpdate = false;
        return;
      }
      pendingActivityUpdate = false;
      // Format at write time so a later setStatus("") isn't overwritten by a
      // stale snapshot captured when the flush was queued.
      queuedActivityUpdate = queuedActivityUpdate
        .then(async () => updateActivityMessage(activityBuilder.format()))
        .catch((error) => {
          console.error("Error flushing Slack activity update:", error);
        });
    };

    const scheduleActivityUpdate = (): void => {
      // Ask-user replaces the activity message with questions; don't overwrite it.
      if (askUserShown) {
        return;
      }
      pendingActivityUpdate = true;
      const now = Date.now();
      if (now - lastActivityAt >= ACTIVITY_UPDATE_THROTTLE_MS) {
        lastActivityAt = now;
        if (activityFlushTimer) {
          clearTimeout(activityFlushTimer);
          activityFlushTimer = null;
        }
        flushActivityUpdate();
        return;
      }
      if (!activityFlushTimer) {
        const waitMs = Math.max(
          0,
          ACTIVITY_UPDATE_THROTTLE_MS - (now - lastActivityAt),
        );
        activityFlushTimer = setTimeout(() => {
          activityFlushTimer = null;
          lastActivityAt = Date.now();
          flushActivityUpdate();
        }, waitMs);
      }
    };

    const deleteStreamMessages = async (): Promise<void> => {
      if (streamTs) {
        try {
          await stopSlackStream();
        } catch (error) {
          console.error("Error stopping Slack stream before reset:", error);
        }
        try {
          await typedClient.apiCall("chat.delete", {
            channel: typedMessage.channel!,
            ts: streamTs,
          });
        } catch (deleteError) {
          const code = (deleteError as { data?: { error?: string } }).data?.error;
          if (code !== "message_not_found") {
            console.error("Error deleting Slack stream message:", deleteError);
          }
        }
        streamTs = null;
        streamStopped = false;
        streamCharCount = 0;
      }
      for (const oldTs of rolledOverStreamTs) {
        try {
          await typedClient.apiCall("chat.delete", {
            channel: typedMessage.channel!,
            ts: oldTs,
          });
        } catch (deleteError) {
          const code = (deleteError as { data?: { error?: string } }).data?.error;
          if (code !== "message_not_found") {
            console.error("Error deleting rolled-over stream message:", deleteError);
          }
        }
      }
      rolledOverStreamTs = [];
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
      if (hasMarkdownTableStartOutsideCodeFences(streamTableProbeText)) {
        tableStreamingDisabled = true;
        pendingAppendText = "";
        clearAnswerFlushTimer();
        queuedStreamAppend = queuedStreamAppend
          .then(async () => sendTableStreamingPausedHint())
          .catch((error) => {
            console.error("Error sending Slack table formatting hint:", error);
          });
        return;
      }

      pendingAppendText += nextChunk;
      // Large buffer → flush now. Otherwise arm a short idle timer so a pause
      // in token arrival still pushes what's buffered (avoids multi-second stalls).
      if (pendingAppendText.length >= STREAM_UPDATE_MAX_CHARS) {
        clearAnswerFlushTimer();
        flushPendingAppend();
        return;
      }
      if (!answerFlushTimer) {
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
        clearAnswerFlushTimer();
        pendingAppendText = "";
        streamedAnswerLength = 0;
        queuedStreamAppend = queuedStreamAppend
          .then(async () => deleteStreamMessages())
          .catch((error) => {
            console.error("Error clearing Slack answer preamble:", error);
          });
      }
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
      pendingActivityUpdate = false;
      if (activityFlushTimer) {
        clearTimeout(activityFlushTimer);
        activityFlushTimer = null;
      }

      // Replace the Thinking/activity placeholder with the questions so
      // "_Thinking..._" is not left sitting above the clarification.
      queuedStreamAppend = queuedStreamAppend
        .then(async () => {
          await deleteStreamMessages();
          await queuedActivityUpdate.catch(() => undefined);
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

    const aguiHandler = createSlackAGUIEventHandler({
      onStatus: (message) => {
        activityBuilder.setStatus(message);
        scheduleActivityUpdate();
      },
      onReasoning: (delta, done) => {
        if (delta) {
          activityBuilder.appendReasoning(delta);
          scheduleActivityUpdate();
        }
        if (done) {
          activityBuilder.finishReasoning();
          scheduleActivityUpdate();
        }
      },
      onNarration: (text) => {
        activityBuilder.appendNarration(text);
        scheduleActivityUpdate();
      },
      onToolStart: (toolName, displayName) => {
        activityBuilder.startTool(
          toolName,
          toolStatusLabel(toolName, displayName),
        );
        scheduleActivityUpdate();
      },
      onToolResult: (toolName, displayName, status) => {
        activityBuilder.finishTool(
          toolName,
          toolActivityLabel(toolName, displayName),
          status === "failed" || status === "blocked",
        );
        scheduleActivityUpdate();
      },
      onSubAgent: (role, phase) => {
        if (phase === "started") {
          activityBuilder.startSubAgent(role);
        } else {
          activityBuilder.finishSubAgent(role, phase);
        }
        scheduleActivityUpdate();
      },
      onAnswerAccumulated: handleAnswerAccumulated,
      onClearAnswerPreamble: () => {
        clearAnswerFlushTimer();
        pendingAppendText = "";
        streamedAnswerLength = 0;
        queuedStreamAppend = queuedStreamAppend
          .then(async () => deleteStreamMessages())
          .catch((error) => {
            console.error("Error clearing Slack answer preamble:", error);
          });
      },
      onAskUserQuestion: handleAskUserQuestion,
      onArtifact: handleArtifact,
      onConversationCreated: (conversationId) => {
        conversationPersistPromise = persistConversationId(conversationId);
      },
      onComplete: (conv) => {
        completionConversation = conv as ConversationData["conversation"];
      },
      onError: (message) => {
        streamErrorMessage = resolveSlackErrorMessage(message);
      },
    });

    const pushTextToSlackStream = async (text: string): Promise<void> => {
      // Bail out early if the stream is already stopped or an error was recorded —
      // prevents the independent-catch queue items from firing redundant API calls.
      if (streamStopped || streamErrorMessage) {
        return;
      }

      if (text.length === 0) {
        return;
      }

      // Raw `[source](refN)` becomes broken `<refN|source>` in Slack mrkdwn.
      text = stripTinyRefCitationLinks(text);
      text = text.replace(INLINE_RECORD_CITATION_LINK_PATTERN, "");
      text = parseArtifactMarkers(text).text;
      if (text.length === 0) {
        return;
      }

      const renderedDeltaText = markdownToSlackMrkdwn(text, {
        preserveTrailingWhitespace: true,
      });
      if (renderedDeltaText.length === 0) {
        return;
      }

      const markdownChunks = splitByLengthPreferringNewlines(
        renderedDeltaText,
        SLACK_STREAM_MARKDOWN_LIMIT,
      );
      if (markdownChunks.length === 0) {
        return;
      }

      for (let chunk of markdownChunks) {
        while (chunk.length > 0) {
          if (streamStopped || streamErrorMessage) {
            return;
          }

          // If this chunk would overflow the current message, split at a clean boundary first
          if (streamTs && streamCharCount + chunk.length > SLACK_STREAM_MESSAGE_CHAR_LIMIT) {
            const spaceLeft = SLACK_STREAM_MESSAGE_CHAR_LIMIT - streamCharCount;

            if (spaceLeft > 0) {
              // Prefer splitting at the last newline within the remaining space
              const candidate = chunk.slice(0, spaceLeft);
              const lastNewline = candidate.lastIndexOf("\n");
              const splitIndex = lastNewline > -1 ? lastNewline + 1 : spaceLeft;
              const fitsInCurrent = chunk.slice(0, splitIndex);

              if (fitsInCurrent.length > 0) {
                await typedClient.apiCall("chat.appendStream", {
                  channel: typedMessage.channel!,
                  ts: streamTs,
                  markdown_text: fitsInCurrent,
                });
                streamCharCount += fitsInCurrent.length;
              }
              chunk = chunk.slice(splitIndex);
            }

            await rolloverSlackStream();
            continue; // re-evaluate the overflow with a fresh streamCharCount = 0
          }

          // No overflow — start a new stream or append to existing
          if (!streamTs) {
            const startStreamResult = (await typedClient.apiCall(
              "chat.startStream",
              {
                channel: typedMessage.channel!,
                thread_ts: threadId,
                markdown_text: chunk,
                ...NO_UNFURL_OPTIONS,
                ...streamRecipientPayload,
              },
            )) as StreamStartResult;

            if (!startStreamResult.ts) {
              throw new Error("Failed to start Slack stream");
            }
            streamTs = startStreamResult.ts;
            streamCharCount = chunk.length;
            // Keep the activity message above the answer stream.
          } else {
            await typedClient.apiCall("chat.appendStream", {
              channel: typedMessage.channel!,
              ts: streamTs,
              markdown_text: chunk,
            });
            streamCharCount += chunk.length;
          }
          break; // chunk fully consumed
        }
      }
    };

    const flushPendingAppend = (): void => {
      clearAnswerFlushTimer();
      const textToAppend = pendingAppendText;
      if (!textToAppend) {
        return;
      }

      pendingAppendText = "";
      queuedStreamAppend = queuedStreamAppend
        .then(async () => pushTextToSlackStream(textToAppend))
        .catch((error) => {
          console.error("Error appending Slack stream text:", error);
          if (!streamErrorMessage) {
            streamErrorMessage = STREAM_FAILURE_MESSAGE;
          }
        });
    };

    const sendTableStreamingPausedHint = async (): Promise<void> => {
      if (tablePauseHintSent) {
        return;
      }
      tablePauseHintSent = true;

      if (streamTs) {
        if (streamStopped || streamErrorMessage) {
          return;
        }

        const renderedHint = markdownToSlackMrkdwn(TABLE_STREAMING_PAUSED_HINT, {
          preserveTrailingWhitespace: true,
        });
        if (!renderedHint) {
          return;
        }

        try {
          await typedClient.apiCall("chat.appendStream", {
            channel: typedMessage.channel!,
            ts: streamTs,
            markdown_text: renderedHint,
          });
        } catch (error) {
          console.error("Error appending Slack table formatting hint:", error);
        }
        return;
      }

      if (!waitingMessageTs) {
        return;
      }

      try {
        await typedClient.chat.update({
          channel: typedMessage.channel!,
          ts: waitingMessageTs,
          text: TABLE_STREAMING_PAUSED_HINT,
          ...NO_UNFURL_OPTIONS,
        });
      } catch (error) {
        console.error("Error updating Slack waiting message with table hint:", error);
      }
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
    if (activityFlushTimer) {
      clearTimeout(activityFlushTimer);
      activityFlushTimer = null;
    }
    flushPendingAppend();
    // Never flush activity after ask-user — that would wipe the questions text.
    if (!askUserShown) {
      flushActivityUpdate();
    } else {
      pendingActivityUpdate = false;
    }
    await Promise.all([queuedStreamAppend, queuedActivityUpdate]);

    if (streamErrorMessage) {
      if (streamTs) {
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
      if (streamTs) {
        await deleteStreamMessages();
      }
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
      if (streamTs) {
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
      if (streamTs) {
        const stopStreamSucceeded = await stopSlackStream(invalidResponseMessage);
        if (!stopStreamSucceeded) {
          await sendOrUpdateNonStreamMessage(invalidResponseMessage);
        }
      } else {
        await sendOrUpdateNonStreamMessage(invalidResponseMessage);
      }
      return;
    }

    // Keep tools/thinking timeline above the answer; remove a bare Thinking placeholder.
    if (activityBuilder.hasTimeline()) {
      activityBuilder.setStatus("");
      await updateActivityMessage(activityBuilder.format());
    } else if (waitingMessageTs) {
      try {
        await typedClient.apiCall("chat.delete", {
          channel: typedMessage.channel!,
          ts: waitingMessageTs,
        });
      } catch (error) {
        const code = (error as { data?: { error?: string } }).data?.error;
        if (code !== "message_not_found") {
          console.error("Error deleting Slack activity placeholder:", error);
        }
      } finally {
        waitingMessageTs = null;
      }
    }

    if (!streamTs && !tableStreamingDisabled && botResponse.content) {
      await pushTextToSlackStream(botResponse.content);
    }

    const citationBlocks = buildCitationSources(botResponse.citations);
    const citationBlockChunks = splitSlackBlocksByLimit(citationBlocks);
    // Frontend strips ::artifact markers into an Artifacts panel; Slack must
    // not show the raw wire syntax in the final answer body.
    const { text: contentWithoutArtifacts, artifacts: contentArtifacts } =
      parseArtifactMarkers(botResponse.content || "");
    let answerBody = rewriteInlineRecordCitationsForSlack(
      contentWithoutArtifacts,
      botResponse.citations,
    );
    answerBody = removeContinuousDuplicateMarkdownLinks(answerBody);
    answerBody = addSpaceBetweenMarkdownLinks(answerBody);
    const finalChunks = await buildFinalSlackChunks(answerBody);

    const [firstFinalChunk, ...remainingFinalChunks] = finalChunks;

    if (firstFinalChunk) {
      let firstChunkSent = false;

      if (streamTs) {
        await stopSlackStream();

        // Delete any earlier rolled-over stream messages — the final blocks
        // represent the full answer so those partial-text messages are redundant.
        for (const oldTs of rolledOverStreamTs) {
          try {
            await typedClient.apiCall("chat.delete", {
              channel: typedMessage.channel!,
              ts: oldTs,
            });
          } catch (deleteError) {
            const code = (deleteError as { data?: { error?: string } }).data?.error;
            if (code !== "message_not_found") {
              console.error("Error deleting rolled-over stream message:", deleteError);
            }
          }
        }
        rolledOverStreamTs = [];

        try {
          await typedClient.chat.update({
            channel: typedMessage.channel!,
            ts: streamTs,
            text: "",
            blocks: firstFinalChunk,
            ...NO_UNFURL_OPTIONS,
          });
          firstChunkSent = true;
        } catch (updateError) {
          console.error(
            "Error updating final streamed Slack message with blocks, trying delete and repost:",
            updateError,
          );
          try {
            try {
              await typedClient.apiCall("chat.delete", {
                channel: typedMessage.channel!,
                ts: streamTs,
              });
            } catch (deleteError) {
              // If the message is already gone, we can still post a fresh one
              const code = (deleteError as { data?: { error?: string } }).data?.error;
              if (code !== "message_not_found") {
                throw deleteError;
              }
            }
            await typedClient.chat.postMessage({
              channel: typedMessage.channel!,
              thread_ts: threadId,
              text: "",
              blocks: firstFinalChunk,
              ...NO_UNFURL_OPTIONS,
            });
            firstChunkSent = true;
          } catch (replacementError) {
            console.error(
              "Error replacing failed streamed Slack message, sending fallback error message:",
              replacementError,
            );
            await typedClient.chat.postMessage({
              channel: typedMessage.channel!,
              thread_ts: threadId,
              text: FAILED_RESPONSE_GENERATION_MESSAGE,
              ...NO_UNFURL_OPTIONS,
            });
          }
        }
      } else {
        // Post answer as a new message so the activity timeline stays above it.
        await typedClient.chat.postMessage({
          channel: typedMessage.channel!,
          thread_ts: threadId,
          text: "",
          blocks: firstFinalChunk,
          ...NO_UNFURL_OPTIONS,
        });
        firstChunkSent = true;
      }

      if (firstChunkSent) {
        for (const remainingChunk of remainingFinalChunks) {
          await postThreadChunkMessage(remainingChunk);
        }
        for (const citationChunk of citationBlockChunks) {
          await postThreadChunkMessage(citationChunk);
        }
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
      if (streamTs) {
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

  const resolvedSlackBot = await resolveSlackBotForEvent();
  const hasAgent = Boolean(resolvedSlackBot?.agentId);
  const filesPresent = (typedMessage.files?.length ?? 0) > 0;
  const { supported, unsupported, oversized } = classifySlackFiles(typedMessage.files);

  if (filesPresent && hasAgent && (unsupported.length > 0 || oversized.length > 0)) {
    await postUnsupportedAttachmentsNotice(
      typedClient,
      typedMessage,
      unsupported,
      supported.length > 0,
      oversized,
    );
    if (supported.length === 0) return;
  }

  // Preserve legacy silent-ignore on non-agent path (out of scope to fix here).
  if (filesPresent && !hasAgent && supported.length === 0) return;

  let query = await resolveMentionsInText(typedMessage.text, typedClient);
  if (!query) {
    if (supported.length > 0) query = "See below attached file(s).";
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

  const resolvedSlackBot = await resolveSlackBotForEvent();
  const hasAgent = Boolean(resolvedSlackBot?.agentId);
  const filesPresent = (typedMessage.files?.length ?? 0) > 0;
  const { supported, unsupported, oversized } = classifySlackFiles(typedMessage.files);

  if (filesPresent && hasAgent && (unsupported.length > 0 || oversized.length > 0)) {
    await postUnsupportedAttachmentsNotice(
      typedClient,
      typedMessage,
      unsupported,
      supported.length > 0,
      oversized,
    );
    if (supported.length === 0) return;
  }

  // Preserve legacy silent-ignore on non-agent path (out of scope to fix here).
  if (filesPresent && !hasAgent && supported.length === 0) return;

  let query = await resolveMentionsInText(typedMessage.text, typedClient);
  if (!query) {
    if (supported.length > 0) query = "Attached file(s).";
    else query = "Hi";
  }

  try {
    const contextualQuery = await buildQueryWithThreadContext(
      typedClient,
      typedMessage,
      query,
    );
    await processSlackMessage(
      typedMessage,
      typedClient,
      typedContext,
      contextualQuery,
      resolvedSlackBot,
    );
  } catch (error) {
    console.error("Error handling app mention:", error);
    await sendUserFacingSlackErrorMessage(typedClient, typedMessage, error);
  }
});

(async () => {
  await connect();

  // Drop legacy threadId + botId index if it exists
  await dropLegacyThreadBotIndex();

  await app.start(process.env.SLACK_BOT_PORT || 3020);
  console.log("Bolt app is running on 3020.");
})();
