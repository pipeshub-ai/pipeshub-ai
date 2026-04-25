import {
  AIServiceResponse,
  IAgentConversation,
  IAIModel,
  IConversation,
  IConversationDocument,
  IMessage,
  IMessageCitation,
  IMessageDocument,
} from '../types/conversation.interfaces';
import { IAIResponse } from '../types/conversation.interfaces';
import mongoose, { ClientSession } from 'mongoose';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  BadRequestError,
  InternalServerError,
} from '../../../libs/errors/http.errors';
import Citation, { ICitation } from '../schema/citation.schema';
import { CONVERSATION_STATUS } from '../constants/constants';
import { Logger } from '../../../libs/services/logger.service';
import {
  IAgentConversationDocument,
  AgentConversation,
} from '../schema/agent.conversation.schema';
import { Response } from 'express';
import { Conversation } from '../schema/conversation.schema';
import { safeParsePagination } from '../../../utils/safe-integer';
import {
  sanitizeForResponse,
  validateBooleanParam,
  validateNoXSS,
  validateNoFormatSpecifiers,
} from '../../../utils/xss-sanitization';

const logger = new Logger({
  service: 'enterprise-search',
});

/**
 * Extract model information from request body
 */
export const extractModelInfo = (
  body: any,
  defaultChatMode: string = 'quick',
): IAIModel => {
  // Use modelFriendlyName if provided and not empty, otherwise fallback to modelName for backward compatibility
  const modelFriendlyName = body.modelFriendlyName && body.modelFriendlyName.trim() 
    ? body.modelFriendlyName.trim() 
    : (body.modelName || undefined);
  
  return {
    modelKey: body.modelKey || undefined,
    modelName: body.modelName || undefined,
    modelProvider: body.modelProvider || undefined,
    chatMode: body.chatMode || defaultChatMode,
    modelFriendlyName: modelFriendlyName,
  };
};

export const buildUserQueryMessage = (query: string): IMessage => ({
  messageType: 'user_query',
  content: query,
  contentFormat: 'MARKDOWN',
  createdAt: new Date(),
  updatedAt: new Date(),
});

/**
 * Safely extracts and validates a search parameter from query string
 * Prevents type confusion by ensuring the parameter is a string, not an array
 * @param searchParam - The search parameter from req.query.search
 * @returns A validated string value
 * @throws BadRequestError if the parameter is an array or not a string
 */
function extractSearchParameter(searchParam: unknown): string {
  // First check: reject arrays explicitly
  if (Array.isArray(searchParam)) {
    throw new BadRequestError('Search parameter must be a string, not an array');
  }
  // Second check: ensure it's a string type
  if (typeof searchParam !== 'string') {
    throw new BadRequestError('Search parameter must be a string');
  }
  // Return the validated string
  return searchParam;
}

export const buildAIFailureResponseMessage = (): IMessage => ({
  messageType: 'error',
  content: 'Error Generating Response, Please try again',
  contentFormat: 'MARKDOWN',
  createdAt: new Date(),
  updatedAt: new Date(),
});

/**
 * Attach populated citation documents across ALL messages of a conversation.
 *
 * Earlier implementations built a lookup map from ONLY the newly-created
 * citations for the current response and then applied it to every message in
 * the conversation. That wiped `citationData` for previously-saved assistant
 * messages because their citationIds were not in the new-citations map — which
 * is exactly what caused inline citation chips in earlier answers to collapse
 * to unclickable numbered badges (no filename, no popover) on the client after
 * any follow-up query, only recovering on a full page refresh (the GET
 * `getConversationById` path correctly populates citations).
 *
 * Strategy:
 *   1. Re-fetch the conversation with `populate` on every citationId across
 *      all messages (matches the GET path).
 *   2. For each message citation, if populate resolved to a full Citation
 *      document, use it. Otherwise fall back to the newly-created
 *      `fallbackCitations` array (handles transactional edge cases where the
 *      just-saved citation isn't visible to a follow-up query).
 */
export const attachPopulatedCitations = async <
  T extends IConversation | IAgentConversation,
>(
  conversationId: mongoose.Types.ObjectId | string | undefined,
  updatedConversationObject: T,
  fallbackCitations: ICitation[],
  isAgent: boolean,
  session?: ClientSession | null,
): Promise<T> => {
  let plainConversation: T = updatedConversationObject;

  // Only attempt the populate round-trip when Mongoose is actually connected.
  // In unit tests (and any environment without an active DB connection) the
  // default Mongoose buffering would hang this call for ~10s before failing,
  // which is both slow and unnecessary — the fallback branch below handles
  // those cases using the newly-created citations.
  const isConnected = mongoose.connection?.readyState === 1;

  if (conversationId && isConnected) {
    try {
      const Model = isAgent ? AgentConversation : Conversation;
      const query = (Model as any).findById(conversationId).populate({
        path: 'messages.citations.citationId',
        model: 'citation',
        select: '-__v',
      });
      if (session) {
        query.session(session);
      }
      const populated = await query.lean().exec();
      if (populated) {
        plainConversation = populated as T;
      }
    } catch (err: any) {
      logger.warn(
        'Failed to populate citations for conversation response; falling back to newly-created citations only',
        { conversationId: conversationId?.toString(), error: err?.message },
      );
    }
  }

  return {
    ...plainConversation,
    messages: (plainConversation.messages as IMessage[]).map(
      (message: IMessage) => ({
        ...message,
        citations:
          message.citations?.map((citation: IMessageCitation) => {
            // After populate, `citationId` is the full Citation document;
            // otherwise it's still an ObjectId / string reference. We must
            // explicitly exclude ObjectId here because some bson versions
            // expose inherited properties that make a plain `'_id' in x`
            // check truthy on an ObjectId.
            const populated = citation.citationId as unknown as
              | (ICitation & { _id?: mongoose.Types.ObjectId })
              | mongoose.Types.ObjectId
              | string
              | undefined;
            const isPopulatedCitationDoc =
              !!populated &&
              typeof populated === 'object' &&
              !(populated instanceof mongoose.Types.ObjectId) &&
              (populated as any)._bsontype !== 'ObjectId' &&
              '_id' in populated;
            if (isPopulatedCitationDoc) {
              const doc = populated as ICitation & {
                _id?: mongoose.Types.ObjectId;
              };
              return {
                ...citation,
                citationId: doc._id,
                citationData: doc as ICitation,
              };
            }
            // Fallback to the newly-created citations for this response.
            return {
              ...citation,
              citationData: citation.citationId
                ? fallbackCitations.find(
                    (c: ICitation) =>
                      c._id?.toString() === citation.citationId?.toString(),
                  )
                : undefined,
            };
          }) || [],
      }),
    ),
  } as T;
};

