import {
  AIServiceResponse,
  IConversation,
  IConversationDocument,
  IMessage,
  IMessageDocument,
} from '../types/conversation.interfaces';
import { IAIResponse } from '../types/conversation.interfaces';
import mongoose from 'mongoose';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  BadRequestError,
  InternalServerError,
} from '../../../libs/errors/http.errors';
import { ICitation } from '../schema/citation.schema';

export const buildUserQueryMessage = (query: string): IMessage => ({
  messageType: 'user_query',
  content: query,
  contentFormat: 'MARKDOWN',
  createdAt: Date.now(),
  updatedAt: Date.now(),
});

export const buildAIFailureResponseMessage = (): IMessage => ({
  messageType: 'error',
  content: 'Error Generating Response, Please try again',
  contentFormat: 'MARKDOWN',
  createdAt: Date.now(),
  updatedAt: Date.now(),
});

export const buildAIResponseMessage = (
  aiResponse: AIServiceResponse<IAIResponse>,
  citations: ICitation[] = [],
): IMessage => {
  if (!aiResponse?.data?.answer) {
    throw new InternalServerError('AI response must include an answer');
  }

  return {
    messageType: 'bot_response',
    createdAt: Date.now(),
    updatedAt: Date.now(),
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
  };
};

export const formatPreviousConversations = (messages: IMessage[]) => {
  return messages
    .filter((msg) => msg.messageType !== 'error')
    .map((msg) => ({
      content: msg.content,
      role: msg.messageType,
    }));
};

export const getPaginationParams = (req: AuthenticatedUserRequest) => {
  const page = Math.max(1, parseInt(req.query?.page as string) || 1);
  const limit = Math.max(
    1,
    Math.min(parseInt(req.query?.limit as string) || 20, 100),
  );
  const skip = (page - 1) * limit;
  return { page, limit, skip };
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

export const buildSharedWithMeFilter = (req: AuthenticatedUserRequest) => {
  // Initialize base filter with required fields
  const filter = {
    orgId: new mongoose.Types.ObjectId(`${req.user?.orgId}`),
    isDeleted: false,
    isArchived: false,
    // Only include conversations where:
    // 1. User is not the initiator
    // 2. Either the conversation is explicitly shared with the user
    //    or the conversation is publicly shared
    initiator: { $ne: new mongoose.Types.ObjectId(`${req.user?.userId}`) },
    $or: [
      {
        'sharedWith.userId': new mongoose.Types.ObjectId(`${req.user?.userId}`),
      },
      { isShared: true },
    ],
  };

  return filter;
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

export const buildFilter = (
  req: AuthenticatedUserRequest,
  orgId: string,
  userId: string,
  id?: string, // conversationId or searchId
) => {
  // Initialize base filter with required fields
  const filter: any = {
    orgId: new mongoose.Types.ObjectId(`${orgId}`),
    isDeleted: false,
    isArchived: false,
    $or: [
      { userId: new mongoose.Types.ObjectId(`${userId}`) },
      { 'sharedWith.userId': new mongoose.Types.ObjectId(`${userId}`) },
      { isShared: true },
    ],
  };

  if (id) {
    filter._id = new mongoose.Types.ObjectId(id);
  }

  // Handle search
  if (req.query.search) {
    filter.$and = [
      {
        $or: [
          { title: { $regex: req.query.search, $options: 'i' } },
          { 'messages.content': { $regex: req.query.search, $options: 'i' } },
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

  // Handle shared/private filter
  if (req.query.shared !== undefined) {
    filter.isShared = req.query.shared === 'true';
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
  addFilterIfApplied('page', query.page);
  addFilterIfApplied('limit', query.limit);

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
        current: query.shared || null,
        applied: activeFilters.has('shared'),
      },
      tags: {
        type: 'string',
        description: 'Filter by tags',
        current: query.tags || null,
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
        current: query.search || null,
        applied: activeFilters.has('search'),
      },
      pagination: {
        page: {
          type: 'number',
          current: query.page || 1,
          min: 1,
          max: 1000,
          default: 1,
          description: 'Page number for pagination',
          applied: activeFilters.has('pagination'),
        },
        limit: {
          type: 'number',
          current: query.limit || 20,
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
          current: query.sortBy || 'lastActivityAt',
          applied: activeFilters.has('sorting'),
        },
        sortOrder: {
          values: ['asc', 'desc'],
          default: 'desc',
          description: 'Sort order',
          current: query.sortOrder || 'desc',
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
              query.startDate ||
              null,
            end:
              appliedFilters.createdAt?.$lte?.toISOString() ||
              query.endDate ||
              null,
          },
          applied: activeFilters.has('dateRange'),
        },
      },
      messageFilters: {
        messageType: {
          values: ['user_query', 'bot_response', 'error', 'feedback', 'system'],
          description: 'Filter by message type',
          current: query.messageType || null,
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
      return (a.createdAt || 0) - (b.createdAt || 0);
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
    messages: messages.map((message) => ({
      ...message,
      citations:
        message.citations?.map((citation) => ({
          citationId: citation.citationId?._id,
          citationData: citation.citationId,
        })) || [],
    })),
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
