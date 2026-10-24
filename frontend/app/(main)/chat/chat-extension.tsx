'use client';

/**
 * Chat extension points. `@/config` exports the active `chatExtension` (plain functions) and
 * `chatSlots` (hooks and components); the defaults below are today's chat.
 */

import React, { forwardRef } from 'react';
import type { ComponentType, ForwardRefExoticComponent, ReactNode, RefAttributes, RefObject } from 'react';
import type { ThreadMessageLike } from '@assistant-ui/react';
import type { ShareAdapter, SharedMember } from '@/app/components/share/types';
import { StreamError } from '@/lib/api/stream-errors';
import { useChatStore } from './store';
import { createChatShareAdapter } from './share-adapter';
import type {
  AppliedFilterNode,
  AttachmentRef,
  ChatSlot,
  Conversation,
  ConversationMessage,
  PendingAskUserQuestion,
  QueuedSend,
  ResponseTab,
  SSEConnectedEvent,
  StreamChatRequest,
} from './types';
import type { MessagePair } from './components/message-area/message-pairs';
import type { CitationMaps } from './components/message-area/response-tabs/citations/types';
import type { ComposerInputHandle, ComposerInputProps } from './components/composer/composer-input.types';
import { ComposerField as TextareaComposerField } from './components/composer/composer-field';
import type { MentionNode } from './utils/node-mentions';
import type { QueuedMessage } from './utils/queued-send';
import { useQueuedSends } from './hooks/use-send-when-free';

/** Extra fields stored in a thread row's `metadata.custom`. */
export type RowCustom = Record<string, unknown>;

/** A thread row as `buildMessagePairs` reads it. */
export interface PairMessage {
  readonly id?: string;
  readonly role: string;
  readonly content: unknown;
  readonly metadata?: { custom?: unknown };
}

/** What a list or detail response says about one conversation, beyond the shared fields. */
export interface ConversationSummaryInput {
  access?: { isOwner: boolean; accessLevel?: string };
}

export interface HistoryLoadOptions {
  /** Revision of the conversation the rows were read at. */
  rev?: number;
}

/** State for one send, created by `stream.begin`. Absent members keep today's behaviour. */
export interface ChatStreamSession {
  /** Merged into the optimistic user row's `metadata.custom`. */
  userRowCustom?: RowCustom;
  /** Merged into the placeholder assistant row's `metadata.custom`. */
  assistantRowCustom?: RowCustom;
  /** Slot fields set when the send starts. */
  startFields?: Partial<ChatSlot>;
  /** The run id the server sends (header or first frame) replaces the client's. */
  adoptServerRunId?: boolean;
  /** Messages to show once the run completed; `null` keeps the server's list. */
  completeMessages?(
    slotBefore: ChatSlot | undefined,
    finalMessages: ThreadMessageLike[],
    ctx: { pendingAssistantId: string },
  ): ThreadMessageLike[] | null;
  /** The server refused the send; `true` when this session put the slot back in shape itself. */
  onRejected?(
    slotId: string,
    error: unknown,
    ctx: { baseMessages: ThreadMessageLike[]; query: string; request: StreamChatRequest },
  ): boolean;
  /** An answer to a question card failed. */
  onResumeFailed?(slotId: string, error: unknown): void;
  /** The run completed in `convId`. */
  onCompleted?(convId: string): void;
}

export interface ChatSendContext {
  slotId: string;
  slot: ChatSlot;
  query: string;
  attachments?: AttachmentRef[];
  message: ThreadMessageLike;
}