export const buildAIResponseMessage = (
  aiResponse: AIServiceResponse<IAIResponse>,
  citations: ICitation[] = [],
  modelInfo?: IAIModel,
): IMessage => {
  if (!aiResponse?.data?.answer) {
    throw new InternalServerError('AI response must include an answer');
  }

  const message: IMessage = {
    messageType: 'bot_response',
    createdAt: new Date(),
    updatedAt: new Date(),
    content: aiResponse.data.answer,
    contentFormat: 'MARKDOWN',
    citations: citations.map((citation) => ({
      citationId: citation._id as mongoose.Types.ObjectId,
    })),
    confidence: aiResponse.data.confidence,
    followUpQuestions:
      aiResponse.data.followUpQuestions?.map((q) => ({
        question: q.question,
        confidence: q.confidence,
        reasoning: q.reasoning,
      })) || [],
    metadata: {
      processingTimeMs: aiResponse.data.metadata?.processingTimeMs,
      modelVersion: aiResponse.data.metadata?.modelVersion,
      aiTransactionId: aiResponse.data.metadata?.aiTransactionId,
      reason: aiResponse.data?.reason,
    },
    modelInfo: modelInfo,
  };

  // Include referenceData if present (IDs for follow-up queries)
  // This stores technical IDs that were in the response for later reference
  // Filter out invalid items (must have name and at least key or id)
  if (aiResponse.data.referenceData && Array.isArray(aiResponse.data.referenceData)) {
    message.referenceData = aiResponse.data.referenceData.filter((item) => {
      // Ensure item has name and at least one of key or id (id can be optional)
      return item && item.name;
    });
  }

  return message;
};

export const formatPreviousConversations = (messages: IMessage[]) => {
  return messages
    .filter((msg) => msg.messageType !== 'error')
    .map((msg) => ({
      content: msg.content,
      role: msg.messageType,
      // Include referenceData for follow-up queries (IDs from tool responses)
      ...(msg.referenceData && msg.referenceData.length > 0 && { referenceData: msg.referenceData }),
    }));
};

export const getPaginationParams = (req: AuthenticatedUserRequest) => {
  try {
    // Validate and sanitize page and limit parameters for XSS
    
    if (req.query?.page) {
      validateNoXSS(req.query.page as string, 'page parameter');
    }
    if (req.query?.limit) {
      validateNoXSS(req.query.limit as string, 'limit parameter');
    }
    
    return safeParsePagination(
      req.query?.page as string | undefined,
      req.query?.limit as string | undefined,
      1,
      20,
      100,
    );
  } catch (error: any) {
    // Fallback to safe defaults if parsing fails
    return { page: 1, limit: 20, skip: 0 };
  }
};

export const buildSortOptions = (req: AuthenticatedUserRequest) => {
  const allowedSortFields = ['createdAt', 'lastActivityAt', 'title'];
  const sortField = allowedSortFields.includes(req.query?.sortBy as string)
    ? (req.query?.sortBy as string)
    : 'lastActivityAt';

  return {
    [sortField]: req.query.sortOrder === 'asc' ? 1 : -1,
    _id: -1, // Secondary sort for consistency
  };
};

export const addComputedFields = (
  conversation: IConversation | IConversationDocument,
  userId: string,
) => {
  return {
    ...conversation,
    isOwner: conversation.initiator.toString() === userId,
    accessLevel:
      conversation.sharedWith?.find(
        (share) => share.userId.toString() === userId,
      )?.accessLevel || 'read',
  };
};

/**
 * Base access filter for conversations / enterprise searches in list and
 * by-id flows. Matches either:
 * - rows owned by this user (`userId`), or
 * - rows explicitly shared with this user (`isShared` and `sharedWith` contains
 *   their id).
 *
 * The shared branch uses `$and` so `isShared: true` alone does not grant access.
 */
export const buildFilter = (
  req: AuthenticatedUserRequest,
  orgId: string,
  userId: string,
  id?: string, // conversationId or searchId
  owned: boolean = true,
  shared: boolean = true,
) => {
  if (!owned && !shared) {
    throw new BadRequestError('Either owned or shared must be true');
  }
  const filter: any = {
    orgId: new mongoose.Types.ObjectId(`${orgId}`),
    isDeleted: false,
    isArchived: false,
    $or: [
      ...(owned ? [{ userId: new mongoose.Types.ObjectId(`${userId}`) }] : []),
      ...(shared ? [{
        $and: [
          { isShared: true },
          { 'sharedWith.userId': new mongoose.Types.ObjectId(`${userId}`) },
        ],
      }] : [])
    ],
  };

  if (id) {
    filter._id = new mongoose.Types.ObjectId(id);
  }

  // Handle search with XSS validation
  // Use helper function to safely extract and validate search parameter
  if (req.query.search) {
    const searchValue = extractSearchParameter(req.query.search);
    
    // Validate search parameter for XSS
    validateNoXSS(searchValue, 'search parameter');
    
    // Additional validation: limit search length
    if (searchValue.length > 1000) {
      throw new BadRequestError('Search parameter too long (max 1000 characters)');
    }
    
    // Escape special regex characters to prevent regex injection
    const escapedSearch = searchValue.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    
    filter.$and = [
      {
        $or: [
          { title: { $regex: escapedSearch, $options: 'i' } },
          { 'messages.content': { $regex: escapedSearch, $options: 'i' } },
        ],
      },
    ];
  }

  // Handle date range
  if (req.query.startDate || req.query.endDate) {
    filter.createdAt = {};
    if (req.query.startDate) {
      const startDate = new Date(req.query.startDate as string);
      if (isNaN(startDate.getTime())) {
        throw new BadRequestError('Invalid start date format');
      }
      filter.createdAt.$gte = startDate;
    }
    if (req.query.endDate) {
      const endDate = new Date(req.query.endDate as string);
      if (isNaN(endDate.getTime())) {
        throw new BadRequestError('Invalid end date format');
      }
      filter.createdAt.$lte = endDate;
    }
  }

  // Handle shared/private filter with XSS validation
  if (req.query.shared !== undefined) {
    const sharedValue = validateBooleanParam(
      req.query.shared as string,
      'shared parameter',
    );
    if (sharedValue !== undefined) {
      filter.isShared = sharedValue;
    }
  }

  return filter;
};

