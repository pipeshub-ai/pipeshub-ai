import {
  buildAIFailureResponseMessage,
  buildMessageSortOptions,
} from './../utils/utils';
import { Response, NextFunction } from 'express';
import mongoose, { ClientSession } from 'mongoose';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  AIServiceResponse,
  IAIResponse,
  IConversationDocument,
  IMessage,
  IMessageCitation,
  IMessageDocument,
} from '../types/conversation.interfaces';
import { IConversation } from '../types/conversation.interfaces';
import { Conversation } from '../schema/conversation.schema';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import {
  addComputedFields,
  buildAIResponseMessage,
  buildFiltersMetadata,
  buildConversationResponse,
  buildFilter,
  buildMessageFilter,
  buildPaginationMetadata,
  buildSharedWithMeFilter,
  buildSortOptions,
  buildUserQueryMessage,
  formatPreviousConversations,
  getPaginationParams,
  sortMessages,
} from '../utils/utils';
import { IAMServiceCommand } from '../../../libs/commands/iam/iam.service.command';
import EnterpriseSemanticSearch, {
  IEnterpriseSemanticSearch,
} from '../schema/search.schema';
import { AppConfig } from '../../tokens_manager/config/config';
import Citation, {
  AiSearchResponse,
  ICitation,
} from '../schema/citation.schema';
import { CONVERSATION_STATUS } from '../constants/constants';

const logger = Logger.getInstance({ service: 'Enterprise Search Service' });
const rsAvailable = process.env.REPLICA_SET_AVAILABLE === 'true';
const AI_SERVICE_UNAVAILABLE_MESSAGE =
  'AI Service is currently unavailable. Please check your network connection or try again later.';