/** The questions queue (`utils/queued-send.ts`): what waits, what goes with it, and when it may go. */
export interface ChatQueueExtension {
  /**
   * A send to this slot waits in its queue instead of going out now; `queued`: questions already wait there. The
   * default queues behind them, and while this tab's answer streams.
   */
  shouldQueue(slot: ChatSlot, queued: boolean): boolean;
  /** The first queued question may not go yet, although nothing runs in the slot. */
  isHeld(slot: ChatSlot): boolean;
  /** A new slot for the chat takes back what this tab had queued in it before a reload. */
  restoresKept(slot: ChatSlot): boolean;
  /** Fields a queued question keeps from its send, besides its text and files. */
  itemFields(message: QueuedMessage): Partial<QueuedSend>;
  /** Request fields of a queued question as it goes out. */
  requestFields(item: QueuedSend): Partial<StreamChatRequest>;
  /** The slot whose queue is listed and capped with this slot's; `null` when there is none. */
  pairedSlotId(slots: Readonly<Record<string, ChatSlot>>, slotId: string): string | null;
  /**
   * The reason a queued question the server refused waits with, first in its queue, until it is retried, edited or
   * removed; `null` shows the error in the thread, as for any send.
   */
  refusedReason(error: unknown): string | null;
}

export interface ChatExtension {
  /** Title as shown in lists and headers. */
  formatConversationTitle(title: string | undefined): string | undefined;
  /** Fields a list or detail row adds to the mapped conversation. */
  conversationFields(
    /** A list row (API or project list). */
    row: object,
    opts?: { archived?: boolean; /** The row's `access` decides `isOwner`. */ ownerFromAccess?: boolean },
  ): Partial<Conversation>;
  /** Fields the agent stream body forwards from the request. */
  agentStreamBodyFields(request: StreamChatRequest): Record<string, unknown>;
  /** Base path of a conversation's routes; its detail, stream, regenerate and cancel calls build on it. */
  conversationPath(conversationId: string, agentId?: string | null): string;
  slot: {
    /** Fields of a fresh slot. */
    defaults(isNew: boolean): Partial<ChatSlot>;
    /**
     * Fields of a chat the user just created, as its first run ends; `slot` is the slot just before, which may already
     * hold what the page learned while the run went on (the chat was shared from the header meanwhile).
     */
    ownerFields(slot?: ChatSlot): Partial<ChatSlot>;
    /** The slot stays on screen while active but is never handed out again for its conversation. */
    isRetired(slot: ChatSlot): boolean;
    /** Shown beside the active slot: never evicted, and streamed at the active slot's rate. */
    isOnScreen(slot: ChatSlot, activeSlotId: string | null): boolean;
  };
  send: {
    /** `true` when the send was taken over (held for later); nothing is streamed now. */
    hold(ctx: ChatSendContext): boolean;
    /** Request fields for a composer send. */
    requestFields(slot: ChatSlot, message: ThreadMessageLike, ctx: { isAgent: boolean }): Partial<StreamChatRequest>;
    /** Request fields for a new message appended to the thread. */
    newMessageFields(request: StreamChatRequest, message: ThreadMessageLike): Partial<StreamChatRequest>;
    /** `metadata.custom` of a message another page's composer handed to the chat page (`PendingChatContext.extra`). */
    handoffCustom(extra: ComposerSendExtra | undefined): RowCustom;
  };
  history: {
    /** Options for the detail fetch. */
    loadOptions(): { quietWhenGone?: boolean };
    /** Slot fields from a loaded detail response. */
    detailFields(conversation: ConversationSummaryInput): Partial<ChatSlot>;
    /** Slot fields after a failed load; `null` when the failure is an ordinary error. */
    loadFailureFields(error: unknown): Partial<ChatSlot> | null;
    /** Rows before they are mapped. */
    prepare(messages: ConversationMessage[]): ConversationMessage[];
    /** Extra `metadata.custom` fields of a mapped row. */
    rowCustom(msg: ConversationMessage, opts: HistoryLoadOptions): RowCustom;
    /**
     * The `rowCustom` fields a card's row takes when the reply to its answer is folded onto it (`answer`, of that
     * reply); `card` is the row's own. The row keeps the card's place in the thread.
     */
    foldedRowCustom(answer: RowCustom, card: RowCustom): RowCustom;
    /** A tool row that adds fields to its turn's answer; `null` when it is not one. */
    turnCustomFromToolRow(msg: ConversationMessage): RowCustom | null;
    /** A row type the base mapping does not know: the row to show, `null` to drop it, `undefined` when not handled. */
    mapRow(msg: ConversationMessage, opts: HistoryLoadOptions): ThreadMessageLike | null | undefined;
    /** Extra fields of an unanswered question card. */
    askCardFields(
      msg: ConversationMessage,
      ctx: { messages: ConversationMessage[]; index: number; cardRowId?: string },
    ): Partial<PendingAskUserQuestion>;
    /** The viewer can answer this question card; one they can answer stays open when a newer one they can't follows. */
    cardIsAnswerable(card: Partial<PendingAskUserQuestion>): boolean;
    /** A card answer row (`User selections:`) answers this card; it closes only the card it answers. */
    answersCard(answer: ConversationMessage, card: Partial<PendingAskUserQuestion>): boolean;
    /** A new question closes this open card: its person asked something else instead of answering it. */
    questionClosesCard(question: ConversationMessage, card: Partial<PendingAskUserQuestion>): boolean;
    /**
     * The composer may take its filters back from this question: a stored row, or a thread row's `metadata.custom`.
     * The latest question with filters that it allows wins. `isOwner`: the viewer owns the conversation.
     */
    restoresFiltersOf(question: ConversationMessage | RowCustom, conversation: { isOwner: boolean }): boolean;
  };
  stream: {
    /** Per-send state; `null` sends as today. */
    begin(slotId: string, slot: ChatSlot, request: StreamChatRequest): ChatStreamSession | null;
    /** Slot fields reset when any run starts. */
    startFields(): Partial<ChatSlot>;
    /** An AG-UI `CUSTOM` event the shared handler does not know. */
    onCustomEvent(slotId: string, name: string, value: unknown): void;
    /** The first frame says no answer follows. */
    skipsAnswer(data: SSEConnectedEvent | undefined): boolean;
    /** Adjusts the completed thread rows in place. */
    finalizeMessages(messages: ThreadMessageLike[], request: StreamChatRequest, convKey: string): void;
    /** Any run completed. */
    onCompleted(request: StreamChatRequest): void;
    /** Toast text for a failed answer to a question card; `null` for the default text. */
    resumeFailedMessage(error: unknown): string | null;
  };
  queue: ChatQueueExtension;
  pairs: {
    /** A row that is not part of a question/answer pair and does not break one. */
    isStandalone(msg: PairMessage): boolean;
    /** The pair drawn for a standalone row. */
    standalonePair(msg: PairMessage, index: number, ctx: { emptyCitationMaps: CitationMaps }): MessagePair | null;
    /** Adds fields to a built pair. */
    decorate(
      pair: MessagePair,
      ctx: { user?: RowCustom; assistant?: RowCustom; standaloneBefore: number },
    ): MessagePair;
  };
  question: {
    truncate(text: string, limit: number): string;
    /** Text that goes to the clipboard. */
    toPlainText(text: string): string;
  };
  answer: {
    /**
     * The stored answer as the markdown renderer gets it. Text the extension draws itself is wrapped in
     * `ANSWER_INLINE_MARK` pairs, which reach `AnswerInlineText` in place of plain text.
     */
    toMarkdown(text: string): string;
    /** The stored answer as it goes to the clipboard, before markdown is stripped. */
    toPlainText(text: string): string;
  };
}