export const buildPaginationMetadata = (
  totalCount: number,
  page: number,
  limit: number,
) => ({
  page,
  limit,
  totalCount,
  totalPages: Math.ceil(totalCount / limit),
  hasNextPage: page * limit < totalCount,
  hasPrevPage: page > 1,
});

export const buildFiltersMetadata = (
  appliedFilters: any,
  query: any,
  sortOptions?: { field: string; direction: number },
) => {
  const activeFilters = new Set();
  const currentValues: Record<string, any> = {};

  // Helper function to check and add filter
  const addFilterIfApplied = (filterName: string, value: any) => {
    if (value !== undefined && value !== null && value !== '') {
      activeFilters.add(filterName);
      currentValues[filterName] = value;
    }
  };

  // Process common filters
  addFilterIfApplied('search', query.search);
  addFilterIfApplied('shared', query.shared);
  addFilterIfApplied('tags', query.tags);
  addFilterIfApplied('minMessages', query.minMessages);
  addFilterIfApplied('sortBy', query.sortBy);
  addFilterIfApplied('sortOrder', query.sortOrder);
  addFilterIfApplied('startDate', query.startDate);
  addFilterIfApplied('endDate', query.endDate);
  addFilterIfApplied('messageType', query.messageType);

  // Extract and parse query parameters with safe integer validation
  let page: number;
  let limit: number;
  try {
    const pagination = safeParsePagination(
      query.page as string | undefined,
      query.limit as string | undefined,
      1,
      20,
      100,
    );
    page = pagination.page;
    limit = pagination.limit;
  } catch (error: any) {
    throw new BadRequestError(
      error.message || 'Invalid pagination parameters',
    );
  }

  addFilterIfApplied('page', page);
  addFilterIfApplied('limit', limit);

  // Process date filters
  if (appliedFilters.createdAt) {
    activeFilters.add('dateRange');
    currentValues.dateRange = {
      start: appliedFilters.createdAt.$gte?.toISOString(),
      end: appliedFilters.createdAt.$lte?.toISOString(),
    };
  }

  return {
    applied: {
      filters: Array.from(activeFilters),
      values: currentValues,
    },
    available: {
      shared: {
        values: ['true', 'false'],
        description: 'Filter by shared status',
        current:
          typeof query.shared === 'string'
            ? sanitizeForResponse(query.shared)
            : query.shared || null,
        applied: activeFilters.has('shared'),
      },
      tags: {
        type: 'string',
        description: 'Filter by tags',
        current:
          typeof query.tags === 'string'
            ? sanitizeForResponse(query.tags)
            : query.tags || null,
        applied: activeFilters.has('tags'),
      },
      minMessages: {
        type: 'number',
        description: 'Filter by minimum number of messages',
        current: query.minMessages || null,
        applied: activeFilters.has('minMessages'),
      },
      search: {
        type: 'string',
        description: 'Search in conversation title and messages',
        current:
          typeof query.search === 'string'
            ? sanitizeForResponse(query.search)
            : query.search || null,
        applied: activeFilters.has('search'),
      },
      pagination: {
        page: {
          type: 'number',
          current: page || 1,
          min: 1,
          max: 1000,
          default: 1,
          description: 'Page number for pagination',
          applied: activeFilters.has('pagination'),
        },
        limit: {
          type: 'number',
          current: limit || 20,
          min: 1,
          max: 100,
          default: 20,
          description: 'Number of items per page',
          applied: activeFilters.has('pagination'),
        },
      },
      sorting: {
        sortBy: {
          values: [
            'createdAt',
            'lastActivityAt',
            'title',
            'messageType',
            'content',
          ],
          default: 'lastActivityAt',
          description: 'Field to sort by',
          current:
            typeof query.sortBy === 'string'
              ? sanitizeForResponse(query.sortBy)
              : query.sortBy || 'lastActivityAt',
          applied: activeFilters.has('sorting'),
        },
        sortOrder: {
          values: ['asc', 'desc'],
          default: 'desc',
          description: 'Sort order',
          current:
            typeof query.sortOrder === 'string'
              ? sanitizeForResponse(query.sortOrder)
              : query.sortOrder || 'desc',
          applied: activeFilters.has('sorting'),
        },
      },
      dateFilters: {
        dateRange: {
          type: 'date',
          description: 'Filter by creation date range',
          format: 'ISO 8601 (YYYY-MM-DD)',
          current: {
            start:
              appliedFilters.createdAt?.$gte?.toISOString() ||
              (typeof query.startDate === 'string'
                ? sanitizeForResponse(query.startDate)
                : query.startDate) ||
              null,
            end:
              appliedFilters.createdAt?.$lte?.toISOString() ||
              (typeof query.endDate === 'string'
                ? sanitizeForResponse(query.endDate)
                : query.endDate) ||
              null,
          },
          applied: activeFilters.has('dateRange'),
        },
      },
      messageFilters: {
        messageType: {
          values: ['user_query', 'bot_response', 'error', 'feedback', 'system'],
          description: 'Filter by message type',
          current:
            typeof query.messageType === 'string'
              ? sanitizeForResponse(query.messageType)
              : query.messageType || null,
          applied: activeFilters.has('messageType'),
        },
      },
      sortingMessages: {
        sortBy: {
          values: ['createdAt', 'messageType', 'content'],
          default: 'createdAt',
          description: 'Field to sort messages by',
          current: sortOptions?.field || 'createdAt',
        },
        sortOrder: {
          values: ['asc', 'desc'],
          default: 'desc',
          description: 'Sort order for messages',
          current: sortOptions?.direction === 1 ? 'asc' : 'desc',
        },
      },
    },
  };
};