export const createConversation =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    let session: ClientSession | null = null;
    let responseData: any;

    // Helper function that contains the common conversation operations.
    async function createConversationUtil(
      session?: ClientSession | null,
    ): Promise<any> {
      const userQueryMessage = buildUserQueryMessage(req.body.query);

      const userConversationData: Partial<IConversation> = {
        orgId,
        userId,
        initiator: userId,
        title: req.body.query.slice(0, 100),
        messages: [userQueryMessage] as IMessageDocument[],
        lastActivityAt: Date.now(),
        status: CONVERSATION_STATUS.INPROGRESS,
      };

      const conversation = new Conversation(userConversationData);
      const savedConversation = session
        ? await conversation.save({ session })
        : await conversation.save();
      if (!savedConversation) {
        throw new InternalServerError('Failed to create conversation');
      }

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/chat`,
        method: HttpMethod.POST,
        headers: req.headers as Record<string, string>,
        body: {
          query: req.body.query,
          previousConversations: req.body.previousConversations || [],
          recordIds: req.body.recordIds || [],
          filters: req.body.filters || {},
        },
      };

      logger.debug('Sending query to AI service', {
        requestId,
        query: req.body.query,
        filters: req.body.filters,
      });

      try {
        const aiServiceCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponseData =
          (await aiServiceCommand.execute()) as AIServiceResponse<IAIResponse>;
        if (!aiResponseData?.data || aiResponseData.statusCode !== 200) {
          savedConversation.status = CONVERSATION_STATUS.FAILED;
          savedConversation.failReason = `AI service error: ${aiResponseData?.msg || 'Unknown error'} (Status: ${aiResponseData?.statusCode})`;

          const updatedWithError = session
            ? await savedConversation.save({ session })
            : await savedConversation.save();

          if (!updatedWithError) {
            throw new InternalServerError(
              'Failed to update conversation status',
            );
          }

          throw new InternalServerError(
            'Failed to get AI response',
            aiResponseData?.data,
          );
        }

        const citations = await Promise.all(
          aiResponseData.data?.citations?.map(async (citation: any) => {
            const newCitation = new Citation({
              content: citation.content,
              chunkIndex: citation.chunkIndex,
              citationType: citation.citationType,
              metadata: {
                ...citation.metadata,
                orgId,
              },
            });
            return newCitation.save();
          }) || [],
        );

        // Update the existing conversation with AI response
        const aiResponseMessage = buildAIResponseMessage(
          aiResponseData,
          citations,
        ) as IMessageDocument;
        // Add the AI message to the conversation
        savedConversation.messages.push(aiResponseMessage);
        savedConversation.lastActivityAt = Date.now();
        savedConversation.status = CONVERSATION_STATUS.COMPLETE; // Successful conversation

        const updatedConversation = session
          ? await savedConversation.save({ session })
          : await savedConversation.save();

        if (!updatedConversation) {
          throw new InternalServerError('Failed to update conversation');
        }
        const plainConversation: IConversation = updatedConversation.toObject();
        return {
          conversation: {
            _id: updatedConversation._id,
            ...plainConversation,
            messages: plainConversation.messages.map((message: IMessage) => ({
              ...message,
              citations: message.citations?.map(
                (citation: IMessageCitation) => ({
                  ...citation,
                  citationData: citations.find(
                    (c: ICitation) => c._id === citation.citationId,
                  ),
                }),
              ),
            })),
          },
        };
      } catch (error: any) {
        // TODO: Add support for retry mechanism and generate response from retry
        // and append the response to the correct messageId

        savedConversation.status = CONVERSATION_STATUS.FAILED;
        if (error.cause?.code === 'ECONNREFUSED') {
          savedConversation.failReason = `AI service connection error: ${AI_SERVICE_UNAVAILABLE_MESSAGE}`;
        } else {
          savedConversation.failReason =
            error.message || 'Unknown error occurred';
        }
        // persist and serve the error message to the user.
        const failedMessage =
          buildAIFailureResponseMessage() as IMessageDocument;
        savedConversation.messages.push(failedMessage);
        savedConversation.lastActivityAt = Date.now();

        const savedWithError = session
          ? await savedConversation.save({ session })
          : await savedConversation.save();

        if (!savedWithError) {
          logger.error('Failed to save conversation error state', {
            requestId,
            conversationId: savedConversation._id,
            error: error.message,
          });
        }
        if (error.cause && error.cause.code === 'ECONNREFUSED') {
          throw new InternalServerError(AI_SERVICE_UNAVAILABLE_MESSAGE, error);
        }
        throw error;
      }
    }

    try {
      logger.debug('Creating new conversation', {
        requestId,
        userId,
        query: req.body.query,
        filters: {
          recordIds: req.body.recordIds,
          departments: req.body.departments,
        },
        timestamp: new Date().toISOString(),
      });

      if (rsAvailable) {
        // Start a session and run the operations inside a transaction.
        session = await mongoose.startSession();
        responseData = await session.withTransaction(() =>
          createConversationUtil(session),
        );
      } else {
        // Execute without session/transaction.
        responseData = await createConversationUtil();
      }

      logger.debug('Conversation created successfully', {
        requestId,
        conversationId: responseData.conversation._id,
        duration: Date.now() - startTime,
      });

      res.status(HTTP_STATUS.CREATED).json({
        ...responseData,
        meta: {
          requestId,
          timestamp: new Date().toISOString(),
          duration: Date.now() - startTime,
        },
      });
    } catch (error: any) {
      logger.error('Error creating conversation', {
        requestId,
        message: 'Error creating conversation',
        error: error.message,
        stack: error.stack,
        duration: Date.now() - startTime,
      });

      if (session?.inTransaction()) {
        await session.abortTransaction();
      }
      next(error);
    } finally {
      if (session) {
        session.endSession();
      }
    }
  };

export const addMessage =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    let session: ClientSession | null = null;

    try {
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;

      logger.debug('Adding message to conversation', {
        requestId,
        message: 'Adding message to conversation',
        conversationId: req.params.conversationId,
        query: req.body.query,
        filters: req.body.filters,
        timestamp: new Date().toISOString(),
      });

      // Extract common operations into a helper function.
      async function performAddMessage(session?: ClientSession | null) {
        // Get existing conversation
        const conversation = await Conversation.findOne({
          _id: req.params.conversationId,
          orgId,
          userId,
          isDeleted: false,
        });

        if (!conversation) {
          throw new NotFoundError('Conversation not found');
        }

        // Update status to processing when adding a new message
        conversation.status = CONVERSATION_STATUS.INPROGRESS;
        conversation.failReason = undefined; // Clear previous error if any

        // add previous conversations to the conversation
        // in case of bot_response message
        // Format previous conversations for context
        const previousConversations = formatPreviousConversations(
          conversation.messages,
        );
        logger.debug('Previous conversations', {
          previousConversations,
        });

        const userQueryMessage = buildUserQueryMessage(req.body.query);
        // First, add the user message to the existing conversation
        conversation.messages.push(userQueryMessage as IMessageDocument);
        conversation.lastActivityAt = Date.now();

        // Save the user message to the existing conversation first
        const savedConversation = session
          ? await conversation.save({ session })
          : await conversation.save();

        if (!savedConversation) {
          throw new InternalServerError(
            'Failed to update conversation with user message',
          );
        }
        logger.debug('Sending query to AI service', {
          requestId,
          payload: {
            query: req.body.query,
            previousConversations,
            filters: req.body.filters,
          },
        });

        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/chat`,
          method: HttpMethod.POST,
          headers: req.headers as Record<string, string>,
          body: {
            query: req.body.query,
            previousConversations: previousConversations,
            filters: req.body.filters || {},
          },
        };
        try {
          const aiServiceCommand = new AIServiceCommand(aiCommandOptions);
          let aiResponseData;
          try {
            aiResponseData =
              (await aiServiceCommand.execute()) as AIServiceResponse<IAIResponse>;
          } catch (error: any) {
            // Update conversation status for AI service connection errors
            conversation.status = CONVERSATION_STATUS.FAILED;
            if (error.cause?.code === 'ECONNREFUSED') {
              conversation.failReason = `AI service connection error: ${AI_SERVICE_UNAVAILABLE_MESSAGE}`;
            } else {
              conversation.failReason =
                error.message || 'Unknown connection error';
            }

            const saveErrorStatus = session
              ? await conversation.save({ session })
              : await conversation.save();

            if (!saveErrorStatus) {
              logger.error('Failed to save conversation error status', {
                requestId,
                conversationId: conversation._id,
              });
            }
            if (error.cause && error.cause.code === 'ECONNREFUSED') {
              throw new InternalServerError(
                AI_SERVICE_UNAVAILABLE_MESSAGE,
                error,
              );
            }
            logger.error(' Failed error ', error);
            throw new InternalServerError('Failed to get AI response', error);
          }

          if (!aiResponseData?.data || aiResponseData.statusCode !== 200) {
            // Update conversation status for API errors
            conversation.status = CONVERSATION_STATUS.FAILED;
            conversation.failReason = `AI service API error: ${aiResponseData?.msg || 'Unknown error'} (Status: ${aiResponseData?.statusCode})`;

            const saveApiError = session
              ? await conversation.save({ session })
              : await conversation.save();

            if (!saveApiError) {
              logger.error('Failed to save conversation API error status', {
                requestId,
                conversationId: conversation._id,
              });
            }

            throw new InternalServerError(
              'Failed to get AI response',
              aiResponseData?.data,
            );
          }

          const savedCitations: ICitation[] = await Promise.all(
            aiResponseData.data?.citations?.map(async (citation: any) => {
              const newCitation = new Citation({
                content: citation.content,
                chunkIndex: citation.chunkIndex,
                citationType: citation.citationType,
                metadata: {
                  ...citation.metadata,
                  orgId,
                },
              });
              return newCitation.save();
            }) || [],
          );

          // Update the existing conversation with AI response
          const aiResponseMessage = buildAIResponseMessage(
            aiResponseData,
            savedCitations,
          ) as IMessageDocument;
          // Add the AI message to the existing conversation
          savedConversation.messages.push(aiResponseMessage);
          savedConversation.lastActivityAt = Date.now();
          savedConversation.status = CONVERSATION_STATUS.COMPLETE;

          // Save the updated conversation with AI response
          const updatedConversation = session
            ? await savedConversation.save({ session })
            : await savedConversation.save();

          if (!updatedConversation) {
            throw new InternalServerError(
              'Failed to update conversation with AI response',
            );
          }

          // Return the updated conversation with new messages.
          const plainConversation = updatedConversation.toObject();
          return {
            conversation: {
              ...plainConversation,
              messages: plainConversation.messages.map((message: IMessage) => ({
                ...message,
                citations:
                  message.citations?.map((citation: IMessageCitation) => ({
                    ...citation,
                    citationData: savedCitations.find(
                      (c) =>
                        (c as mongoose.Document).id.toString() ===
                        citation.citationId?.toString(),
                    ),
                  })) || [],
              })),
            },
            recordsUsed: savedCitations.length, // or validated record count if needed
          };
        } catch (error: any) {
          // TODO: Add support for retry mechanism and generate response from retry
          // and append the response to the correct messageId

          // Update conversation status for general errors
          conversation.status = CONVERSATION_STATUS.FAILED;
          conversation.failReason = error.message || 'Unknown error occurred';

          // persist and serve the error message to the user.
          const failedMessage =
            buildAIFailureResponseMessage() as IMessageDocument;
          conversation.messages.push(failedMessage);
          conversation.lastActivityAt = Date.now();
          const saveGeneralError = session
            ? await conversation.save({ session })
            : await conversation.save();

          if (!saveGeneralError) {
            logger.error('Failed to save conversation general error status', {
              requestId,
              conversationId: conversation._id,
            });
          }
          if (error.cause && error.cause.code === 'ECONNREFUSED') {
            throw new InternalServerError(
              AI_SERVICE_UNAVAILABLE_MESSAGE,
              error,
            );
          }
          throw error;
        }
      }

      let responseData;
      if (rsAvailable) {
        session = await mongoose.startSession();
        responseData = await session.withTransaction(() =>
          performAddMessage(session),
        );
      } else {
        responseData = await performAddMessage();
      }

      logger.debug('Message added successfully', {
        requestId,
        message: 'Message added successfully',
        conversationId: req.params.conversationId,
        duration: Date.now() - startTime,
      });

      res.status(HTTP_STATUS.OK).json({
        ...responseData,
        meta: {
          requestId,
          timestamp: new Date().toISOString(),
          duration: Date.now() - startTime,
          recordsUsed: responseData.recordsUsed,
        },
      });
    } catch (error: any) {
      logger.error('Error adding message', {
        requestId,
        message: 'Error adding message',
        conversationId: req.params.conversationId,
        error: error.message,
        stack: error.stack,
        duration: Date.now() - startTime,
      });

      if (session?.inTransaction()) {
        await session.abortTransaction();
      }
      return next(error);
    } finally {
      if (session) {
        session.endSession();
      }
    }
  };