/** Delimits an answer's inline text the extension draws (`answer.toMarkdown`); a private-use character, never typed. */
export const ANSWER_INLINE_MARK = '';

const NO_FIELDS = Object.freeze({}) as Record<string, never>;

export const defaultChatExtension: ChatExtension = {
  formatConversationTitle: (title) => title,
  conversationFields: () => NO_FIELDS,
  agentStreamBodyFields: () => NO_FIELDS,
  conversationPath: (conversationId, agentId) =>
    agentId ? `/api/v1/agents/${agentId}/conversations/${conversationId}` : `/api/v1/conversations/${conversationId}`,
  slot: {
    defaults: () => NO_FIELDS,
    ownerFields: () => NO_FIELDS,
    isRetired: () => false,
    isOnScreen: () => false,
  },
  send: {
    hold: () => false,
    requestFields: () => NO_FIELDS,
    newMessageFields: () => NO_FIELDS,
    handoffCustom: () => NO_FIELDS,
  },
  history: {
    loadOptions: () => NO_FIELDS,
    detailFields: () => NO_FIELDS,
    loadFailureFields: () => null,
    prepare: (messages) => messages,
    rowCustom: () => NO_FIELDS,
    foldedRowCustom: (answer) => answer,
    turnCustomFromToolRow: () => null,
    mapRow: () => undefined,
    askCardFields: () => NO_FIELDS,
    cardIsAnswerable: () => true,
    answersCard: () => true,
    questionClosesCard: () => false,
    restoresFiltersOf: () => true,
  },
  stream: {
    begin: () => null,
    startFields: () => NO_FIELDS,
    onCustomEvent: () => {},
    skipsAnswer: () => false,
    finalizeMessages: () => {},
    onCompleted: () => {},
    resumeFailedMessage: () => null,
  },
  // A chat shared with you is read-only (no box, `useOwnerAccess`): nothing is queued there, nor taken back.
  queue: {
    shouldQueue: (slot, queued) => (queued || (slot.isStreaming && !slot.stopping)) && slot.isOwner !== false,
    isHeld: () => false,
    restoresKept: (slot) => slot.isOwner !== false,
    itemFields: () => NO_FIELDS,
    requestFields: () => NO_FIELDS,
    pairedSlotId: () => null,
    // Refused before the answer began (the request came back with an HTTP error): nothing was answered, so it waits.
    refusedReason: (error) => (error instanceof StreamError && error.status !== undefined ? error.message : null),
  },
  pairs: {
    isStandalone: () => false,
    standalonePair: () => null,
    decorate: (pair) => pair,
  },
  question: {
    truncate: (text, limit) => text.slice(0, limit),
    toPlainText: (text) => text,
  },
  answer: {
    toMarkdown: (text) => text,
    toPlainText: (text) => text,
  },
};