export const sortMessages = (
  messages: IMessageDocument[],
  sortOptions: { field: keyof IMessage },
) => {
  return [...messages].sort((a, b) => {
    if (sortOptions.field === 'createdAt') {
      return (a.createdAt?.getTime() || 0) - (b.createdAt?.getTime() || 0);
    }
    return String(a[sortOptions.field]) > String(b[sortOptions.field]) ? 1 : -1;
  });
};

export const buildMessageFilter = (req: AuthenticatedUserRequest) => {
  const messageFilter: any = {};
  const { startDate, endDate, messageType } = req.query;

  // Add date range filter if provided
  if (startDate || endDate) {
    messageFilter['messages.createdAt'] = {};
    if (startDate) {
      const parsedStartDate = new Date(startDate as string);
      if (isNaN(parsedStartDate.getTime())) {
        throw new BadRequestError('Invalid start date format');
      }
      messageFilter['messages.createdAt'].$gte = parsedStartDate;
    }
    if (endDate) {
      const parsedEndDate = new Date(endDate as string);
      if (isNaN(parsedEndDate.getTime())) {
        throw new BadRequestError('Invalid end date format');
      }
      messageFilter['messages.createdAt'].$lte = parsedEndDate;
    }
  }

  // Add message type filter if provided
  if (messageType) {
    const validTypes = [
      'user_query',
      'bot_response',
      'error',
      'feedback',
      'system',
    ];
    if (!validTypes.includes(messageType as string)) {
      throw new BadRequestError(
        `Invalid message type. Must be one of: ${validTypes.join(', ')}`,
      );
    }
    messageFilter['messages.messageType'] = messageType;
  }

  return messageFilter;
};

export const buildMessageSortOptions = (
  sortBy = 'createdAt',
  sortOrder = 'desc',
) => {
  const allowedSortFields = ['createdAt', 'messageType', 'content'];
  if (!allowedSortFields.includes(sortBy)) {
    throw new BadRequestError(
      `Invalid sort field. Must be one of: ${allowedSortFields.join(', ')}`,
    );
  }

  return {
    field: sortBy,
    direction: sortOrder.toLowerCase() === 'asc' ? 1 : -1,
  };
};

export const buildConversationResponse = (
  conversation: IConversationDocument,
  userId: string,
  pagination: {
    page: number;
    limit: number;
    skip: number;
    totalMessages: number;
    hasNextPage: boolean;
    hasPrevPage: boolean;
  },
  messages: IMessage[],
) => {
  const { page, limit, skip, totalMessages } = pagination;

  // Calculate proper hasNextPage/hasPrevPage based on total message count
  // hasNextPage means there are older messages (lower indices)
  // hasPrevPage means there are newer messages (higher indices)
  const hasNextPage = skip > 0;
  const hasPrevPage = skip + messages.length < totalMessages;

  return {
    id: conversation._id,
    title: conversation.title,
    initiator: conversation.initiator,
    createdAt: conversation.createdAt,
    isShared: conversation.isShared,
    sharedWith: conversation.sharedWith,
    status: conversation.status,
    failReason: conversation.failReason,
    messages: messages.map((message) => ({
      ...message,
      citations:
        message.citations?.map((citation) => ({
          citationId: citation.citationId?._id,
          citationData: citation.citationId,
        })) || [],
    })),
    modelInfo: conversation.modelInfo,
    pagination: {
      page,
      limit,
      totalCount: totalMessages,
      totalPages: Math.ceil(totalMessages / limit),
      hasNextPage,
      hasPrevPage,
      messageRange: {
        start: totalMessages - (skip + messages.length) + 1,
        end: totalMessages - skip,
      },
    },
    access: {
      isOwner: conversation.initiator.toString() === userId,
      accessLevel:
        conversation.sharedWith?.find(
          (share) => share.userId.toString() === userId,
        )?.accessLevel || 'read',
    },
  };
};

// Helper function to save complete conversation
export const saveCompleteConversation = async (
  conversation: IConversationDocument,
  completeData: IAIResponse,
  orgId: string,
  session?: ClientSession | null,
  modelInfo?: IAIModel,
): Promise<any> => {
  try {
    // Save citations first
    const citations = await Promise.all(
      completeData.citations?.map(async (citation: any) => {
        const newCitation = new Citation({
          content: citation.content,
          chunkIndex: citation.chunkIndex,
          citationType: citation.citationType,
          metadata: {
            ...citation.metadata,
            orgId,
          },
        });
        return session ? newCitation.save({ session }) : newCitation.save();
      }) || [],
    );

    // Create AI response message
    const aiResponseMessage = buildAIResponseMessage(
      { data: completeData, statusCode: 200 },
      citations,
      modelInfo,
    ) as IMessageDocument;

    // Update conversation
    conversation.messages.push(aiResponseMessage);
    conversation.modelInfo = modelInfo;
    conversation.lastActivityAt = Date.now();
    conversation.status = CONVERSATION_STATUS.COMPLETE;

    // Save updated conversation
    const updatedConversation = session
      ? await conversation.save({ session })
      : await conversation.save();

    if (!updatedConversation) {
      throw new InternalServerError('Failed to update conversation');
    }

    return attachPopulatedCitations(
      updatedConversation._id as mongoose.Types.ObjectId,
      updatedConversation.toObject() as IConversation,
      citations,
      false,
      session,
    );
  } catch (error: any) {
    logger.error('Error saving complete conversation', {
      conversationId: conversation._id,
      error: error.message,
    });
    throw error;
  }
};

// Helper function to mark conversation as failed
// Helper function to add error to conversation errors array
export const addErrorToConversation = (
  conversation: IConversationDocument | IAgentConversationDocument,
  errorMessage: string,
  errorType?: string,
  messageId?: mongoose.Types.ObjectId,
  stack?: string,
  metadata?: Map<string, any>,
): void => {
  if (!conversation.conversationErrors) {
    conversation.conversationErrors = [];
  }
  conversation.conversationErrors.push({
    message: errorMessage,
    errorType: errorType || 'unknown',
    timestamp: new Date(),
    messageId,
    stack,
    metadata,
  });
};