export const getAllConversations = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    const { conversationId } = req.query;
    logger.debug('Fetching conversations', {
      requestId,
      message: 'Fetching conversations',
      userId,
      conversationId: req.query.conversationId,
      query: req.query,
    });

    const { skip, limit, page } = getPaginationParams(req);
    const filter = buildFilter(req, orgId, userId, conversationId as string);
    const sortOptions = buildSortOptions(req);

    // sharedWith Me Conversation
    const sharedWithMeFilter = buildSharedWithMeFilter(req);

    // Execute query
    const [conversations, totalCount, sharedWithMeConversations] =
      await Promise.all([
        Conversation.find(filter)
          .sort(sortOptions as any)
          .skip(skip)
          .limit(limit)
          .select('-__v')
          .select('-messages')
          .lean()
          .exec(),
        Conversation.countDocuments(filter),
        Conversation.find(sharedWithMeFilter)
          .sort(sortOptions as any)
          .skip(skip)
          .limit(limit)
          .select('-__v')
          .select('-messages')
          .select('-sharedWith')
          .lean()
          .exec(),
      ]);

    const processedConversations = conversations.map((conversation: any) => {
      const conversationWithComputedFields = addComputedFields(
        conversation as IConversation,
        userId,
      );
      return {
        ...conversationWithComputedFields,
      };
    });
    const processedSharedWithMeConversations = sharedWithMeConversations.map(
      (sharedWithMeConversation: any) => {
        const conversationWithComputedFields = addComputedFields(
          sharedWithMeConversation as IConversation,
          userId,
        );
        return {
          ...conversationWithComputedFields,
        };
      },
    );

    // Build response metadata
    const response = {
      conversations: processedConversations,
      sharedWithMeConversations: processedSharedWithMeConversations,
      pagination: buildPaginationMetadata(totalCount, page, limit),
      filters: buildFiltersMetadata(filter, req.query),
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Successfully fetched conversations', {
      requestId,
      count: conversations.length,
      totalCount,
      duration: Date.now() - startTime,
    });

    res.status(200).json(response);
  } catch (error: any) {
    logger.error('Error fetching conversations', {
      requestId,
      message: 'Error fetching conversations',
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });
    next(error);
  }
};

export const getConversationById = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  const { conversationId } = req.params;
  try {
    const { sortBy = 'createdAt', sortOrder = 'desc', ...query } = req.query;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Fetching conversation by ID', {
      requestId,
      conversationId,
      userId,
      timestamp: new Date().toISOString(),
    });
    // Get pagination parameters
    const { page, limit } = getPaginationParams(req);

    // Build the base filter with access control
    const baseFilter = buildFilter(req, orgId, userId, conversationId);

    // Build message filter
    const messageFilter = buildMessageFilter(req);

    // Get sort options for messages
    const messageSortOptions = buildMessageSortOptions(
      sortBy as string,
      sortOrder as string,
    );

    const countResult = await Conversation.aggregate([
      { $match: baseFilter },
      { $project: { messageCount: { $size: '$messages' } } },
    ]);

    if (!countResult.length) {
      throw new NotFoundError('Conversation not found');
    }

    const totalMessages = countResult[0].messageCount;

    // Calculate skip and limit for backward pagination
    const skip = Math.max(0, totalMessages - page * limit);
    const effectiveLimit = Math.min(limit, totalMessages - skip);

    // Get conversation with paginated messages
    const conversationWithMessages = await Conversation.findOne(baseFilter)
      .select({
        messages: { $slice: [skip, effectiveLimit] },
        title: 1,
        initiator: 1,
        createdAt: 1,
        isShared: 1,
        sharedWith: 1,
        status: 1, 
        failReason: 1,
      })
      .populate({
        path: 'messages.citations.citationId',
        model: 'citation',
        select: '-__v',
      })
      .lean()
      .exec();

    // Sort messages using existing helper
    const sortedMessages = sortMessages(
      (conversationWithMessages?.messages ||
        []) as unknown as IMessageDocument[],
      messageSortOptions as { field: keyof IMessage },
    );

    // Build conversation response using existing helper
    const conversationResponse = buildConversationResponse(
      conversationWithMessages as unknown as IConversationDocument,
      userId,
      {
        page,
        limit,
        skip,
        totalMessages,
        hasNextPage: skip > 0,
        hasPrevPage: skip + effectiveLimit < totalMessages,
      },
      sortedMessages,
    );

    // Build filters metadata using existing helper
    const filtersMetadata = buildFiltersMetadata(
      messageFilter,
      query,
      messageSortOptions,
    );

    // Prepare response using existing format
    const response = {
      conversation: conversationResponse,
      filters: filtersMetadata,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
        conversationId,
        messageCount: totalMessages,
      },
    };

    logger.debug('Conversation fetched successfully', {
      requestId,
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(200).json(response);
  } catch (error: any) {
    logger.error('Error fetching conversation', {
      requestId,
      conversationId,
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });

    next(error);
  }
};

export const deleteConversationById = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  const { conversationId } = req.params;

  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Attempting to delete conversation', {
      requestId,
      message: 'Attempting to delete conversation',
      conversationId,
      userId,
      timestamp: new Date().toISOString(),
    });

    // Common helper that performs the delete operation.
    async function performDeleteConversation(session?: ClientSession | null) {
      // Get conversation with access control
      const conversation: IConversation | null = await Conversation.findOne({
        _id: conversationId,
        userId,
        orgId,
        isDeleted: false,
        $or: [
          { initiator: userId },
          { 'sharedWith.userId': userId, 'sharedWith.accessLevel': 'write' },
        ],
      });

      if (!conversation) {
        throw new NotFoundError('Conversation not found');
      }

      // Perform soft delete on the conversation
      const updatedConversation = await Conversation.findByIdAndUpdate(
        conversationId,
        {
          $set: {
            isDeleted: true,
            deletedBy: userId,
            lastActivityAt: Date.now(),
          },
        },
        {
          new: true,
          session,
          runValidators: true,
        },
      );

      if (!updatedConversation) {
        throw new InternalServerError('Failed to delete conversation');
      }

      // Extract all citation IDs from messages
      const citationIds = conversation.messages
        .filter((msg: IMessage) => msg.citations && msg.citations.length)
        .flatMap((msg: IMessage) =>
          msg.citations?.map(
            (citation: IMessageCitation) => citation.citationId,
          ),
        );

      // Update all associated citations if any exist
      if (citationIds.length > 0) {
        await Citation.updateMany(
          {
            _id: { $in: citationIds },
            orgId,
            isDeleted: false,
          },
          {
            $set: {
              isDeleted: true,
              deletedBy: userId,
            },
          },
          { session: session || undefined },
        );
      }

      return { updatedConversation, citationIds };
    }

    let result;
    if (rsAvailable) {
      session = await mongoose.startSession();
      session.startTransaction();
      result = await session.withTransaction(() =>
        performDeleteConversation(session),
      );
      await session.commitTransaction();
    } else {
      result = await performDeleteConversation();
    }

    const response = {
      id: conversationId,
      status: 'deleted',
      deletedAt: result.updatedConversation.updatedAt,
      deletedBy: userId,
      citationsDeleted: result.citationIds.length,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Conversation deleted successfully', {
      requestId,
      message: 'Conversation deleted successfully',
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(200).json(response);
  } catch (error: any) {
    logger.error('Error deleting conversation', {
      requestId,
      message: 'Error deleting conversation',
      conversationId,
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });

    if (session?.inTransaction()) {
      await session.abortTransaction();
    }
    next(error);
  } finally {
    if (session) {
      session.endSession();
    }
  }
};