// ── Slots: hooks and components ─────────────────────────────────────

export interface ChatAccess {
  canShare: boolean;
  showComposer: boolean;
  /** Agent threads offer Share too. */
  shareAgentChats: boolean;
  /** `MessageList` draws the chat without `renderDefault`, so the composer keeps a row of its own under the list. */
  ownMessageList?: boolean;
}

export interface ChatHeaderOptions {
  slotId: string | null;
  conversationId: string | null;
  /** Agent of the open thread (history and share routes). */
  threadAgentId: string | null;
  /** `?agentId=` of the page. */
  pageAgentId: string | null;
  showConversationShare: boolean;
  openShare(): void;
}

export interface ChatHeaderExtension {
  /** The header group around the Share control (`null` when Share is not offered); the page positions it. */
  render(shareGroup: ReactNode): ReactNode;
  /** Total people in the chat, shown next to Share. */
  peopleCount?: number;
  /** Search text the share sidebar opens with. */
  shareInitialSearch?: string;
  onShareOpenChange?(open: boolean): void;
  /** Members after a successful share. */
  onShareMembers?(members: SharedMember[]): void;
  /** Other sidebars and dialogs of the page. */
  sidebars?: ReactNode;
}

/** What a composer send carries besides its text and attachments. */
export interface ComposerSendExtra {
  [key: string]: unknown;
}

export interface ComposerSendOptions {
  isAgentChat: boolean;
  agentId: string | null;
}