export const markConversationFailed = async (
  conversation: IConversationDocument,
  failReason: string,
  session?: ClientSession | null,
  errorType?: string,
  stack?: string,
  metadata?: Map<string, any>,
): Promise<void> => {
  try {
    conversation.status = CONVERSATION_STATUS.FAILED;
    conversation.failReason = failReason;
    conversation.lastActivityAt = Date.now();

    // Add error to errors array
    addErrorToConversation(
      conversation,
      failReason,
      errorType,
      undefined,
      stack,
      metadata,
    );

    // Add failure message
    const failedMessage = buildAIFailureResponseMessage() as IMessageDocument;
    // Update the error message content with the exact error
    failedMessage.content = failReason;
    conversation.messages.push(failedMessage);

    // Save failed conversation
    const savedWithError = session
      ? await conversation.save({ session })
      : await conversation.save();

    if (!savedWithError) {
      logger.error('Failed to save conversation error state', {
        conversationId: conversation._id,
        failReason,
      });
    }

    logger.debug('Conversation marked as failed', {
      conversationId: conversation._id,
      failReason,
    });
  } catch (error: any) {
    logger.error('Error marking conversation as failed', {
      conversationId: conversation._id,
      error: error.message,
    });
    throw error;
  }
};

/**
 * Replace a message at a specific index with an error message (used for regeneration)
 */
export const replaceMessageWithError = async (
  conversation: IConversationDocument | IAgentConversationDocument,
  messageIndex: number,
  errorMessage: string,
  session?: ClientSession | null,
  errorType?: string,
  stack?: string,
  metadata?: Map<string, any>,
): Promise<void> => {
  try {
    if (messageIndex < 0 || messageIndex >= conversation.messages.length) {
      throw new InternalServerError(
        'Invalid message index for error replacement',
      );
    }

    conversation.status = CONVERSATION_STATUS.FAILED;
    conversation.failReason = errorMessage;
    conversation.lastActivityAt = Date.now();

    // Add error to errors array
    const originalMessage = conversation.messages[
      messageIndex
    ] as IMessageDocument;
    addErrorToConversation(
      conversation,
      errorMessage,
      errorType,
      originalMessage._id as mongoose.Types.ObjectId,
      stack,
      metadata,
    );

    // Replace the message at the specified index with error message
    const failedMessage = buildAIFailureResponseMessage() as IMessageDocument;
    failedMessage.content = errorMessage;
    // Preserve the original message ID
    failedMessage._id = originalMessage._id;
    conversation.messages[messageIndex] = failedMessage;

    // Save updated conversation
    const savedWithError = session
      ? await conversation.save({ session })
      : await conversation.save();

    if (!savedWithError) {
      logger.error('Failed to replace message with error', {
        conversationId: conversation._id,
        messageIndex,
        errorMessage,
      });
    }

    logger.debug('Message replaced with error', {
      conversationId: conversation._id,
      messageIndex,
      errorMessage,
    });
  } catch (error: any) {
    logger.error('Error replacing message with error', {
      conversationId: conversation._id,
      messageIndex,
      error: error.message,
    });
    throw error;
  }
};

/**
 * Save complete agent conversation data to database
 */
export const saveCompleteAgentConversation = async (
  conversation: IAgentConversationDocument,
  completeData: IAIResponse,
  orgId: string,
  session?: ClientSession | null,
  modelInfo?: IAIModel,
): Promise<any> => {
  try {
    // Save citations first
    const citations = await Promise.all(
      completeData.citations?.map(async (citation: any) => {
        const newCitation = new Citation({
          content: citation.content,
          chunkIndex: citation.chunkIndex,
          citationType: citation.citationType,
          metadata: {
            ...citation.metadata,
            orgId,
          },
        });
        return session ? newCitation.save({ session }) : newCitation.save();
      }) || [],
    );

    // Create AI response message
    const aiResponseMessage = buildAIResponseMessage(
      { data: completeData, statusCode: 200 },
      citations,
      modelInfo,
    ) as IMessageDocument;

    // Update conversation
    conversation.messages.push(aiResponseMessage);
    conversation.modelInfo = modelInfo;
    conversation.lastActivityAt = Date.now();
    conversation.status = CONVERSATION_STATUS.COMPLETE;

    // Save updated conversation
    const updatedConversation = session
      ? await conversation.save({ session })
      : await conversation.save();

    if (!updatedConversation) {
      throw new InternalServerError('Failed to update agent conversation');
    }

    return attachPopulatedCitations(
      updatedConversation._id as mongoose.Types.ObjectId,
      updatedConversation.toObject() as IAgentConversation,
      citations,
      true,
      session,
    );
  } catch (error: any) {
    logger.error('Error saving complete agent conversation', {
      conversationId: conversation._id,
      agentKey: conversation.agentKey,
      error: error.message,
    });
    throw error;
  }
};

/**
 * Mark agent conversation as failed
 */
export const markAgentConversationFailed = async (
  conversation: IAgentConversationDocument,
  failReason: string,
  session?: ClientSession | null,
  errorType?: string,
  stack?: string,
  metadata?: Map<string, any>,
): Promise<void> => {
  try {
    conversation.status = CONVERSATION_STATUS.FAILED;
    conversation.failReason = failReason;
    conversation.lastActivityAt = Date.now();

    addErrorToConversation(
      conversation,
      failReason,
      errorType,
      undefined,
      stack,
      metadata,
    );

    // Add failure message
    const failedMessage = buildAIFailureResponseMessage() as IMessageDocument;
    conversation.messages.push(failedMessage);

    // Save failed conversation
    const savedWithError = session
      ? await conversation.save({ session })
      : await conversation.save();

    if (!savedWithError) {
      logger.error('Failed to save agent conversation error state', {
        conversationId: conversation._id,
        agentKey: conversation.agentKey,
        failReason,
      });
    }

    logger.debug('Agent conversation marked as failed', {
      conversationId: conversation._id,
      agentKey: conversation.agentKey,
      failReason,
    });
  } catch (error: any) {
    logger.error('Failed to mark agent conversation as failed', {
      conversationId: conversation._id,
      agentKey: conversation.agentKey,
      error: error.message,
    });
    throw error;
  }
};