export const shareConversationById =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    let session: ClientSession | null = null;
    const { conversationId } = req.params;
    let { userIds, accessLevel } = req.body;
    try {
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;

      logger.debug('Attempting to share conversation', {
        requestId,
        conversationId,
        userIds,
        accessLevel,
        timestamp: new Date().toISOString(),
      });

      async function performShareConversation(session?: ClientSession | null) {
        // Validate request body
        if (!userIds || !Array.isArray(userIds) || userIds.length === 0) {
          throw new BadRequestError('userIds is required and must be an array');
        }

        if (accessLevel && !['read', 'write'].includes(accessLevel)) {
          throw new BadRequestError(
            "Invalid access level. Must be 'read' or 'write'",
          );
        }

        // Get conversation with access control
        const conversation: IConversation | null = await Conversation.findOne({
          _id: conversationId,
          orgId,
          userId,
          isDeleted: false,
          initiator: userId, // Only initiator can share
        });

        if (!conversation) {
          throw new NotFoundError('Conversation not found or unauthorized');
        }

        // Update object for conversation
        const updateObject: Partial<IConversation> = {
          isShared: true,
        };

        // Handle user-specific sharing
        // Validate all user IDs
        const validUsers = await Promise.all(
          userIds.map(async (id) => {
            if (!mongoose.Types.ObjectId.isValid(id)) {
              throw new BadRequestError(`Invalid user ID format: ${id}`);
            }
            try {
              const iamCommand = new IAMServiceCommand({
                uri: `${appConfig.iamBackend}/api/v1/users/${id}`,
                method: HttpMethod.GET,
                headers: req.headers as Record<string, string>,
              });
              const userResponse = await iamCommand.execute();
              if (userResponse && userResponse.statusCode !== 200) {
                throw new BadRequestError(`User not found: ${id}`);
              }
            } catch (exception) {
              logger.debug(`User does not exist: ${id}`, {
                requestId,
              });
              throw new BadRequestError(`User not found: ${id}`);
            }
            return {
              userId: id,
              accessLevel: accessLevel || 'read',
            };
          }),
        );

        // Get existing shared users
        const existingSharedWith = conversation.sharedWith || [];

        // Create a map of existing users for quick lookup
        const existingUserMap = new Map(
          existingSharedWith.map((share: any) => [
            share.userId.toString(),
            share,
          ]),
        );

        // Merge existing and new users, updating access levels for existing users if they're in the new list
        const mergedSharedWith = [...existingSharedWith];

        for (const newUser of validUsers) {
          const existingUser = existingUserMap.get(newUser.userId.toString());
          if (existingUser) {
            // Update access level if user already exists
            existingUser.accessLevel = newUser.accessLevel;
          } else {
            // Add new user if they don't exist
            mergedSharedWith.push(newUser);
          }
        }

        // Update sharedWith array with merged users
        updateObject.sharedWith = mergedSharedWith;

        // Update the conversation
        const updatedConversation = await Conversation.findByIdAndUpdate(
          conversationId,
          updateObject,
          {
            new: true,
            session,
            runValidators: true,
          },
        );

        if (!updatedConversation) {
          throw new InternalServerError(
            'Failed to update conversation sharing settings',
          );
        }
        return updatedConversation;
      }

      let updatedConversation: IConversationDocument | null = null;
      if (rsAvailable) {
        session = await mongoose.startSession();
        session.startTransaction();
        updatedConversation = await performShareConversation(session);
        await session.commitTransaction();
      } else {
        updatedConversation = await performShareConversation();
      }

      logger.debug('Conversation shared successfully', {
        requestId,
        conversationId,
        duration: Date.now() - startTime,
      });

      // Prepare response
      const response = {
        id: updatedConversation._id,
        isShared: updatedConversation.isShared,
        shareLink: updatedConversation.shareLink,
        sharedWith: updatedConversation.sharedWith,
        meta: {
          requestId,
          timestamp: new Date().toISOString(),
          duration: Date.now() - startTime,
        },
      };

      res.status(200).json(response);
    } catch (error: any) {
      logger.error('Error sharing conversation', {
        requestId,
        message: 'Error sharing conversation',
        conversationId,
        error: error.message,
        stack: error.stack,
        duration: Date.now() - startTime,
      });

      if (session?.inTransaction()) {
        await session.abortTransaction();
      }
      next(error);
    } finally {
      if (session) {
        session.endSession();
      }
    }
  };

export const unshareConversationById = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  const { conversationId } = req.params;
  try {
    const { userIds } = req.body;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Attempting to unshare conversation', {
      requestId,
      conversationId,
      userId,
      timestamp: new Date().toISOString(),
    });

    // Validate request body
    if (!userIds || !Array.isArray(userIds) || userIds.length === 0) {
      throw new BadRequestError('userIds is required and must be an array');
    }

    // Validate all user IDs format
    userIds.forEach((id) => {
      if (!mongoose.Types.ObjectId.isValid(id)) {
        throw new BadRequestError(`Invalid user ID format: ${id}`);
      }
    });

    async function performUnshareConversation(session?: ClientSession | null) {
      // Get conversation with access control
      const conversation = await Conversation.findOne({
        _id: conversationId,
        orgId,
        isDeleted: false,
        initiator: userId, // Only initiator can unshare
      });

      if (!conversation) {
        throw new NotFoundError('Conversation not found or unauthorized');
      }

      // Get existing shared users
      const existingSharedWith = conversation.sharedWith || [];

      // Remove specified users from sharedWith array
      const updatedSharedWith = existingSharedWith.filter(
        (share) => !userIds.includes(share.userId.toString()),
      );

      // Prepare update object
      const updateObject: Partial<IConversation> = {
        sharedWith: updatedSharedWith,
      };

      // If no more shares exist, update isShared and remove shareLink
      if (updatedSharedWith.length === 0) {
        updateObject.isShared = false;
        updateObject.shareLink = undefined;
      }

      // Update the conversation
      const updatedConversation = await Conversation.findByIdAndUpdate(
        conversationId,
        updateObject,
        {
          new: true,
          session,
          runValidators: true,
        },
      );

      if (!updatedConversation) {
        throw new InternalServerError(
          'Failed to update conversation sharing settings',
        );
      }
      return updatedConversation;
    }

    let updatedConversation: IConversationDocument | null = null;
    if (rsAvailable) {
      session = await mongoose.startSession();
      session.startTransaction();
      updatedConversation = await performUnshareConversation(session);
      await session.commitTransaction();
    } else {
      updatedConversation = await performUnshareConversation();
    }

    logger.debug('Conversation unshared successfully', {
      requestId,
      conversationId,
      duration: Date.now() - startTime,
    });

    // Prepare response
    const response = {
      id: updatedConversation._id,
      isShared: updatedConversation.isShared,
      shareLink: updatedConversation.shareLink,
      sharedWith: updatedConversation.sharedWith,
      unsharedUsers: userIds,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    res.status(200).json(response);
  } catch (error: any) {
    logger.error('Error un-sharing conversation', {
      requestId,
      conversationId,
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });

    if (session?.inTransaction()) {
      await session.abortTransaction();
    }
    next(error);
  } finally {
    if (session) {
      session.endSession();
    }
  }
};