export interface ComposerSendExtension {
  placeholder?: string;
  /** Sending stays open while the chat's answer streams (no Stop in its place); `intercept` must then take those sends. */
  sendDuringRun?: boolean;
  /**
   * A send made while the chat's answer streams, or behind questions already queued, joins the chat's queue. Defaults
   * to true; `false` keeps the box closed while the answer streams (Stop in place of Send).
   */
  queueSends?: boolean;
  /** Rendered above the composer. */
  accessories: ReactNode;
  /**
   * `false`, or a promise of `true` when the send was handled here and must not reach the thread. `'keep'` handles it
   * too but leaves the message in the composer, for a send the extension did not make (a cancelled confirm).
   */
  intercept(
    message: string,
    attachments: AttachmentRef[] | undefined,
    extra: ComposerSendExtra | undefined,
    /** Knowledge nodes the message names with `@`. */
    mentions?: MentionNode[],
  ): false | Promise<boolean | 'keep'>;
  beforeAppend(extra: ComposerSendExtra | undefined): void;
  /** Extra `metadata.custom` fields of the appended user row. */
  messageCustom(extra: ComposerSendExtra | undefined): RowCustom;
  afterAppend(): void;
}

export interface ComposerInputOptions {
  composerRef: RefObject<ComposerInputHandle | null>;
  message: string;
  setMessage(text: string): void;
  slotId: string | null;
  /** Files in the composer that a send would carry: uploaded or still uploading, not failed. */
  attachmentCount?: number;
}

export interface ComposerInputExtension {
  /** Use the rich editor. */
  rich: boolean;
  /** The text to send, or `null` to hold the send (`resubmit` sends again later). */
  resolveSend(message: string, resubmit: () => void): { text: string; extra?: ComposerSendExtra } | null;
  /** Rendered under the composer field. */
  overlay: ReactNode;
}

export interface ComposerFieldProps extends ComposerInputProps {
  rich: boolean;
  /** Dimmed text, used while the composer sits underneath an open overlay panel. */
  muted?: boolean;
  /** The container owns this paste (attachment and pasted-text chips). */
  shouldDeferPaste?(data: DataTransfer | null): boolean;
}

/** Row layout passed by a list body to `ChatResponse`. */
export interface ChatResponseLayout {
  [key: string]: unknown;
}

export interface MessageListBodyProps {
  pairs: MessagePair[];
  isStreaming: boolean;
  renderDefault(): ReactNode;
  renderResponse(pair: MessagePair, isLast: boolean, layout?: ChatResponseLayout): ReactNode;
  setMessageRef(key: string, el: HTMLDivElement | null): void;
}

export interface ChatResponseInput {
  pair?: MessagePair;
  layout?: ChatResponseLayout;
  question: string;
  isStreaming: boolean;
  isLastMessage: boolean;
  unanswered: boolean;
}

/** Pieces of a rendered answer, for a row layout that draws them itself. */
export interface ChatResponseParts {
  activeTab: ResponseTab;
  setActiveTab(tab: ResponseTab): void;
  sourcesCount: number;
  citationCount: number;
  /** The question card owns the row (live or stored). */
  askCardOwnsRow: boolean;
  renderTabContent(opts?: { padding?: string }): ReactNode;
  renderActions(opts?: { compact?: boolean }): ReactNode;
  filtersAndAttachments: ReactNode;
  previewDialog: ReactNode;
  withRowKey(node: ReactNode): ReactNode;
  onEditQuery(): void;
  /** Knowledge nodes the question names with `@`, drawn as chips in it. */
  questionMentions: AppliedFilterNode[];
}