/**
 * Build filter for agent conversations
 */

export const buildAgentConversationFilter = (
  req: any,
  orgId: string,
  userId: string,
  agentKey: string,
  conversationId?: string,
) => {
  const filter: any = {
    agentKey,
    orgId: new mongoose.Types.ObjectId(`${orgId}`),
    $or: [{ userId: new mongoose.Types.ObjectId(`${userId}`) }],
    isDeleted: false,
  };

  if (conversationId) {
    filter._id = new mongoose.Types.ObjectId(`${conversationId}`);
  }

  // Handle search with XSS and format string validation
  // Use helper function to safely extract and validate search parameter
  if (req.query.search) {
    const searchValue = extractSearchParameter(req.query.search);
    
    // Validate search parameter for XSS and format specifiers
    validateNoXSS(searchValue, 'search parameter');
    validateNoFormatSpecifiers(searchValue, 'search parameter');
    
    // Additional validation: limit search length
    if (searchValue.length > 1000) {
      throw new BadRequestError('Search parameter too long (max 1000 characters)');
    }
    
    // Escape special regex characters to prevent regex injection
    const escapedSearch = searchValue.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    
    filter.$and = [
      {
        $or: [
          { title: { $regex: escapedSearch, $options: 'i' } },
          { 'messages.content': { $regex: escapedSearch, $options: 'i' } },
        ],
      },
    ];
  }

  // Handle date range
  if (req.query.startDate || req.query.endDate) {
    filter.createdAt = {};
    if (req.query.startDate) {
      const startDate = new Date(req.query.startDate as string);
      if (isNaN(startDate.getTime())) {
        throw new BadRequestError('Invalid start date format');
      }
      filter.createdAt.$gte = startDate;
    }
    if (req.query.endDate) {
      const endDate = new Date(req.query.endDate as string);
      if (isNaN(endDate.getTime())) {
        throw new BadRequestError('Invalid end date format');
      }
      filter.createdAt.$lte = endDate;
    }
  }

  // Handle shared/private filter with XSS validation
  if (req.query.shared !== undefined) {
    const sharedValue = validateBooleanParam(
      req.query.shared as string,
      'shared parameter',
    );
    if (sharedValue !== undefined) {
      filter.isShared = sharedValue;
    }
  }

  return filter;
};

/**
 * Build shared with me filter for agent conversations
 */
export const buildAgentSharedWithMeFilter = (
  req: any,
  userId: string,
  agentKey: string,
) => {
  const filter: any = {
    agentKey,
    isDeleted: false,
    isShared: true,
    'sharedWith.userId': userId,
  };

  // Add additional filters
  if (req.query.status) {
    filter.status = req.query.status;
  }

  if (req.query.isArchived) {
    filter.isArchived = req.query.isArchived === 'true';
  }

  return filter;
};

/**
 * Add computed fields for agent conversations
 */
export const addAgentConversationComputedFields = (
  conversation: any,
  userId: string,
) => {
  return {
    ...conversation,
    isOwner: conversation.userId?.toString() === userId?.toString(),
    canEdit:
      conversation.userId?.toString() === userId?.toString() ||
      conversation.sharedWith?.some(
        (share: any) =>
          share.userId?.toString() === userId?.toString() &&
          share.accessLevel === 'write',
      ),
    canView: true, // User can view if they got this conversation in results
    messageCount: conversation.messages?.length || 0,
    lastMessage:
      conversation.messages?.length > 0
        ? conversation.messages[conversation.messages.length - 1]
        : null,
  };
};

/**
 * Build sort options for agent conversations
 */
export const buildAgentConversationSortOptions = (req: any) => {
  const { sortBy = 'lastActivityAt', sortOrder = 'desc' } = req.query;

  const sortOptions: any = {};
  sortOptions[sortBy] = sortOrder === 'asc' ? 1 : -1;

  return sortOptions;
};

/**
 * Validate agent conversation access
 */
export const validateAgentConversationAccess = async (
  conversationId: string,
  agentKey: string,
  userId: string,
  orgId: string,
  accessLevel: 'read' | 'write' = 'read',
): Promise<IAgentConversationDocument | null> => {
  try {
    const conversation = await AgentConversation.findOne({
      _id: conversationId,
      agentKey,
      orgId,
      isDeleted: false,
      $or: [
        { userId }, // Owner
        {
          isShared: true,
          'sharedWith.userId': userId,
          ...(accessLevel === 'write' && { 'sharedWith.accessLevel': 'write' }),
        },
      ],
    });

    return conversation;
  } catch (error: any) {
    logger.error('Error validating agent conversation access', {
      conversationId,
      agentKey,
      userId,
      accessLevel,
      error: error.message,
    });
    return null;
  }
};

/**
 * Get agent conversation statistics
 */
export const getAgentConversationStats = async (
  agentKey: string,
  orgId: string,
  userId: string,
) => {
  try {
    const stats = await AgentConversation.aggregate([
      {
        $match: {
          agentKey,
          orgId,
          userId,
          isDeleted: false,
        },
      },
      {
        $group: {
          _id: null,
          totalConversations: { $sum: 1 },
          completedConversations: {
            $sum: { $cond: [{ $eq: ['$status', 'Complete'] }, 1, 0] },
          },
          failedConversations: {
            $sum: { $cond: [{ $eq: ['$status', 'Failed'] }, 1, 0] },
          },
          inProgressConversations: {
            $sum: { $cond: [{ $eq: ['$status', 'Inprogress'] }, 1, 0] },
          },
          totalMessages: { $sum: { $size: '$messages' } },
          avgMessagesPerConversation: { $avg: { $size: '$messages' } },
          lastActivity: { $max: '$lastActivityAt' },
        },
      },
    ]);

    return (
      stats[0] || {
        totalConversations: 0,
        completedConversations: 0,
        failedConversations: 0,
        inProgressConversations: 0,
        totalMessages: 0,
        avgMessagesPerConversation: 0,
        lastActivity: null,
      }
    );
  } catch (error: any) {
    logger.error('Error getting agent conversation stats', {
      agentKey,
      orgId,
      userId,
      error: error.message,
    });
    throw error;
  }
};