export const regenerateAnswers =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    let session: ClientSession | null = null;
    const { conversationId, messageId } = req.params;
    try {
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;

      logger.debug('Attempting to regenerate answers', {
        requestId,
        conversationId,
        messageId,
        timestamp: new Date().toISOString(),
      });

      // Common helper that performs all operations needed to regenerate the answer.
      async function performRegenerateAnswers(session?: ClientSession | null) {
        // Get conversation with access control
        const conversation = await Conversation.findOne({
          _id: conversationId,
          orgId,
          userId,
          isDeleted: false,
          $or: [
            { initiator: userId },
            { 'sharedWith.userId': userId },
            { isShared: true },
          ],
        });

        if (!conversation) {
          throw new NotFoundError('Conversation not found or unauthorized');
        }

        // Ensure there are messages
        if (!conversation.messages || conversation.messages.length === 0) {
          throw new BadRequestError('No messages found in conversation');
        }

        // Get the last message and validate it
        const lastMessage: IMessageDocument = conversation.messages[
          conversation.messages.length - 1
        ] as IMessageDocument;

        if (lastMessage._id?.toString() !== messageId) {
          throw new BadRequestError(
            'Can only regenerate the last message in the conversation',
          );
        }
        if (lastMessage.messageType !== 'bot_response') {
          throw new BadRequestError(
            'Can only regenerate bot response messages',
          );
        }

        // Get user query from the previous message
        if (conversation.messages.length < 2) {
          throw new BadRequestError(
            'No user query found to regenerate response',
          );
        }
        const userQuery = conversation.messages[
          conversation.messages.length - 2
        ] as IMessageDocument;
        if (userQuery.messageType !== 'user_query') {
          throw new BadRequestError('Previous message must be a user query');
        }

        // Format previous conversations up to this message
        const previousConversations = formatPreviousConversations(
          conversation.messages.slice(0, -2), // Exclude last bot response and user query
        );

        // Get new AI response
        const aiCommand = new AIServiceCommand({
          uri: `${appConfig.aiBackend}/api/v1/chat`,
          method: HttpMethod.POST,
          headers: req.headers as Record<string, string>,
          body: {
            query: userQuery.content,
            previousConversations: previousConversations || [],
          },
        });
        let aiResponse;
        try {
          aiResponse =
            (await aiCommand.execute()) as AIServiceResponse<IAIResponse>;
        } catch (error: any) {
          if (error.cause && error.cause.code === 'ECONNREFUSED') {
            throw new InternalServerError(
              AI_SERVICE_UNAVAILABLE_MESSAGE,
              error,
            );
          }
          logger.error(' Failed error ', error);
          throw new InternalServerError('Failed to get AI response', error);
        }
        if (!aiResponse || aiResponse.statusCode !== 200 || !aiResponse.data) {
          throw new InternalServerError(
            'Failed to get response from AI service',
            aiResponse?.data,
          );
        }

        // Create and save citations
        const savedCitations = await Promise.all(
          aiResponse.data.citations.map(async (citation: ICitation) => {
            const newCitation = new Citation({
              content: citation.content,
              chunkIndex: citation.chunkIndex ?? 0,
              citationType: citation.citationType,
              metadata: {
                ...citation.metadata,
                orgId,
              },
            });
            return newCitation.save();
          }),
        );

        // Build new AI message while preserving the original message's _id
        const newMessage = buildAIResponseMessage(
          aiResponse,
          savedCitations,
        ) as IMessageDocument;
        newMessage._id = lastMessage._id;

        // Update conversation with the new message
        const updatedConversation = await Conversation.findOneAndUpdate(
          { _id: conversationId },
          {
            $set: {
              [`messages.${conversation.messages.length - 1}`]: newMessage,
              lastActivityAt: Date.now(),
            },
          },
          {
            new: true,
            session,
            runValidators: true,
          },
        );
        if (!updatedConversation) {
          throw new InternalServerError('Failed to update conversation');
        }

        return {
          conversation: {
            id: updatedConversation._id,
            messages: [
              {
                ...newMessage,
                citations:
                  newMessage.citations?.map((citation: IMessageCitation) => ({
                    citationId: citation.citationId,
                    citationData: savedCitations.find(
                      (c: ICitation) =>
                        (c as mongoose.Document).id.toString() ===
                        citation.citationId?.toString(),
                    ),
                  })) || [],
              },
            ],
          },
        };
      }

      let responseData;
      if (rsAvailable) {
        session = await mongoose.startSession();
        responseData = await session.withTransaction(() =>
          performRegenerateAnswers(session),
        );
      } else {
        responseData = await performRegenerateAnswers();
      }

      if (session && rsAvailable) {
        await session.commitTransaction();
      }

      logger.debug('Answer regenerated successfully', {
        requestId,
        conversationId,
        messageId,
        duration: Date.now() - startTime,
      });

      res.status(200).json({
        ...responseData,
        meta: {
          requestId,
          timestamp: new Date().toISOString(),
          duration: Date.now() - startTime,
        },
      });
    } catch (error: any) {
      logger.error('Error regenerating answer', {
        requestId,
        conversationId,
        messageId,
        error: error.message,
        stack: error.stack,
        duration: Date.now() - startTime,
      });
      if (session?.inTransaction()) {
        await session.abortTransaction();
      }
      next(error);
    } finally {
      if (session) {
        session.endSession();
      }
    }
  };

export const updateTitle = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  try {
    const { conversationId } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Attempting to update conversation title', {
      requestId,
      message: 'Attempting to update conversation title',
      conversationId: req.params.conversationId,
      userId,
      title: req.body.title,
      timestamp: new Date().toISOString(),
    });

    session = await mongoose.startSession();
    session.startTransaction();

    const conversation = await Conversation.findOne({
      _id: conversationId,
      orgId,
      userId,
      isDeleted: false,
    });

    if (!conversation) {
      throw new NotFoundError('Conversation not found');
    }

    conversation.title = req.body.title;
    await conversation.save();

    const response = {
      conversation: {
        ...conversation.toObject(),
        title: conversation.title,
      },
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Conversation title updated successfully', {
      requestId,
      message: 'Conversation title updated successfully',
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json(response);
  } catch (error: any) {
    logger.error('Error updating conversation title', {
      requestId,
      message: 'Error updating conversation title',
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });
    next(error);
  }
};