export interface ChatResponseExtension {
  hideTabs: boolean;
  hideConfidence: boolean;
  /** Draw the run's activity in a bordered card above the confidence, with no separator below it. */
  activityCard?: boolean;
  /**
   * Set the question as a headline under a byline: no space above it, sized by its length (18 px when `compact`),
   * 24 px down to what follows, and 8 px from the confidence to the answer.
   */
  headline?: { compact: boolean };
  allowRegenerate: boolean;
  /** Offer Edit on the question. Defaults to true. */
  allowEditQuery?: boolean;
  /** Offer Helpful and Not helpful on the answer. Defaults to true. */
  allowFeedback?: boolean;
  /** Show the applied-filter chips under the question. Defaults to true. */
  showAppliedFilters?: boolean;
  /** Replaces the time line under the question. */
  questionMeta?: ReactNode;
  /** Above the tabs. */
  beforeTabs?: ReactNode;
  /** In the answer tab, after the answer. */
  answerExtras?: ReactNode;
  /** After the tab content. */
  afterContent?(activeTab: ResponseTab): ReactNode;
  /** After the answer's own actions, before its model details. */
  leadingActions?: ReactNode;
  /** The live question card is read-only, waiting for this person. */
  askCardReadOnlyFor?: { name: string | null };
  /** The live question card can be filled in but not sent yet; the text says why. */
  askCardSubmitHeld?: string;
  /**
   * Someone else asked this question: the viewer sees which tools the agent could not use, but cannot connect them
   * for that turn. Unset, the viewer asked it.
   */
  toolsConnectReadOnlyFor?: { agentName: string | null };
  /** Who answers the approval card instead of the conversation's owner: this person, or the viewer when `null`. */
  approvalCardReadOnlyFor?: { name: string | null } | null;
  /** Adjusts the request that answers a live question card. */
  decorateResumeRequest?(request: StreamChatRequest, pending: PendingAskUserQuestion): void;
  /** A whole row layout; `null` draws the default one. */
  renderRow?(parts: ChatResponseParts): ReactNode | null;
}

export interface ChatRowOptions {
  conversation: Conversation;
  agentId?: string;
  ownerMenu: ReactNode;
  isHovered: boolean;
  onMenuOpenChange(open: boolean): void;
  applyProjectChange(projectId: string | null): Promise<void>;
  isMutationBlocked(): boolean;
}

export interface ChatRowExtension {
  rightSlot: ReactNode;
  dialogs: ReactNode;
  /** `true` when the move was taken over (e.g. previewed first). */
  interceptProjectChange(projectId: string | null): boolean;
}

/** How the queue list above the composer draws its rows. */
export interface QueueListExtension {
  /** Replaces the list's hint; `null` keeps it. */
  hint: string | null;
  /** A queued question as its row shows it. */
  rowText(item: QueuedSend): string;
  /**
   * What the row shows after its text. `files` is the row's files tag (`null` without files); `paired`: the question
   * waits in the paired slot's queue.
   */
  rowTags(item: QueuedSend, opts: { paired: boolean; files: ReactNode }): ReactNode;
}

export interface ChatSlots {
  useChatAccess(slotId: string | null): ChatAccess;
  usePageEffects(slotId: string | null): void;
  useConversationShareAdapter(conversationId: string | null, agentId: string | null): ShareAdapter | null;
  useChatHeader(opts: ChatHeaderOptions): ChatHeaderExtension;
  /** Replaces the message list of a loaded, empty chat. */
  useEmptyState(opts: { slotId: string | null; onPick(text: string): void }): ReactNode | null;
  /** Above the composer, for as long as the chat is open. It hosts the queue's sending (`useQueuedSends`). */
  ComposerBanners: ComponentType<{ slotId: string | null; hasMessages: boolean }>;
  useComposerSend(opts: ComposerSendOptions): ComposerSendExtension;
  useQueueList(opts: { slotId: string | null; pairedSlotId: string | null }): QueueListExtension;
  useComposerInput(opts: ComposerInputOptions): ComposerInputExtension;
  ComposerField: ForwardRefExoticComponent<ComposerFieldProps & RefAttributes<ComposerInputHandle>>;
  /** The message list of a chat that has messages; `renderDefault` draws the default list. */
  MessageList: ComponentType<{ renderDefault(): ReactNode }>;
  MessageListBody: ComponentType<MessageListBodyProps>;
  /** Beside the chat column on wide screens, while no file preview is open there. */
  SidePanel: ComponentType<{ slotId: string | null }>;
  useResponseExtension(input: ChatResponseInput): ChatResponseExtension;
  QueryText: ComponentType<{ text: string }>;
  /** An answer's text holding `ANSWER_INLINE_MARK` (`answer.toMarkdown`). */
  AnswerInlineText: ComponentType<{ text: string }>;
  /** Conversations a list may show. */
  useVisibleConversations<T extends { id: string }>(rows: T[]): T[];
  /** Conversations of an agent shared with the user, shown in their own section. */
  useAgentSharedConversations(rows: Conversation[]): Conversation[];
  useChatRow(opts: ChatRowOptions): ChatRowExtension;
}