/**
 * Search agent conversations
 */
export const searchAgentConversations = async (
  agentKey: string,
  orgId: string,
  userId: string,
  searchQuery: string,
  options: {
    page?: number;
    limit?: number;
    sortBy?: string;
    sortOrder?: 'asc' | 'desc';
  } = {},
) => {
  try {
    const {
      page = 1,
      limit = 20,
      sortBy = 'lastActivityAt',
      sortOrder = 'desc',
    } = options;

    const skip = (page - 1) * limit;
    const sort: any = {};
    sort[sortBy] = sortOrder === 'asc' ? 1 : -1;

    const searchFilter = {
      agentKey,
      orgId,
      userId,
      isDeleted: false,
      $or: [
        { title: { $regex: searchQuery, $options: 'i' } },
        { 'messages.content': { $regex: searchQuery, $options: 'i' } },
      ],
    };

    const [conversations, totalCount] = await Promise.all([
      AgentConversation.find(searchFilter)
        .sort(sort)
        .skip(skip)
        .limit(limit)
        .select('-messages') // Exclude messages for list view
        .lean()
        .exec(),
      AgentConversation.countDocuments(searchFilter),
    ]);

    return {
      conversations: conversations.map((conv) =>
        addAgentConversationComputedFields(conv, userId),
      ),
      pagination: {
        page,
        limit,
        total: totalCount,
        pages: Math.ceil(totalCount / limit),
        hasNextPage: page < Math.ceil(totalCount / limit),
        hasPrevPage: page > 1,
      },
      searchQuery,
    };
  } catch (error: any) {
    logger.error('Error searching agent conversations', {
      agentKey,
      orgId,
      userId,
      searchQuery,
      error: error.message,
    });
    throw error;
  }
};

/**
 * Archive/Unarchive agent conversation
 */
export const toggleAgentConversationArchive = async (
  conversationId: string,
  agentKey: string,
  userId: string,
  orgId: string,
  archive: boolean,
): Promise<IAgentConversationDocument | null> => {
  try {
    const conversation = await validateAgentConversationAccess(
      conversationId,
      agentKey,
      userId,
      orgId,
      'write',
    );

    if (!conversation) {
      return null;
    }

    conversation.isArchived = archive;
    conversation.archivedBy = archive ? (userId as any) : undefined;
    conversation.lastActivityAt = Date.now();

    const updatedConversation = await conversation.save();

    logger.debug(`Agent conversation ${archive ? 'archived' : 'unarchived'}`, {
      conversationId,
      agentKey,
      userId,
      archived: archive,
    });

    return updatedConversation;
  } catch (error: any) {
    logger.error(
      `Error ${archive ? 'archiving' : 'unarchiving'} agent conversation`,
      {
        conversationId,
        agentKey,
        userId,
        error: error.message,
      },
    );
    throw error;
  }
};

/**
 * Delete agent conversation (soft delete)
 */
export const deleteAgentConversation = async (
  conversationId: string,
  agentKey: string,
  userId: string,
  orgId: string,
): Promise<IAgentConversationDocument | null> => {
  try {
    const conversation = await validateAgentConversationAccess(
      conversationId,
      agentKey,
      userId,
      orgId,
      'write',
    );

    if (!conversation) {
      return null;
    }

    conversation.isDeleted = true;
    conversation.deletedBy = userId as any;
    conversation.lastActivityAt = Date.now();

    const updatedConversation = await conversation.save();

    logger.debug('Agent conversation deleted', {
      conversationId,
      agentKey,
      userId,
    });

    return updatedConversation;
  } catch (error: any) {
    logger.error('Error deleting agent conversation', {
      conversationId,
      agentKey,
      userId,
      error: error.message,
    });
    throw error;
  }
};

/**
 * Initialize SSE response headers and send connection event
 */
export const initializeSSEResponse = (res: Response): void => {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'Access-Control-Allow-Origin': '*',
    'X-Accel-Buffering': 'no',
  });

  res.write(
    `event: connected\ndata: ${JSON.stringify({ message: 'SSE connection established' })}\n\n`,
  );
  (res as any).flush?.();
};

/**
 * Send error event to client with optional updated conversation
 */
export const sendSSEErrorEvent = async (
  res: Response,
  errorMessage: string,
  details?: string,
  conversation?: any,
): Promise<void> => {
  const errorData: any = {
    error: errorMessage,
  };

  if (details) {
    errorData.details = details;
  }

  if (conversation) {
    errorData.conversation = conversation;
  }

  res.write(`event: error\ndata: ${JSON.stringify(errorData)}\n\n`);
};

/**
 * Send complete event to client with conversation data
 */
export const sendSSECompleteEvent = (
  res: Response,
  conversation: any,
  recordsUsed: number,
  requestId: string,
  startTime: number,
): void => {
  const responsePayload = {
    conversation,
    recordsUsed,
    meta: {
      requestId,
      timestamp: new Date().toISOString(),
      duration: Date.now() - startTime,
      recordsUsed,
    },
  };

  res.write(`event: complete\ndata: ${JSON.stringify(responsePayload)}\n\n`);
};

/**
 * Handle regeneration stream data events
 */