export const updateFeedback = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  try {
    const { conversationId, messageId } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Attempting to update conversation feedback', {
      requestId,
      message: 'Attempting to update conversation feedback',
      conversationId: req.params.conversationId,
      userId,
      feedback: req.body.feedback,
      timestamp: new Date().toISOString(),
    });

    async function performUpdateFeedback(session?: ClientSession | null) {
      const conversation = await Conversation.findOne({
        _id: conversationId,
        orgId,
        userId,
        isDeleted: false,
        $or: [
          { initiator: userId },
          { 'sharedWith.userId': userId },
          { isShared: true },
        ],
      });

      if (!conversation) {
        throw new NotFoundError('Conversation not found');
      }

      // Find the specific message
      const messageIndex = conversation.messages.findIndex(
        (msg: IMessageDocument) => msg._id?.toString() === messageId,
      );

      if (messageIndex === -1) {
        throw new NotFoundError('Message not found');
      }

      // Check if message is a user query
      if (conversation.messages[messageIndex]?.messageType === 'user_query') {
        throw new BadRequestError('Feedback is not allowed for user queries');
      }

      // Update message feedback
      // Prepare feedback entry
      const feedbackEntry = {
        ...req.body,
        feedbackProvider: userId,
        timestamp: Date.now(),
        metrics: {
          timeToFeedback:
            Date.now() - Number(conversation.messages[messageIndex]?.createdAt),
          userInteractionTime: req.body.metrics?.userInteractionTime,
          feedbackSessionId: req.body.metrics?.feedbackSessionId,
          userAgent: req.headers['user-agent'],
        },
      };

      // Update message feedback
      const updatePath = `messages.${messageIndex}.feedback`;
      const updateObject = {
        $push: { [updatePath]: feedbackEntry },
      };

      // Update conversation
      const updatedConversation = await Conversation.findByIdAndUpdate(
        conversationId,
        updateObject,
        {
          new: true,
          session,
          runValidators: true,
        },
      );

      if (!updatedConversation) {
        throw new InternalServerError('Failed to update feedback');
      }
      return { updatedConversation, feedbackEntry };
    }
    let updatedConversation, feedbackEntry;
    if (rsAvailable) {
      session = await mongoose.startSession();
      ({ updatedConversation, feedbackEntry } = await session.withTransaction(
        () => performUpdateFeedback(session),
      ));
    } else {
      ({ updatedConversation, feedbackEntry } = await performUpdateFeedback());
    }

    logger.debug('Feedback updated successfully', {
      requestId,
      conversationId,
      messageId,
      duration: Date.now() - startTime,
    });

    // Prepare response
    const response = {
      conversationId: updatedConversation._id,
      messageId,
      feedback: feedbackEntry,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    res.status(200).json(response);
  } catch (error: any) {
    logger.error('Error updating conversation feedback', {
      requestId,
      message: 'Error updating conversation feedback',
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });
    next(error);
  }
};

export const archiveConversation = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  try {
    const { conversationId } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Attempting to archive conversation', {
      requestId,
      message: 'Attempting to archive conversation',
      conversationId: req.params.conversationId,
      userId,
      timestamp: new Date().toISOString(),
    });

    async function performArchiveConversation(session?: ClientSession | null) {
      // Get conversation with access control
      const conversation = await Conversation.findOne({
        _id: conversationId,
        userId,
        orgId,
        isDeleted: false,
        $or: [
          { initiator: userId },
          { 'sharedWith.userId': userId, 'sharedWith.accessLevel': 'write' },
        ],
      });

      if (!conversation) {
        throw new NotFoundError(
          'Conversation not found or no archive permission',
        );
      }

      if (conversation.isArchived) {
        throw new BadRequestError('Conversation already archived');
      }

      // Perform soft delete
      const updatedConversation: IConversationDocument | null =
        await Conversation.findByIdAndUpdate(
          conversationId,
          {
            $set: {
              isArchived: true,
              archivedBy: userId,
              lastActivityAt: Date.now(),
            },
          },
          {
            new: true,
            session,
            runValidators: true,
          },
        ).exec();

      if (!updatedConversation) {
        throw new InternalServerError('Failed to archive conversation');
      }

      return updatedConversation;
    }

    let updatedConversation: IConversationDocument | null = null;
    if (rsAvailable) {
      session = await mongoose.startSession();
      session.startTransaction();
      updatedConversation = await performArchiveConversation(session);
      await session.commitTransaction();
    } else {
      await performArchiveConversation();
    }

    // Prepare response
    const response = {
      id: updatedConversation?._id,
      status: 'archived',
      archivedBy: userId,
      archivedAt: updatedConversation?.updatedAt,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Conversation archived successfully', {
      requestId,
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json(response);
  } catch (error: any) {
    logger.error('Error archiving conversation', {
      requestId,
      message: 'Error archiving conversation',
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });
    next(error);
  }
};

export const unarchiveConversation = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  try {
    const { conversationId } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Attempting to unarchive conversation', {
      requestId,
      message: 'Attempting to unarchive conversation',
      conversationId: req.params.conversationId,
      userId,
      timestamp: new Date().toISOString(),
    });

    async function performUnarchiveConversation(
      session?: ClientSession | null,
    ) {
      // Get conversation with access control
      const conversation = await Conversation.findOne({
        _id: conversationId,
        userId,
        orgId,
        isDeleted: false,
        $or: [
          { initiator: userId },
          { 'sharedWith.userId': userId, 'sharedWith.accessLevel': 'write' },
        ],
      });

      if (!conversation) {
        throw new NotFoundError(
          'Conversation not found or no unarchive permission',
        );
      }

      if (!conversation.isArchived) {
        throw new BadRequestError('Conversation is not archived');
      }

      // Perform soft delete
      const updatedConversation: IConversationDocument | null =
        await Conversation.findByIdAndUpdate(
          conversationId,
          {
            $set: {
              isArchived: false,
              archivedBy: null,
              lastActivityAt: Date.now(),
            },
          },
          {
            new: true,
            session,
            runValidators: true,
          },
        ).exec();

      if (!updatedConversation) {
        throw new InternalServerError('Failed to unarchive conversation');
      }
      return updatedConversation;
    }

    let updatedConversation: IConversationDocument | null = null;
    if (rsAvailable) {
      session = await mongoose.startSession();
      session.startTransaction();
      updatedConversation = await performUnarchiveConversation(session);
      await session.commitTransaction();
    } else {
      updatedConversation = await performUnarchiveConversation();
    }

    // Prepare response
    const response = {
      id: updatedConversation?._id,
      status: 'unarchived',
      unarchivedBy: userId,
      unarchivedAt: updatedConversation?.updatedAt,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Conversation un-archived successfully', {
      requestId,
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json(response);
  } catch (error: any) {
    logger.error('Error un-archiving conversation', {
      requestId,
      message: 'Error un-archiving conversation',
      error: error.message,
    });
    next(error);
  }
};

export const listAllArchivesConversation = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    const { conversationId } = req.query;

    logger.debug('Fetching all archived conversations', {
      requestId,
      userId,
    });

    const { skip, limit, page } = getPaginationParams(req);
    const filter = {
      ...buildFilter(req, orgId, userId, conversationId as string),
      isArchived: true, // Add archived filter
      archivedBy: { $exists: true }, // Ensure it was properly archived
    };
    const sortOptions = buildSortOptions(req);

    // Execute query
    const [conversations, totalCount] = await Promise.all([
      Conversation.find(filter)
        .sort(sortOptions as any)
        .skip(skip)
        .limit(limit)
        .select('-__v')
        .select('-citations')
        .lean()
        .exec(),
      Conversation.countDocuments(filter),
    ]);

    // Process results with computed fields and restructured citations
    const processedConversations = conversations.map((conversation: any) => {
      const conversationWithComputedFields = addComputedFields(
        conversation as IConversation,
        userId,
      );

      return {
        ...conversationWithComputedFields,
        archivedAt: conversation.updatedAt,
        archivedBy: conversation.archivedBy,
      };
    });

    // Build response with metadata
    const response = {
      conversations: processedConversations,
      pagination: buildPaginationMetadata(totalCount, page, limit),
      filters: buildFiltersMetadata(filter, req.query),
      summary: {
        totalArchived: totalCount,
        oldestArchive: processedConversations[0]?.archivedAt,
        newestArchive:
          processedConversations[processedConversations.length - 1]?.archivedAt,
      },
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Successfully fetched archived conversations', {
      requestId,
      count: conversations.length,
      totalCount,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json(response);
  } catch (error: any) {
    logger.error('Error fetching archived conversations', {
      requestId,
      message: 'Error fetching archived conversations',
      error: error.message,
    });
    next(error);
  }
};