function useOwnerAccess(slotId: string | null): ChatAccess {
  const isOwner = useChatStore((s) => (slotId ? s.slots[slotId]?.isOwner ?? null : null));
  // `null` means "not yet known" (loading): keep the composer to avoid a flash.
  return { canShare: isOwner === true, showComposer: isOwner !== false, shareAgentChats: true };
}

function useLegacyShareAdapter(conversationId: string | null, agentId: string | null): ShareAdapter | null {
  return React.useMemo(
    () => (conversationId ? createChatShareAdapter(conversationId, agentId ? { agentId } : undefined) : null),
    [conversationId, agentId],
  );
}

// The page's header overlay positions the share group; the default adds nothing around it.
const NO_HEADER: ChatHeaderExtension = { render: (shareGroup) => shareGroup };

const NO_COMPOSER_SEND: ComposerSendExtension = {
  accessories: null,
  intercept: () => false,
  beforeAppend: () => {},
  messageCustom: () => NO_FIELDS,
  afterAppend: () => {},
};

const PLAIN_QUEUE_LIST: QueueListExtension = {
  hint: null,
  rowText: (item) => item.query,
  rowTags: (_item, { files }) => files,
};

function QueueBanners({ slotId }: { slotId: string | null }) {
  useQueuedSends(slotId);
  return null;
}

const PLAIN_COMPOSER_INPUT: ComposerInputExtension = {
  rich: false,
  resolveSend: (message) => ({ text: message }),
  overlay: null,
};

const DEFAULT_RESPONSE: ChatResponseExtension = { hideTabs: false, hideConfidence: false, allowRegenerate: true };

const PlainComposerField = forwardRef<ComposerInputHandle, ComposerFieldProps>(function PlainComposerField(props, ref) {
  return <TextareaComposerField ref={ref} {...props} />;
});

function DefaultMessageList({ renderDefault }: { renderDefault(): ReactNode }) {
  return <>{renderDefault()}</>;
}

function DefaultMessageListBody({ renderDefault }: MessageListBodyProps) {
  return <>{renderDefault()}</>;
}

function PlainQueryText({ text }: { text: string }) {
  return <>{text}</>;
}

export const defaultChatSlots: ChatSlots = {
  useChatAccess: useOwnerAccess,
  usePageEffects: () => {},
  useConversationShareAdapter: useLegacyShareAdapter,
  useChatHeader: () => NO_HEADER,
  useEmptyState: () => null,
  ComposerBanners: QueueBanners,
  useComposerSend: () => NO_COMPOSER_SEND,
  useQueueList: () => PLAIN_QUEUE_LIST,
  useComposerInput: () => PLAIN_COMPOSER_INPUT,
  ComposerField: PlainComposerField,
  MessageList: DefaultMessageList,
  MessageListBody: DefaultMessageListBody,
  SidePanel: () => null,
  useResponseExtension: () => DEFAULT_RESPONSE,
  QueryText: PlainQueryText,
  AnswerInlineText: PlainQueryText,
  useVisibleConversations: (rows) => rows,
  useAgentSharedConversations: (rows) => rows,
  useChatRow: ({ ownerMenu }) => ({ rightSlot: ownerMenu, dialogs: null, interceptProjectChange: () => false }),
};