export const handleRegenerationStreamData = (
  chunk: Buffer,
  buffer: string,
  existingConversation: IConversationDocument | null,
  messageIndex: number,
  session: ClientSession | null,
  requestId: string,
  res: Response,
  onCompleteData: (data: IAIResponse) => void,
): string => {
  const chunkStr = chunk.toString();
  let newBuffer = buffer + chunkStr;

  const events = newBuffer.split('\n\n');
  newBuffer = events.pop() || '';

  let filteredChunk = '';

  for (const event of events) {
    if (event.trim()) {
      const lines = event.split('\n');
      const eventType = lines
        .find((line) => line.startsWith('event:'))
        ?.replace('event:', '')
        .trim();
      const dataLines = lines
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.replace(/^data: ?/, ''));
      const dataLine = dataLines.join('\n');

      if (eventType === 'complete' && dataLine) {
        try {
          const completeData = JSON.parse(dataLine);
          onCompleteData(completeData);
        } catch (parseError: any) {
          logger.error('Failed to parse complete event data', {
            requestId,
            parseError: parseError.message,
            dataLine,
          });
          filteredChunk += event + '\n\n';
        }
      } else if (eventType === 'error' && dataLine) {
        try {
          const errorData = JSON.parse(dataLine);
          if (existingConversation && messageIndex >= 0) {
            const errorMessage =
              errorData.error || errorData.message || 'Unknown error occurred';
            replaceMessageWithError(
              existingConversation,
              messageIndex,
              errorMessage,
              session,
              'streaming_error',
              errorData.stack,
              errorData.metadata
                ? new Map(Object.entries(errorData.metadata))
                : undefined,
            ).catch((err) => {
              logger.error('Failed to replace message with error', {
                requestId,
                error: err.message,
              });
            });
          }
          filteredChunk += event + '\n\n';
        } catch (parseError: any) {
          logger.error('Failed to parse error event data', {
            requestId,
            parseError: parseError.message,
            dataLine,
          });
          if (existingConversation && messageIndex >= 0) {
            const errorMessage = `Failed to parse error event: ${parseError.message}`;
            replaceMessageWithError(
              existingConversation,
              messageIndex,
              errorMessage,
              session,
              'parse_error',
              parseError.stack,
            ).catch((err) => {
              logger.error('Failed to replace message with error', {
                requestId,
                error: err.message,
              });
            });
          }
          filteredChunk += event + '\n\n';
        }
      } else {
        filteredChunk += event + '\n\n';
      }
    }
  }

  if (filteredChunk) {
    res.write(filteredChunk);
    (res as any).flush?.();
  }

  return newBuffer;
};

/**
 * Handle successful regeneration completion
 */
export const handleRegenerationSuccess = async (
  completeData: IAIResponse,
  existingConversation: IConversationDocument | IAgentConversationDocument,
  messageIndex: number,
  orgId: string,
  session: ClientSession | null,
  modelInfo?: IAIModel,
): Promise<{
  conversation: any;
  savedCitations: ICitation[];
}> => {
  // Create and save citations
  const savedCitations: ICitation[] = await Promise.all(
    completeData.citations?.map(async (citation: ICitation) => {
      const newCitation = new Citation({
        content: citation.content,
        chunkIndex: citation.chunkIndex ?? 0,
        citationType: citation.citationType,
        metadata: {
          ...citation.metadata,
          orgId,
        },
      });
      return session ? newCitation.save({ session }) : newCitation.save();
    }) || [],
  );

  // Build AI response message
  const aiResponseMessage = buildAIResponseMessage(
    { statusCode: 200, data: completeData },
    savedCitations,
    modelInfo,
  ) as IMessageDocument;

  // Preserve the original message ID
  const originalMessage = existingConversation.messages[
    messageIndex
  ] as IMessageDocument;
  aiResponseMessage._id = originalMessage._id;

  if (modelInfo) {
    const fieldsToUpdate: Array<keyof IAIModel> = [
      'modelKey',
      'modelName',
      'modelProvider',
      'chatMode',
      'modelFriendlyName',
    ];
    for (const field of fieldsToUpdate) {
      const value = modelInfo[field];
      if (value !== undefined && value !== null) {
        (existingConversation.modelInfo as IAIModel)[field] = value;
      }
    }
  }

  // Update the conversation with the new message at the same index
  existingConversation.messages[messageIndex] = aiResponseMessage;
  existingConversation.lastActivityAt = Date.now();
  existingConversation.status = CONVERSATION_STATUS.COMPLETE;

  // Save the updated conversation
  const updatedConversation = session
    ? await existingConversation.save({ session })
    : await existingConversation.save();

  if (!updatedConversation) {
    throw new InternalServerError(
      'Failed to update conversation with regenerated response',
    );
  }

  // Populate citationData across ALL messages so the frontend can rebuild its
  // citationMaps for the entire conversation. Otherwise previous messages lose
  // inline citation chips (see attachPopulatedCitations docstring).
  const isAgent = (updatedConversation as IAgentConversationDocument)
    .agentKey !== undefined;
  const responseConversation = await attachPopulatedCitations(
    updatedConversation._id as mongoose.Types.ObjectId,
    updatedConversation.toObject() as IConversation | IAgentConversation,
    savedCitations,
    isAgent,
    session,
  );

  return {
    conversation: responseConversation,
    savedCitations,
  };
};

/**
 * Handle regeneration error and send error event
 */
export const handleRegenerationError = async (
  res: Response,
  error: Error | any,
  existingConversation: IConversationDocument | IAgentConversationDocument | null,
  messageIndex: number,
  conversationId: string,
  session: ClientSession | null,
  requestId: string,
  errorType: string = 'regeneration_error',
): Promise<void> => {
  const errorMessage = error.message || 'Unknown error occurred';

  if (existingConversation && messageIndex >= 0) {
    try {
      await replaceMessageWithError(
        existingConversation,
        messageIndex,
        errorMessage,
        session,
        errorType,
        error.stack,
      );

      // Determine the model type from the conversation object itself
      // Check if it's an AgentConversation by looking for agentKey property
      // or by checking the constructor
      const isAgentConversation =
        'agentKey' in existingConversation ||
        existingConversation.constructor === AgentConversation;

      // Reload conversation to get updated state using the appropriate model
      const updatedConversation = isAgentConversation
        ? await AgentConversation.findById(conversationId)
        : await Conversation.findById(conversationId);
      if (updatedConversation) {
        const plainConversation = updatedConversation.toObject();
        await sendSSEErrorEvent(
          res,
          errorMessage,
          error.message,
          plainConversation,
        );
      } else {
        await sendSSEErrorEvent(res, errorMessage, error.message);
      }
    } catch (replaceError: any) {
      logger.error('Failed to replace message with error', {
        requestId,
        error: replaceError.message,
      });
      await sendSSEErrorEvent(res, errorMessage, error.message);
    }
  } else {
    await sendSSEErrorEvent(res, errorMessage, error.message);
  }
};