export const search =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const aiBackendUrl = appConfig.aiBackend;
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;
    try {
      const { query, limit, filters } = req.body;

      logger.debug('Attempting to search', {
        requestId,
        query,
        limit,
        timestamp: new Date().toISOString(),
      });

      const aiCommand = new AIServiceCommand({
        uri: `${aiBackendUrl}/api/v1/search`,
        method: HttpMethod.POST,
        headers: req.headers as Record<string, string>,
        body: { query, limit, filters },
      });

      let aiResponse;
      try {
        aiResponse =
          (await aiCommand.execute()) as AIServiceResponse<AiSearchResponse>;
      } catch (error: any) {
        if (error.cause && error.cause.code === 'ECONNREFUSED') {
          throw new InternalServerError(AI_SERVICE_UNAVAILABLE_MESSAGE, error);
        }
        logger.error(' Failed error ', error);
        throw new InternalServerError(AI_SERVICE_UNAVAILABLE_MESSAGE, error);
      }
      if (!aiResponse || aiResponse.statusCode !== 200 || !aiResponse.data) {
        throw new InternalServerError(
          'Failed to get response from AI service',
          aiResponse?.data,
        );
      }

      const results = aiResponse.data.searchResults;
      let citationIds;
      if (results) {
        // save the citations to the citations collection
        citationIds = await Promise.all(
          results.map(async (result: ICitation) => {
            const citationDoc = new Citation({
              content: result.content,
              chunkIndex: result.chunkIndex ?? 0, // fallback to 0 if not present
              citationType: result.citationType,
              metadata: result.metadata,
            });

            const savedCitation = await citationDoc.save();
            return savedCitation._id;
          }),
        );
      }

      const recordsArray = aiResponse.data?.records || {};
      const recordsMap = new Map();

      if (Array.isArray(recordsArray)) {
        recordsArray.forEach((record) => {
          // Use either _id or _key as the unique key for each record
          const key = record._id || record._key;
          // Convert the record object to a string since your schema expects Map<string, string>
          recordsMap.set(key, JSON.stringify(record));
        });
      }
      // Save the entire search operation as a single document
      const searchRecord = await new EnterpriseSemanticSearch({
        query,
        limit,
        orgId,
        userId,
        citationIds,
        records: recordsMap,
      }).save();

      logger.debug('Saved search operation', {
        requestId,
        searchId: searchRecord._id,
        resultCount: Array.isArray(aiResponse.data)
          ? aiResponse.data.length
          : 1,
      });

      // Return the response
      res.status(HTTP_STATUS.OK).json({
        searchId: searchRecord._id,
        searchResponse: aiResponse.data,
      });
    } catch (error: any) {
      logger.error('Error searching query', {
        requestId,
        message: 'Error searching query',
        error: error.message,
      });
      next(error);
    }
  };

export const searchHistory = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();

  try {
    const { page, limit, skip } = getPaginationParams(req);
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;
    const sortOptions = buildSortOptions(req);
    const filter = buildFilter(req, orgId, userId);

    logger.debug('Attempting to get search history', {
      requestId,
      timestamp: new Date().toISOString(),
    });

    const [searchHistory, totalCount] = await Promise.all([
      EnterpriseSemanticSearch.find(filter)
        .sort(sortOptions as any)
        .skip(skip)
        .limit(limit)
        .exec(),
      EnterpriseSemanticSearch.countDocuments({
        filter,
      }),
    ]);

    const response = {
      searchHistory: searchHistory,
      pagination: buildPaginationMetadata(totalCount, page, limit),
      filters: buildFiltersMetadata(filter, req.query),
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };
    res.status(HTTP_STATUS.OK).json(response);
  } catch (error: any) {
    logger.error('Error searching history', {
      requestId,
      message: 'Error searching history',
      error: error.message,
    });
    next(error);
  }
};

export const getSearchById = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const searchId = req.params.searchId;

  try {
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;
    const filter = buildFilter(req, orgId, userId, searchId);

    logger.debug('Attempting to get search', {
      requestId,
      searchId,
      timestamp: new Date().toISOString(),
    });
    const search = await EnterpriseSemanticSearch.find(filter)
      .populate({
        path: 'citationIds',
        model: 'citation',
        select: '-__v',
      })
      .lean()
      .exec();
    if (!search) {
      throw new NotFoundError('Search Id not found');
    }

    res.status(HTTP_STATUS.OK).json(search);
  } catch (error: any) {
    logger.error('Error getting search by ID', {
      requestId,
      message: 'Error getting search by ID',
      error: error.message,
    });
    next(error);
  }
};

export const deleteSearchById = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const searchId = req.params.searchId;

  try {
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;
    const filter = buildFilter(req, orgId, userId, searchId);

    logger.debug('Attempting to delete search', {
      requestId,
      searchId,
      timestamp: new Date().toISOString(),
    });

    const search = await EnterpriseSemanticSearch.findOneAndDelete(filter);
    if (!search) {
      throw new NotFoundError('Search Id not found');
    }

    // delete related citations
    await Citation.deleteMany({ _id: { $in: search.citationIds } });

    res.status(HTTP_STATUS.OK).json({ message: 'Search deleted successfully' });
  } catch (error: any) {
    logger.error('Error deleting search by ID', {
      requestId,
      message: 'Error deleting search by ID',
      error: error.message,
    });
    next(error);
  }
};

export const shareSearch =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const searchId = req.params.searchId;
    const { userIds, accessLevel } = req.body;

    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      const filter = buildFilter(req, orgId, userId, searchId);

      logger.debug('Attempting to share search', {
        requestId,
        searchId,
        userIds,
        accessLevel,
        timestamp: new Date().toISOString(),
      });

      if (accessLevel && !['read', 'write'].includes(accessLevel)) {
        throw new BadRequestError(
          "Invalid access level. Must be 'read' or 'write'",
        );
      }

      const search: IEnterpriseSemanticSearch | null =
        await EnterpriseSemanticSearch.findOne(filter);
      if (!search) {
        throw new NotFoundError('Search Id not found');
      }

      // Update object for conversation
      const updateObject: Partial<IEnterpriseSemanticSearch> = {
        isShared: true,
      };

      updateObject.shareLink = `${appConfig.frontendUrl}/api/v1/search/${search._id}`;

      // Validate all user IDs
      const validUsers = await Promise.all(
        userIds.map(async (id: string) => {
          if (!mongoose.Types.ObjectId.isValid(id)) {
            throw new BadRequestError(`Invalid user ID format: ${id}`);
          }
          try {
            const iamCommand = new IAMServiceCommand({
              uri: `${appConfig.iamBackend}/api/v1/users/${id}`,
              method: HttpMethod.GET,
              headers: req.headers as Record<string, string>,
            });
            const userResponse = await iamCommand.execute();
            if (userResponse && userResponse.statusCode !== 200) {
              throw new BadRequestError(`User not found: ${id}`);
            }
          } catch (exception) {
            logger.debug(`User does not exist: ${id}`, {
              requestId,
            });
            throw new BadRequestError(`User not found: ${id}`);
          }
          return {
            userId: id,
            accessLevel: accessLevel || 'read',
          };
        }),
      );

      // Get existing shared users
      const existingSharedWith = search.sharedWith || [];

      // Create a map of existing users for quick lookup
      const existingUserMap = new Map(
        existingSharedWith.map((share: any) => [
          share.userId.toString(),
          share,
        ]),
      );

      // Merge existing and new users, updating access levels for existing users if they're in the new list
      const mergedSharedWith = [...existingSharedWith];

      for (const newUser of validUsers) {
        const existingUser = existingUserMap.get(newUser.userId.toString());
        if (existingUser) {
          // Update access level if user already exists
          existingUser.accessLevel = newUser.accessLevel;
        } else {
          // Add new user if they don't exist
          mergedSharedWith.push(newUser);
        }
      }

      // Update sharedWith array with merged users
      updateObject.sharedWith = mergedSharedWith;
      // Update the search
      const updatedSearch = await EnterpriseSemanticSearch.findByIdAndUpdate(
        searchId,
        updateObject,
      );

      if (!updatedSearch) {
        throw new InternalServerError(
          'Failed to update search sharing settings',
        );
      }

      res.status(HTTP_STATUS.OK).json(updatedSearch);
    } catch (error: any) {
      logger.error('Error sharing search', {
        requestId,
        message: 'Error sharing search',
        error: error.message,
      });
      next(error);
    }
  };

export const unshareSearch =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const searchId = req.params.searchId;
    const startTime = Date.now();
    const { userIds } = req.body;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      const filter = buildFilter(req, orgId, userId, searchId);

      logger.debug('Attempting to unshare search', {
        requestId,
        searchId,
        userId,
        timestamp: new Date().toISOString(),
      });

      const search = await EnterpriseSemanticSearch.findOne(filter);
      if (!search) {
        throw new NotFoundError('Search Id not found or unauthorized');
      }

      // Validate all user IDs
      await Promise.all(
        userIds.map(async (id: string) => {
          if (!mongoose.Types.ObjectId.isValid(id)) {
            throw new BadRequestError(`Invalid user ID format: ${id}`);
          }
          try {
            const iamCommand = new IAMServiceCommand({
              uri: `${appConfig.iamBackend}/api/v1/users/${id}`,
              method: HttpMethod.GET,
              headers: req.headers as Record<string, string>,
            });
            const userResponse = await iamCommand.execute();
            if (userResponse && userResponse.statusCode !== 200) {
              throw new BadRequestError(`User not found: ${id}`);
            }
          } catch (exception) {
            logger.debug(`User does not exist: ${id}`, {
              requestId,
            });
            throw new BadRequestError(`User not found: ${id}`);
          }
        }),
      );

      // Get existing shared users
      const existingSharedWith = search.sharedWith || [];

      // Remove specified users from sharedWith array
      const updatedSharedWith = existingSharedWith.filter(
        (share) => !userIds.includes(share.userId.toString()),
      );

      // Prepare update object
      const updateObject: Partial<IEnterpriseSemanticSearch> = {
        sharedWith: updatedSharedWith,
      };

      // If no more shares exist, update isShared and remove shareLink
      if (updatedSharedWith.length === 0) {
        updateObject.isShared = false;
        updateObject.shareLink = undefined;
      }

      const updatedSearch = await EnterpriseSemanticSearch.findByIdAndUpdate(
        searchId,
        updateObject,
      );

      if (!updatedSearch) {
        throw new InternalServerError(
          'Failed to update search sharing settings',
        );
      }

      // Prepare response
      const response = {
        id: updatedSearch._id,
        isShared: updatedSearch.isShared,
        shareLink: updatedSearch.shareLink,
        sharedWith: updatedSearch.sharedWith,
        unsharedUsers: userIds,
        meta: {
          requestId,
          timestamp: new Date().toISOString(),
          duration: Date.now() - startTime,
        },
      };

      res.status(200).json(response);
    } catch (error: any) {
      logger.error('Error un-sharing search', {
        requestId,
        message: 'Error un-sharing search',
        error: error.message,
      });
      next(error);
    }
  };

export const archiveSearch = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const searchId = req.params.searchId;
  const startTime = Date.now();
  try {
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;
    const filter = buildFilter(req, orgId, userId, searchId);

    logger.debug('Attempting to archive search', {
      requestId,
      message: 'Attempting to archive search',
      searchId,
      userId,
      timestamp: new Date().toISOString(),
    });

    const search = await EnterpriseSemanticSearch.findOne(filter);
    if (!search) {
      throw new NotFoundError('Search Id not found or no archive permission');
    }

    if (search.isArchived) {
      throw new BadRequestError('Search already archived');
    }

    const updatedSearch: IEnterpriseSemanticSearch | null =
      await EnterpriseSemanticSearch.findByIdAndUpdate(searchId, {
        $set: {
          isArchived: true,
          archivedBy: userId,
          lastActivityAt: Date.now(),
        },
      }).exec();

    if (!updatedSearch) {
      throw new InternalServerError('Failed to archive search');
    }

    // Prepare response
    const response = {
      id: updatedSearch?._id,
      status: 'archived',
      archivedBy: userId,
      archivedAt: updatedSearch?.updatedAt,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Search archived successfully', {
      requestId,
      searchId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json(response);
  } catch (error: any) {
    logger.error('Error archiving search', {
      requestId,
      message: 'Error archiving search',
      error: error.message,
    });
    next(error);
  }
};

export const unarchiveSearch = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const searchId = req.params.searchId;
  const startTime = Date.now();
  try {
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;
    const filter = {
      ...buildFilter(req, orgId, userId, searchId),
      isArchived: true,
    };

    logger.debug('Attempting to unarchive conversation', {
      requestId,
      message: 'Attempting to unarchive search',
      searchId,
      userId,
      timestamp: new Date().toISOString(),
    });
    const search = await EnterpriseSemanticSearch.findOne(filter);
    if (!search) {
      throw new NotFoundError('Search Id not found or no unarchive permission');
    }

    if (!search.isArchived) {
      throw new BadRequestError('Search is not archived');
    }

    const updatedSearch: IEnterpriseSemanticSearch | null =
      await EnterpriseSemanticSearch.findOneAndUpdate(filter, {
        $set: {
          isArchived: false,
          archivedBy: null,
          lastActivityAt: Date.now(),
        },
      }).exec();

    if (!updatedSearch) {
      throw new InternalServerError('Failed to unarchive search');
    }

    // Prepare response
    const response = {
      id: updatedSearch?._id,
      status: 'unarchived',
      unarchivedBy: userId,
      unarchivedAt: updatedSearch?.updatedAt,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    };

    logger.debug('Search un-archived successfully', {
      requestId,
      searchId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json(response);
  } catch (error: any) {
    logger.error('Error unarchiving search', {
      requestId,
      message: 'Error unarchiving search',
      error: error.message,
    });
    next(error);
  }
};

export const deleteSearchHistory = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  try {
    const orgId = req.user?.orgId;
    const userId = req.user?.userId;
    const filter = buildFilter(req, orgId, userId);

    logger.debug('Attempting to delete search history', {
      requestId,
      timestamp: new Date().toISOString(),
    });

    const searches = await EnterpriseSemanticSearch.find(filter).lean().exec();

    if (!searches.length) {
      throw new NotFoundError('Search history not found');
    }

    const citationIds = searches.flatMap((search) => search.citationIds);
    await EnterpriseSemanticSearch.deleteMany(filter);
    await Citation.deleteMany({ _id: { $in: citationIds } });

    res.status(HTTP_STATUS.OK).json({
      message: 'Search history deleted successfully',
    });
  } catch (error: any) {
    logger.error('Error deleting search history', {
      requestId,
      message: 'Error deleting search history',
      error: error.message,
    });
    next(error);
  }
};
