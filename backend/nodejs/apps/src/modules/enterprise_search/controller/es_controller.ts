import {
  buildAIFailureResponseMessage,
  buildMessageSortOptions,
  markConversationFailed,
  saveCompleteAgentConversation,
  saveCompleteConversation,
  markAgentConversationFailed,
  buildAgentConversationFilter,
  buildAgentSharedWithMeFilter,
  deleteAgentConversation,
  replaceMessageWithError,
  initializeSSEResponse,
  sendSSEErrorEvent,
  sendSSECompleteEvent,
  handleRegenerationStreamData,
  handleRegenerationSuccess,
  handleRegenerationError,
} from './../utils/utils';
import * as crypto from 'crypto';
import { Response, NextFunction } from 'express';
import mongoose, { ClientSession, Types } from 'mongoose';
import {
  AuthenticatedUserRequest,
  AuthenticatedServiceRequest,
} from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
  UnauthorizedError,
  ForbiddenError,
  ServiceUnavailableError,
  BadGatewayError,
  GatewayTimeoutError,
} from '../../../libs/errors/http.errors';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  AIServiceResponse,
  IAgentConversation,
  IAIModel,
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
  buildSortOptions,
  buildUserQueryMessage,
  extractModelInfo,
  formatPreviousConversations,
  getPaginationParams,
  sortMessages,
  attachPopulatedCitations,
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
import {
  AgentConversation,
  IAgentConversationDocument,
} from '../schema/agent.conversation.schema';
import { Users } from '../../user_management/schema/users.schema';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { AuthTokenService } from '../../../libs/services/authtoken.service';
import {
  validateNoXSS,
  validateNoFormatSpecifiers,
} from '../../../utils/xss-sanitization';
import { getSlackBotStore } from '../../configuration_manager/controller/cm_controller';
import { Org } from '../../user_management/schema/org.schema';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';

const logger = Logger.getInstance({ service: 'Enterprise Search Service' });
const rsAvailable = process.env.REPLICA_SET_AVAILABLE === 'true';

const AGENT_LIST_PAGE_LIMIT = 200;
const AGENT_ARCHIVES_INITIAL_CHAT_LIMIT = 5;
const AGENT_ARCHIVES_INITIAL_AGENT_LIMIT = 5;

/**
 * Loads soft-deleted agent instance keys the user can still see via permissions
 * from the AI backend list-agents API (`isDeleted=true`). Callers should exclude
 * these keys in Mongo (e.g. `agentKey: { $nin: keys }`). Returns null when the AI
 * backend cannot be queried so callers can omit filtering instead of hiding all results.
 */
async function fetchDeletedAgentKeysForUser(
  appConfig: AppConfig,
  req: AuthenticatedUserRequest,
): Promise<string[] | null> {
  const keys: string[] = [];
  let page = 1;
  try {
    while (true) {
      const queryParams = new URLSearchParams();
      queryParams.set('page', String(page));
      queryParams.set('limit', String(AGENT_LIST_PAGE_LIMIT));
      queryParams.set('isDeleted', 'true');
      const uri = `${appConfig.aiBackend}/api/v1/agent/?${queryParams.toString()}`;
      const aiCommand = new AIServiceCommand({
        uri,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      });
      const aiResponse = await aiCommand.execute();
      if (aiResponse.statusCode !== 200) {
        logger.warn(
          'fetchDeletedAgentKeysForUser: AI backend returned non-200; skipping agent soft-delete filter',
          { statusCode: aiResponse.statusCode },
        );
        return null;
      }
      const data = aiResponse.data as {
        agents?: Array<Record<string, unknown>>;
        pagination?: { hasNext?: boolean };
      };
      const agents = data.agents ?? [];
      for (const a of agents) {
        const raw = a._key ?? a.id;
        if (typeof raw === 'string' && raw.length > 0) {
          keys.push(raw);
        } else if (typeof raw === 'number' && !Number.isNaN(raw)) {
          keys.push(String(raw));
        }
      }
      if (!data.pagination?.hasNext) break;
      page += 1;
      if (page > 5000) {
        logger.warn('fetchDeletedAgentKeysForUser: page cap hit');
        break;
      }
    }
    return keys;
  } catch (e: unknown) {
    const message = e instanceof Error ? e.message : undefined;
    logger.warn(
      'fetchDeletedAgentKeysForUser failed; skipping agent soft-delete filter',
      {
        message,
      },
    );
    return null;
  }
}

/**
 * Parses the chatMode from request body and determines if agent mode is enabled.
 * Supports formats: 'agent:auto', 'agent:quick', 'agent' (defaults to 'auto'), or regular modes like 'quick'.
 * 
 * @param requestChatMode - The chatMode value from request body
 * @returns Object containing the parsed chatMode and agentMode flag
 */
const parseChatMode = (requestChatMode?: string): { chatMode: string; agentMode: boolean } => {
  let chatMode: string = requestChatMode || 'quick';
  let agentMode: boolean = false;

  if (chatMode.includes('agent')) {
    chatMode = chatMode.split(':')[1] || 'auto';
    agentMode = true;
  }

  return { chatMode, agentMode };
};


/** 24-char hex suitable for Mongo ObjectId; stable per email for Slack/service-account callers without a User row. */
const stableObjectIdHexForExternalEmail = (email: string): string =>
  crypto
    .createHash('sha256')
    .update(`slack-service-account:${email.toLowerCase().trim()}`)
    .digest('hex')
    .slice(0, 24);
const AI_SERVICE_UNAVAILABLE_MESSAGE =
  'AI Service is currently unavailable. Please check your network connection or try again later.';

const hydrateScopedRequestAsUser = async (
  req: AuthenticatedServiceRequest | AuthenticatedUserRequest,
  appConfig: AppConfig,
  keyValueStoreService?: KeyValueStoreService,
): Promise<void> => {
  const existingUser = (req as AuthenticatedUserRequest).user as
    | Record<string, any>
    | undefined;
  if (existingUser?.userId && existingUser?.orgId) {
    return;
  }

  const email = (req as AuthenticatedServiceRequest).tokenPayload?.email;
  if (!email) {
    throw new UnauthorizedError('Email not found in scoped token');
  }

  const user = await Users.findOne({
    email,
    isDeleted: false,
  });

  const authTokenService = new AuthTokenService(
    appConfig.jwtSecret,
    appConfig.scopedJwtSecret,
  );


  if (!user) {
    const { agentKey } = req.params;
    if (agentKey && keyValueStoreService) {
      const store = await getSlackBotStore(keyValueStoreService);
      const configs = store.configs;
      for (const config of configs) {
        if (config.agentId === agentKey) {
          const isServiceAccount = await checkServiceAccountAccess(req, appConfig);
          if (isServiceAccount) {
            const org = await Org.findOne({ isDeleted: false });
            if (!org?._id) {
              throw new NotFoundError('Organization not found');
            }
            const stableUserIdHex = stableObjectIdHexForExternalEmail(email);
            const scopedJwtToken = authTokenService.generateScopedToken({
              userId: stableUserIdHex,
              orgId: org._id,
              email: email,
              scopes: [TokenScopes.CONVERSATION_CREATE],
              isServiceAccount: true,
            }, '1h');
            (req as AuthenticatedServiceRequest).headers.authorization = `Bearer ${scopedJwtToken}`;
            (req as AuthenticatedServiceRequest).user = {
              userId: new Types.ObjectId(stableUserIdHex),
              orgId: org._id,
              email: email,
              scopes: [TokenScopes.CONVERSATION_CREATE],
              isServiceAccount: true,
            };
            return;
          }
        }
      }
    }
    throw new NotFoundError('User not found, create an account on the Pipeshub platform first.');
  }

  const jwtToken = authTokenService.generateToken({
    userId: user._id,
    orgId: user.orgId,
    email: user.email,
    fullName: user.fullName,
    mobile: user.mobile,
    userSlug: user.slug,
  });

  req.headers.authorization = `Bearer ${jwtToken}`;

  (req as AuthenticatedUserRequest).user = {
    userId: user._id,
    orgId: user.orgId,
    email: user.email,
    fullName: user.fullName,
    mobile: user.mobile,
    userSlug: user.slug,
  };
};

const handleBackendError = (error: any, operation: string): Error => {
  // Network/connection failure handling first
  if (
    (error?.cause && error.cause.code === 'ECONNREFUSED') ||
    (typeof error?.message === 'string' &&
      error.message.includes('fetch failed'))
  ) {
    return new ServiceUnavailableError(AI_SERVICE_UNAVAILABLE_MESSAGE, error);
  }

  if (error.response) {
    const { status, data } = error.response;
    const errorDetail =
      data?.detail || data?.reason || data?.message || 'Unknown error';

    logger.error(`Backend error during ${operation}`, {
      status,
      errorDetail,
      fullResponse: data,
    });

    switch (status) {
      case 400:
        return new BadRequestError(errorDetail);
      case 401:
        return new UnauthorizedError(errorDetail);
      case 403:
        return new ForbiddenError(errorDetail);
      case 404:
        return new NotFoundError(errorDetail);
      case 500:
        return new InternalServerError(errorDetail);
      case 502:
        return new BadGatewayError(errorDetail);
      case 503:
        return new ServiceUnavailableError(errorDetail);
      case 504:
        return new GatewayTimeoutError(errorDetail);
      default:
        return new InternalServerError(`Backend error: ${errorDetail}`);
    }
  }

  if (error.request) {
    logger.error(`No response from backend during ${operation}`);
    return new ServiceUnavailableError('Backend service unavailable');
  }

  if (error.detail) {
    return new BadRequestError(error.detail);
  }

  return new InternalServerError(`${operation} failed: ${error.message}`);
};

// Common helper to start AI streams with consistent error mapping and logging
const startAIStream = async (
  options: AICommandOptions,
  operation: string,
  logContext: Record<string, any> = {},
) => {
  const aiServiceCommand = new AIServiceCommand(options);
  try {
    return await aiServiceCommand.executeStream();
  } catch (error: any) {
    const mappedError = handleBackendError(error, operation);
    logger.error('AI service stream start failed', {
      ...logContext,
      message: error?.message,
    });
    throw mappedError;
  }
};

export const streamChat =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response) => {
      const requestId = req.context?.requestId;
      const startTime = Date.now();
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;

      let session: ClientSession | null = null;
      let savedConversation: IConversationDocument | null = null;

      const modelInfo = extractModelInfo(req.body);

      if (!req.body.query) {
        throw new BadRequestError('Query is required');
      }

      try {
        // Set SSE headers
        res.writeHead(200, {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          Connection: 'keep-alive',
          'Access-Control-Allow-Origin': '*',
          'X-Accel-Buffering': 'no',
        });

        // Create initial conversation record (before `connected` so the client
        // can link the stream to a real conversationId for URL/sidebar/parallel tabs)
        const userQueryMessage = buildUserQueryMessage(req.body.query, req.body.appliedFilters);

        const userConversationData: Partial<IConversation> = {
          orgId,
          userId,
          initiator: userId,
          title: req.body.query.slice(0, 100),
          messages: [userQueryMessage] as IMessageDocument[],
          lastActivityAt: Date.now(),
          status: CONVERSATION_STATUS.INPROGRESS,
          // Store model and mode information
          modelInfo: modelInfo,
          // Store applied filters at conversation creation
          // appliedFilters: req.body.appliedFilters || {},
        };

        // Start transaction if replica set is available
        if (rsAvailable) {
          session = await mongoose.startSession();
          await session.withTransaction(async () => {
            const conversation = new Conversation(userConversationData);
            savedConversation = await conversation.save({ session });
          });
        } else {
          const conversation = new Conversation(userConversationData);
          savedConversation = await conversation.save();
        }

        if (!savedConversation) {
          throw new InternalServerError('Failed to create conversation');
        }

        const newConversationId = savedConversation._id?.toString() || '';

        logger.debug('Initial conversation created', {
          requestId,
          conversationId: savedConversation._id,
          userId,
        });

        // Send initial connection event with conversationId and flush
        res.write(
          `event: connected\ndata: ${JSON.stringify({
            message: 'SSE connection established',
            conversationId: newConversationId,
          })}\n\n`,
        );
        (res as any).flush?.();

        const { chatMode, agentMode } = parseChatMode(req.body.chatMode);
        // Prepare AI payload
        const aiPayload = {
          query: req.body.query,
          previousConversations: req.body.previousConversations || [],
          recordIds: req.body.recordIds || [],
          filters: req.body.filters || {},
          // New fields for multi-model support
          modelKey: req.body.modelKey || null,
          modelName: req.body.modelName || null,
          modelFriendlyName: req.body.modelFriendlyName || null,
          chatMode: chatMode,
          conversationId: newConversationId || null,
          timezone: req.body.timezone || null,
          currentTime: req.body.currentTime || null,
        };

        const aiCommandOptions: AICommandOptions = {
          uri: agentMode ? `${appConfig.aiBackend}/api/v1/agent/agentIdPlaceholder/chat/stream` : `${appConfig.aiBackend}/api/v1/chat/stream`,
          method: HttpMethod.POST,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
          body: aiPayload,
        };

        const stream = await startAIStream(aiCommandOptions, 'Chat Stream', {
          requestId,
        });

        if (!stream) {
          throw new Error('Failed to get stream from AI service');
        }

        // Variables to collect complete response data
        let completeData: IAIResponse | null = null;
        let buffer = '';

        // Handle client disconnect
        req.on('close', () => {
          logger.debug('Client disconnected', { requestId });
          stream.destroy();
        });

        // Process SSE events, capture complete event, and forward non-complete events
        stream.on('data', (chunk: Buffer) => {
          const chunkStr = chunk.toString();
          buffer += chunkStr;

          // Look for complete events in the buffer
          const events = buffer.split('\n\n');
          buffer = events.pop() || ''; // Keep incomplete event in buffer

          let filteredChunk = '';

          for (const event of events) {
            if (event.trim()) {
              // Check if this is a complete event
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
                  completeData = JSON.parse(dataLine);
                  logger.debug('Captured complete event data from AI backend', {
                    requestId,
                    conversationId: savedConversation?._id,
                    answer: completeData?.answer,
                    citationsCount: completeData?.citations?.length || 0,
                  });
                  // DO NOT forward the complete event from AI backend
                  // We'll send our own complete event after processing
                } catch (parseError: any) {
                  logger.error('Failed to parse complete event data', {
                    requestId,
                    parseError: parseError.message,
                    dataLine,
                  });
                  // Forward the event if we can't parse it
                  filteredChunk += event + '\n\n';
                }
              } else {
                // Forward all non-complete events
                filteredChunk += event + '\n\n';
              }
            }
          }

          // Forward only non-complete events to client
          if (filteredChunk) {
            res.write(filteredChunk);
            (res as any).flush?.();
          }
        });

        stream.on('end', async () => {
          logger.debug('Stream ended successfully', { requestId });
          try {
            // Save the complete conversation data to database
            if (completeData && savedConversation) {
              const conversation = await saveCompleteConversation(
                savedConversation,
                completeData,
                orgId,
                session,
                modelInfo,
              );

              // Send the final conversation data in the same format as createConversation
              const responsePayload = {
                conversation: conversation,
                meta: {
                  requestId,
                  timestamp: new Date().toISOString(),
                  duration: Date.now() - startTime,
                },
              };

              // Send final response event with the complete conversation data
              res.write(
                `event: complete\ndata: ${JSON.stringify(responsePayload)}\n\n`,
              );

              logger.debug(
                'Conversation completed and saved, sent custom complete event',
                {
                  requestId,
                  conversationId: savedConversation._id,
                  duration: Date.now() - startTime,
                },
              );
            } else {
              // Mark as failed if no complete data received
              await markConversationFailed(
                savedConversation as IConversationDocument,
                'No complete response received from AI service',
                session,
                'incomplete_response',
              );

              // Send error event
              res.write(
                `event: error\ndata: ${JSON.stringify({
                  error: 'No complete response received from AI service',
                })}\n\n`,
              );
            }
          } catch (dbError: any) {
            logger.error('Failed to save complete conversation', {
              requestId,
              conversationId: savedConversation?._id,
              error: dbError.message,
            });

            if (savedConversation) {
              await markConversationFailed(
                savedConversation as IConversationDocument,
                `Failed to save conversation: ${dbError.message}`,
                session,
                'database_error',
                dbError.stack,
              );
            }

            // Send error event
            res.write(
              `event: error\ndata: ${JSON.stringify({
                error: 'Failed to save conversation',
                details: dbError.message,
              })}\n\n`,
            );
          }

          res.end();
        });

        stream.on('error', async (error: Error) => {
          logger.error('Stream error', { requestId, error: error.message });
          try {
            // Mark conversation as failed
            if (savedConversation) {
              await markConversationFailed(
                savedConversation as IConversationDocument,
                `Stream error: ${error.message}`,
                session,
                'stream_error',
                error.stack,
              );
            }
          } catch (dbError: any) {
            logger.error('Failed to mark conversation as failed', {
              requestId,
              conversationId: savedConversation?._id,
              error: dbError.message,
            });
          }

          const errorEvent = `event: error\ndata: ${JSON.stringify({
            error: error.message || 'Stream error occurred',
            details: error.message,
          })}\n\n`;
          res.write(errorEvent);
          res.end();
        });
      } catch (error: any) {
        logger.error('Error in streamChat', { requestId, error: error.message });

        try {
          // Mark conversation as failed if it was created
          if (savedConversation) {
            await markConversationFailed(
              savedConversation as IConversationDocument,
              error.message || 'Internal server error',
              session,
              'internal_error',
              error.stack,
            );
          }
        } catch (dbError: any) {
          logger.error('Failed to mark conversation as failed in catch block', {
            requestId,
            error: dbError.message,
          });
        }

        if (!res.headersSent) {
          res.writeHead(500, { 'Content-Type': 'text/event-stream' });
        }
        logger.error('Error in streamChat', {
          requestId,
          error: error.message,
          stack: error.stack,
        });
        const errorEvent = `event: error\ndata: ${JSON.stringify({
          error: error.message || 'Internal server error',
          details: error.message,
        })}\n\n`;
        res.write(errorEvent);
        res.end();
      } finally {
        if (session) {
          session.endSession();
        }
      }
    };

export const streamChatInternal =
  (appConfig: AppConfig) =>
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        await hydrateScopedRequestAsUser(req, appConfig);
        await streamChat(appConfig)(req as AuthenticatedUserRequest, res);
      } catch (error) {
        next(error);
      }
    };

export const createConversation =
  (appConfig: AppConfig) =>
    async (
      req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      const requestId = req.context?.requestId;
      const startTime = Date.now();

      let userId: Types.ObjectId | undefined;

      let orgId: Types.ObjectId | undefined;

      if ('user' in req) {
        const auth_req = req as AuthenticatedUserRequest;

        userId = auth_req.user?.userId;

        orgId = auth_req.user?.orgId;
      } else {
        try {
          const auth_req = req as AuthenticatedServiceRequest;

          const email = auth_req.tokenPayload?.email;

          const users = await Users.find({
            email: email,

            isDeleted: false,
          });

          const user = users[0];

          if (!user) {
            throw new NotFoundError('User not found');
          }

          userId = user._id as Types.ObjectId;

          orgId = user.orgId;

          const authTokenService = new AuthTokenService(
            appConfig.jwtSecret,
            appConfig.scopedJwtSecret,
          );

          const jwtToken = authTokenService.generateToken({
            userId: user._id,

            orgId: user.orgId,

            email: user.email,

            fullName: user.fullName,

            mobile: user.mobile,

            userSlug: user.slug,
          });

          req.headers.authorization = `Bearer ${jwtToken}`;
        } catch (error: any) {
          logger.error('Error creating conversation', {
            requestId,

            message: 'Error creating conversation',

            error: error.message,

            stack: error.stack,

            duration: Date.now() - startTime,
          });

          next(error);
        }
      }
      let session: ClientSession | null = null;
      let responseData: any;

      const modelInfo = extractModelInfo(req.body);

      // Validate query parameter for XSS and format specifiers
      if (req.body.query && typeof req.body.query === 'string') {
        validateNoXSS(req.body.query, 'query');
        validateNoFormatSpecifiers(req.body.query, 'query');

      } else if (!req.body.query) {
        throw new BadRequestError('Query is required');
      }

      // Helper function that contains the common conversation operations.
      async function createConversationUtil(
        session?: ClientSession | null,
      ): Promise<any> {
        const userQueryMessage = buildUserQueryMessage(req.body.query, req.body.appliedFilters);

        const userConversationData: Partial<IConversation> = {
          orgId,
          userId,
          initiator: userId,
          title: req.body.query.slice(0, 100),
          messages: [userQueryMessage] as IMessageDocument[],
          lastActivityAt: Date.now(),
          status: CONVERSATION_STATUS.INPROGRESS,
          modelInfo: modelInfo,
          // appliedFilters: req.body.appliedFilters || {},
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
            // New fields for multi-model support
            modelKey: req.body.modelKey || null,
            modelName: req.body.modelName || null,
            modelFriendlyName: req.body.modelFriendlyName || null,
            chatMode: req.body.chatMode || 'quick',
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
            modelInfo,
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
          const responseConversation = await attachPopulatedCitations(
            updatedConversation._id as mongoose.Types.ObjectId,
            updatedConversation.toObject() as IConversation,
            citations,
            false,
            session,
          );
          return {
            conversation: {
              _id: updatedConversation._id,
              ...responseConversation,
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
    async (
      req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      const requestId = req.context?.requestId;
      const startTime = Date.now();
      let session: ClientSession | null = null;
      const modelInfo = extractModelInfo(req.body);

      try {
        let userId: Types.ObjectId | undefined;

        let orgId: Types.ObjectId | undefined;

        if ('user' in req) {
          const auth_req = req as AuthenticatedUserRequest;

          userId = auth_req.user?.userId;

          orgId = auth_req.user?.orgId;
        } else {
          const auth_req = req as AuthenticatedServiceRequest;

          const email = auth_req.tokenPayload?.email;

          const users = await Users.find({
            email: email,

            isDeleted: false,
          });

          const user = users[0];

          if (!user) {
            throw new NotFoundError('User not found');
          }

          userId = user._id as Types.ObjectId;

          orgId = user.orgId;

          const authTokenService = new AuthTokenService(
            appConfig.jwtSecret,
            appConfig.scopedJwtSecret,
          );

          const jwtToken = authTokenService.generateToken({
            userId: user._id,

            orgId: user.orgId,

            email: user.email,

            fullName: user.fullName,

            mobile: user.mobile,

            userSlug: user.slug,
          });

          req.headers.authorization = `Bearer ${jwtToken}`;
        }

        // Validate query parameter for XSS and format specifiers
        if (req.body.query && typeof req.body.query === 'string') {
          validateNoXSS(req.body.query, 'query');
          validateNoFormatSpecifiers(req.body.query, 'query');

        } else if (!req.body.query) {
          throw new BadRequestError('Query is required');
        }

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

          const userQueryMessage = buildUserQueryMessage(req.body.query, req.body.appliedFilters);
          // First, add the user message to the existing conversation
          conversation.messages.push(userQueryMessage as IMessageDocument);
          conversation.lastActivityAt = Date.now();

          // if (req.body.appliedFilters) {
          //   conversation.appliedFilters = req.body.appliedFilters;
          //   conversation.markModified('appliedFilters');
          // }

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
              // New fields for multi-model support
              modelKey: req.body.modelKey || null,
              modelName: req.body.modelName || null,
              chatMode: req.body.chatMode || 'quick',
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
              modelInfo,
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
            const responseConversation = await attachPopulatedCitations(
              updatedConversation._id as mongoose.Types.ObjectId,
              updatedConversation.toObject() as IConversation,
              savedCitations,
              false,
              session,
            );
            return {
              conversation: responseConversation,
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

export const addMessageStream =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response) => {
      const requestId = req.context?.requestId;
      const startTime = Date.now();
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const { conversationId } = req.params;

      let session: ClientSession | null = null;
      let existingConversation: IConversationDocument | null = null;

      const modelInfo = extractModelInfo(req.body);

      if (!req.body.query) {
        throw new BadRequestError('Query is required');
      }

      // Helper function that contains the common conversation operations
      async function performAddMessageStream(
        session?: ClientSession | null,
      ): Promise<void> {
        // Get existing conversation
        const conversation = await Conversation.findOne({
          _id: conversationId,
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

        // Update model and mode information if provided
        const fieldsToUpdate: Array<keyof IAIModel> = [
          'modelKey',
          'modelName',
          'modelProvider',
          'chatMode',
        ];
        for (const field of fieldsToUpdate) {
          const value = req.body[field];
          if (value !== undefined && value !== null) {
            (conversation.modelInfo as IAIModel)[field] = value;
          }
        }

        // First, add the user message to the existing conversation
        conversation.messages.push(
          buildUserQueryMessage(req.body.query, req.body.appliedFilters) as IMessageDocument,
        );
        conversation.lastActivityAt = Date.now();

        // if (req.body.appliedFilters) {
        //   conversation.appliedFilters = req.body.appliedFilters;
        //   conversation.markModified('appliedFilters');
        // }

        // Save the user message to the existing conversation first
        const savedConversation = session
          ? await conversation.save({ session })
          : await conversation.save();

        if (!savedConversation) {
          throw new InternalServerError(
            'Failed to update conversation with user message',
          );
        }

        existingConversation = savedConversation;

        logger.debug('User message added to conversation', {
          requestId,
          conversationId: existingConversation._id,
          userId,
        });
      }

      try {
        // Set SSE headers
        res.writeHead(200, {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          Connection: 'keep-alive',
          'Access-Control-Allow-Origin': '*',
          'X-Accel-Buffering': 'no',
        });

        // Send initial connection event and flush
        res.write(
          `event: connected\ndata: ${JSON.stringify({ message: 'SSE connection established' })}\n\n`,
        );
        (res as any).flush?.();

        logger.debug('Adding message to conversation via stream', {
          requestId,
          conversationId,
          userId,
          query: req.body.query,
          filters: req.body.filters,
          timestamp: new Date().toISOString(),
        });

        // Get existing conversation and add user message
        if (rsAvailable) {
          session = await mongoose.startSession();
          await session.withTransaction(() => performAddMessageStream(session));
        } else {
          await performAddMessageStream();
        }

        if (!existingConversation) {
          throw new NotFoundError('Conversation not found');
        }

        // Format previous conversations for context (excluding the user message we just added)
        const previousConversations = formatPreviousConversations(
          (existingConversation as IConversationDocument).messages.slice(
            0,
            -1,
          ) as IMessage[],
        );

        const { chatMode, agentMode } = parseChatMode(req.body.chatMode);
        // Prepare AI payload
        const aiPayload = {
          query: req.body.query,
          previousConversations: previousConversations,
          filters: req.body.filters || {},
          // New fields for multi-model support
          modelKey: req.body.modelKey || null,
          modelName: req.body.modelName || null,
          modelFriendlyName: req.body.modelFriendlyName || null,
          chatMode: chatMode,
          conversationId: conversationId || null,
          timezone: req.body.timezone || null,
          currentTime: req.body.currentTime || null,
        };

        const aiCommandOptions: AICommandOptions = {
          uri: agentMode ? `${appConfig.aiBackend}/api/v1/agent/agentIdPlaceholder/chat/stream` : `${appConfig.aiBackend}/api/v1/chat/stream`,
          method: HttpMethod.POST,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
          body: aiPayload,
        };

        const stream = await startAIStream(
          aiCommandOptions,
          'Add Message Stream',
          { requestId },
        );

        if (!stream) {
          throw new Error('Failed to get stream from AI service');
        }

        // Variables to collect complete response data
        let completeData: IAIResponse | null = null;
        let buffer = '';

        // Handle client disconnect
        req.on('close', () => {
          logger.debug('Client disconnected', { requestId });
          stream.destroy();
        });

        // Process SSE events, capture complete event, and forward non-complete events
        stream.on('data', (chunk: Buffer) => {
          const chunkStr = chunk.toString();
          buffer += chunkStr;

          // Look for complete events in the buffer
          const events = buffer.split('\n\n');
          buffer = events.pop() || ''; // Keep incomplete event in buffer

          let filteredChunk = '';

          for (const event of events) {
            if (event.trim()) {
              // Check if this is a complete event
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
                  completeData = JSON.parse(dataLine);
                  logger.debug('Captured complete event data from AI backend', {
                    requestId,
                    conversationId: existingConversation?._id,
                    answer: completeData?.answer,
                    citationsCount: completeData?.citations?.length || 0,
                  });
                  // DO NOT forward the complete event from AI backend
                  // We'll send our own complete event after processing
                } catch (parseError: any) {
                  logger.error('Failed to parse complete event data', {
                    requestId,
                    parseError: parseError.message,
                    dataLine,
                  });
                  // Forward the event if we can't parse it
                  filteredChunk += event + '\n\n';
                }
              } else if (eventType === 'error' && dataLine) {
                try {
                  const errorData = JSON.parse(dataLine);
                  const errorMessage =
                    errorData.error ||
                    errorData.message ||
                    'Unknown error occurred';
                  markConversationFailed(
                    existingConversation as IConversationDocument,
                    errorMessage,
                    session,
                    'streaming_error',
                    errorData.stack,
                    errorData.metadata
                      ? new Map(Object.entries(errorData.metadata))
                      : undefined,
                  );
                  filteredChunk += event + '\n\n';
                } catch (parseError: any) {
                  logger.error('Failed to parse error event data', {
                    requestId,
                    parseError: parseError.message,
                    dataLine,
                  });
                  const errorMessage = `Failed to parse error event: ${parseError.message}`;
                  if (existingConversation) {
                    markConversationFailed(
                      existingConversation as IConversationDocument,
                      errorMessage,
                      session,
                      'parse_error',
                      parseError.stack,
                    );
                  }
                  filteredChunk += event + '\n\n';
                }
              } else {
                // Forward all non-complete events
                filteredChunk += event + '\n\n';
              }
            }
          }

          // Forward only non-complete events to client
          if (filteredChunk) {
            res.write(filteredChunk);
            (res as any).flush?.();
          }
        });

        stream.on('end', async () => {
          logger.debug('Stream ended successfully', { requestId });
          try {
            // Save the AI response to the existing conversation
            if (completeData && existingConversation) {
              try {
                // Create and save citations
                const savedCitations: ICitation[] = await Promise.all(
                  completeData.citations?.map(async (citation: ICitation) => {
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

                // Build AI response message using existing utility
                const aiResponseMessage = buildAIResponseMessage(
                  { statusCode: 200, data: completeData },
                  savedCitations,
                  modelInfo,
                ) as IMessageDocument;

                // Add the AI message to the existing conversation
                existingConversation.messages.push(aiResponseMessage);
                existingConversation.lastActivityAt = Date.now();
                existingConversation.status = CONVERSATION_STATUS.COMPLETE;

                // Save the updated conversation with AI response
                const updatedConversation = session
                  ? await existingConversation.save({ session })
                  : await existingConversation.save();

                if (!updatedConversation) {
                  throw new InternalServerError(
                    'Failed to update conversation with AI response',
                  );
                }

                // Return the updated conversation in the same format as addMessage
                const responseConversation = await attachPopulatedCitations(
                  updatedConversation._id as mongoose.Types.ObjectId,
                  updatedConversation.toObject() as IConversation,
                  savedCitations,
                  false,
                  session,
                );

                // Send the final conversation data in the same format as addMessage
                const responsePayload = {
                  conversation: responseConversation,
                  recordsUsed: savedCitations.length,
                  meta: {
                    requestId,
                    timestamp: new Date().toISOString(),
                    duration: Date.now() - startTime,
                    recordsUsed: savedCitations.length,
                  },
                };

                // Send final response event with the complete conversation data
                res.write(
                  `event: complete\ndata: ${JSON.stringify(responsePayload)}\n\n`,
                );

                logger.debug(
                  'Message added and conversation updated, sent custom complete event',
                  {
                    requestId,
                    conversationId: existingConversation._id,
                    duration: Date.now() - startTime,
                  },
                );
              } catch (error: any) {
                // Update conversation status for general errors
                if (existingConversation) {
                  existingConversation.status = CONVERSATION_STATUS.FAILED;
                  existingConversation.failReason =
                    error.message || 'Unknown error occurred';

                  // Add error message using existing utility
                  const failedMessage =
                    buildAIFailureResponseMessage() as IMessageDocument;
                  existingConversation.messages.push(failedMessage);
                  existingConversation.lastActivityAt = Date.now();

                  const saveGeneralError = session
                    ? await existingConversation.save({ session })
                    : await existingConversation.save();

                  if (!saveGeneralError) {
                    logger.error(
                      'Failed to save conversation general error status',
                      {
                        requestId,
                        conversationId: existingConversation._id,
                      },
                    );
                  }
                }

                if (error.cause && error.cause.code === 'ECONNREFUSED') {
                  throw new InternalServerError(
                    AI_SERVICE_UNAVAILABLE_MESSAGE,
                    error,
                  );
                }
                throw error;
              }
            } else {
              // Mark as failed if no complete data received
              if (existingConversation) {
                existingConversation.status = CONVERSATION_STATUS.FAILED;
                existingConversation.failReason =
                  'No complete response received from AI service';
                existingConversation.lastActivityAt = Date.now();

                const savedWithError = session
                  ? await existingConversation.save({ session })
                  : await existingConversation.save();

                if (!savedWithError) {
                  logger.error('Failed to save conversation error state', {
                    requestId,
                    conversationId: existingConversation._id,
                  });
                }
              }

              // Send error event
              res.write(
                `event: error\ndata: ${JSON.stringify({
                  error: 'No complete response received from AI service',
                })}\n\n`,
              );
            }
          } catch (dbError: any) {
            logger.error('Failed to save AI response to conversation', {
              requestId,
              conversationId: existingConversation?._id,
              error: dbError.message,
            });

            // Send error event
            res.write(
              `event: error\ndata: ${JSON.stringify({
                error: 'Failed to save AI response',
                details: dbError.message,
              })}\n\n`,
            );
          }

          res.end();
        });

        stream.on('error', async (error: Error) => {
          logger.error('Stream error', { requestId, error: error.message });
          try {
            if (existingConversation) {
              await markConversationFailed(
                existingConversation as IConversationDocument,
                error.message,
                session,
                'stream_error',
                error.stack,
              );
            }
          } catch (dbError: any) {
            logger.error('Failed to mark conversation as failed', {
              requestId,
              conversationId: existingConversation?._id,
              error: dbError.message,
            });
          }

          const errorEvent = `event: error\ndata: ${JSON.stringify({
            error: error.message || 'Stream error occurred',
            details: error.message,
          })}\n\n`;
          res.write(errorEvent);
          res.end();
        });
      } catch (error: any) {
        logger.error('Error in addMessageStream', {
          requestId,
          conversationId,
          error: error.message,
        });

        try {
          // Mark conversation as failed if it exists
          if (existingConversation) {
            (existingConversation as IConversationDocument).status =
              CONVERSATION_STATUS.FAILED;
            (existingConversation as IConversationDocument).failReason =
              error.message || 'Internal server error';

            // Add error message using existing utility
            const failedMessage =
              buildAIFailureResponseMessage() as IMessageDocument;
            (existingConversation as IConversationDocument).messages.push(
              failedMessage,
            );
            (existingConversation as IConversationDocument).lastActivityAt =
              Date.now();

            const saveGeneralError = session
              ? await (existingConversation as IConversationDocument).save({
                session,
              })
              : await (existingConversation as IConversationDocument).save();

            if (!saveGeneralError) {
              logger.error(
                'Failed to save conversation general error status in catch block',
                {
                  requestId,
                  conversationId: (existingConversation as IConversationDocument)
                    ._id,
                },
              );
            }
          }
        } catch (dbError: any) {
          logger.error('Failed to mark conversation as failed in catch block', {
            requestId,
            conversationId,
            error: dbError.message,
          });
        }

        if (!res.headersSent) {
          res.writeHead(500, { 'Content-Type': 'text/event-stream' });
        }

        const errorEvent = `event: error\ndata: ${JSON.stringify({
          error: error.message || 'Internal server error',
          details: error.message,
        })}\n\n`;
        res.write(errorEvent);
        res.end();
      } finally {
        if (session) {
          session.endSession();
        }
      }
    };

export const addMessageStreamInternal =
  (appConfig: AppConfig) =>
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        await hydrateScopedRequestAsUser(req, appConfig);
        await addMessageStream(appConfig)(req as AuthenticatedUserRequest, res);
      } catch (error) {
        next(error);
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

    const source = req.query.source ?? 'owned';
    if (source !== 'owned' && source !== 'shared') {
      throw new BadRequestError(
        "Query param 'source' must be 'owned' or 'shared'",
      );
    }

    const { skip, limit, page } = getPaginationParams(req);
    const sortOptions = buildSortOptions(req);

    const isOwned = source === 'owned';
    const filter = buildFilter(
      req,
      orgId,
      userId,
      conversationId as string,
      isOwned,
      !isOwned,
    );
    let selection = Conversation.find(filter)
      .sort(sortOptions as any)
      .skip(skip)
      .limit(limit)
      .select('-__v')
      .select('-messages');
    if (!isOwned) {
      selection = selection.select('-sharedWith');
    }

    const [conversations, totalCount] = await Promise.all([
      selection.lean().exec(),
      Conversation.countDocuments(filter),
    ]);

    const processedConversations = conversations.map((conversation: any) =>
      addComputedFields(conversation as IConversation, userId),
    );

    const response = {
      conversations: processedConversations,
      source,
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
        modelInfo: 1,
        // appliedFilters : 1,
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

/**
 * Configuration for regeneration function
 */
interface RegenerationConfig {
  conversationModel: typeof Conversation | typeof AgentConversation;
  buildQueryFilter: (
    conversationId: string,
    orgId: string | undefined,
    userId: string | undefined,
    agentKey?: string,
  ) => any;
  buildAIEndpoint: (appConfig: AppConfig, agentKey?: string) => string;
}

/**
 * Generic internal function to handle regeneration for both regular and agent conversations
 */
async function regenerateAnswersInternal(
  appConfig: AppConfig,
  req: AuthenticatedUserRequest,
  res: Response,
  config: RegenerationConfig,
): Promise<void> {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  const { conversationId, messageId, agentKey } = req.params;
  const userId = req.user?.userId;
  const orgId = req.user?.orgId;

  let existingConversation:
    | IConversationDocument
    | IAgentConversationDocument
    | null = null;
  let messageIndex = -1;

  const modelInfo = extractModelInfo(req.body);

  // Helper function to validate and get conversation
  async function performRegenerateAnswersValidation(
    session?: ClientSession | null,
  ): Promise<IConversationDocument | IAgentConversationDocument> {
    if (!conversationId) {
      throw new BadRequestError('Conversation ID is required');
    }

    // Get conversation with access control
    const queryFilter = config.buildQueryFilter(
      conversationId,
      orgId,
      userId,
      agentKey,
    );

    // Type assertion needed because TypeScript can't infer the correct overload
    const conversation = await (config.conversationModel as any).findOne(
      queryFilter,
      null,
      session ? { session } : undefined,
    );

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
      throw new BadRequestError('Can only regenerate bot response messages');
    }

    // Get user query from the previous message
    if (conversation.messages.length < 2) {
      throw new BadRequestError('No user query found to regenerate response');
    }
    const userQuery = conversation.messages[
      conversation.messages.length - 2
    ] as IMessageDocument;
    if (userQuery.messageType !== 'user_query') {
      throw new BadRequestError('Previous message must be a user query');
    }

    messageIndex = conversation.messages.length - 1;

    logger.debug('Regenerate answers validation passed', {
      requestId,
      conversationId,
      messageId,
      messageIndex,
      timestamp: new Date().toISOString(),
    });

    return conversation;
  }

  try {
    // Initialize SSE response
    initializeSSEResponse(res);

    logger.debug('Attempting to regenerate answers via stream', {
      requestId,
      conversationId,
      messageId,
      timestamp: new Date().toISOString(),
    });

    // Validate conversation and message
    if (rsAvailable) {
      session = await mongoose.startSession();
      existingConversation = await session.withTransaction(() =>
        performRegenerateAnswersValidation(session),
      );
    } else {
      existingConversation = await performRegenerateAnswersValidation();
    }

    if (!existingConversation || messageIndex === -1) {
      throw new NotFoundError('Conversation or message not found');
    }

    // Get user query from the previous message
    const userQuery = existingConversation.messages[
      messageIndex - 1
    ] as IMessageDocument;

    // Format previous conversations up to this message
    const previousConversations = formatPreviousConversations(
      existingConversation.messages.slice(0, -2), // Exclude last bot response and user query
    );

    // Prepare AI payload
    const aiPayload = {
      query: userQuery.content,
      previousConversations: previousConversations || [],
      filters: req.body.filters || {},
      // New fields for multi-model support
      modelKey: req.body.modelKey || null,
      modelName: req.body.modelName || null,
      modelFriendlyName: req.body.modelFriendlyName || null,
      chatMode: req.body.chatMode || 'quick',
      conversationId: conversationId || null,
    };

    const aiCommandOptions: AICommandOptions = {
      uri: config.buildAIEndpoint(appConfig, agentKey),
      method: HttpMethod.POST,
      headers: {
        ...(req.headers as Record<string, string>),
        'Content-Type': 'application/json',
      },
      body: aiPayload,
    };

    const stream = await startAIStream(
      aiCommandOptions,
      'Regenerate Answers Stream',
      { requestId },
    );

    if (!stream) {
      throw new Error('Failed to get stream from AI service');
    }

    // Variables to collect complete response data
    let completeData: IAIResponse | null = null;
    let buffer = '';

    // Handle client disconnect
    req.on('close', () => {
      logger.debug('Client disconnected', { requestId });
      stream.destroy();
    });

    // Process SSE events, capture complete event, and forward non-complete events
    stream.on('data', (chunk: Buffer) => {
      buffer = handleRegenerationStreamData(
        chunk,
        buffer,
        existingConversation,
        messageIndex,
        session,
        requestId || '',
        res,
        (data: IAIResponse) => {
          completeData = data;
          logger.debug('Captured complete event data from AI backend', {
            requestId,
            conversationId: existingConversation?._id,
            answer: completeData?.answer,
            citationsCount: completeData?.citations?.length || 0,
          });
        },
      );
    });

    stream.on('end', async () => {
      logger.debug('Stream ended successfully', { requestId });
      try {
        // Save the AI response to the conversation, replacing the existing message
        if (completeData && existingConversation) {
          try {
            const { conversation: responseConversation, savedCitations } =
              await handleRegenerationSuccess(
                completeData,
                existingConversation,
                messageIndex,
                orgId || '',
                session,
                modelInfo,
              );

            // Send final response event with the complete conversation data
            sendSSECompleteEvent(
              res,
              responseConversation,
              savedCitations.length,
              requestId || '',
              startTime,
            );

            logger.debug(
              'Answer regenerated and conversation updated, sent custom complete event',
              {
                requestId,
                conversationId: existingConversation._id,
                messageId,
                duration: Date.now() - startTime,
              },
            );
          } catch (error: any) {
            // Update conversation status for general errors
            if (existingConversation && messageIndex >= 0) {
              await handleRegenerationError(
                res,
                error,
                existingConversation,
                messageIndex,
                conversationId || '',
                session,
                requestId || '',
                'regeneration_error',
              );
            }

            if (error.cause && error.cause.code === 'ECONNREFUSED') {
              throw new InternalServerError(
                AI_SERVICE_UNAVAILABLE_MESSAGE,
                error,
              );
            }
            throw error;
          }
        } else {
          // Mark as failed if no complete data received
          if (existingConversation && messageIndex >= 0) {
            const errorMessage =
              'No complete response received from AI service';
            await replaceMessageWithError(
              existingConversation,
              messageIndex,
              errorMessage,
              session,
              'incomplete_response',
            );

            // Reload conversation to get updated state
            const updatedConversation = await (config.conversationModel as any).findById(
              conversationId || '',
            );
            if (updatedConversation) {
              const plainConversation = updatedConversation.toObject();
              await sendSSEErrorEvent(
                res,
                errorMessage,
                undefined,
                plainConversation,
              );
            } else {
              await sendSSEErrorEvent(res, errorMessage);
            }
          } else {
            await sendSSEErrorEvent(
              res,
              'No complete response received from AI service',
            );
          }
        }
      } catch (dbError: any) {
        logger.error(
          'Failed to save regenerated AI response to conversation',
          {
            requestId,
            conversationId: existingConversation?._id,
            error: dbError.message,
          },
        );

        // Try to replace message with error if we have the index
        if (existingConversation && messageIndex >= 0) {
          try {
            const errorMessage = `Failed to save regenerated AI response: ${dbError.message}`;
            await replaceMessageWithError(
              existingConversation,
              messageIndex,
              errorMessage,
              session,
              'database_error',
              dbError.stack,
            );

            // Reload conversation to get updated state
            const updatedConversation = await (config.conversationModel as any).findById(
              conversationId || '',
            );
            if (updatedConversation) {
              const plainConversation = updatedConversation.toObject();
              await sendSSEErrorEvent(
                res,
                errorMessage,
                dbError.message,
                plainConversation,
              );
            } else {
              await sendSSEErrorEvent(res, errorMessage, dbError.message);
            }
          } catch (replaceError: any) {
            logger.error(
              'Failed to replace message with error in dbError catch',
              {
                requestId,
                error: replaceError.message,
              },
            );
            await sendSSEErrorEvent(
              res,
              'Failed to save regenerated AI response',
              dbError.message,
            );
          }
        } else {
          await sendSSEErrorEvent(
            res,
            'Failed to save regenerated AI response',
            dbError.message,
          );
        }
      }

      res.end();
    });

    stream.on('error', async (error: Error) => {
      logger.error('Stream error in regenerateAnswers', {
        requestId,
        error: error.message,
      });
      try {
        await handleRegenerationError(
          res,
          error,
          existingConversation,
          messageIndex,
          conversationId || '',
          session,
          requestId || '',
          'stream_error',
        );
      } catch (dbError: any) {
        logger.error('Failed to replace message with error', {
          requestId,
          conversationId: existingConversation?._id,
          error: dbError.message,
        });
        await sendSSEErrorEvent(
          res,
          error.message || 'Stream error occurred',
          error.message,
        );
      }
      res.end();
    });
  } catch (error: any) {
    logger.error('Error in regenerateAnswers', {
      requestId,
      conversationId,
      messageId,
      error: error.message,
      stack: error.stack,
    });

    if (!res.headersSent) {
      res.writeHead(500, { 'Content-Type': 'text/event-stream' });
    }

    try {
      await handleRegenerationError(
        res,
        error,
        existingConversation,
        messageIndex,
        conversationId || '',
        session,
        requestId || '',
        'regeneration_error',
      );
    } catch (dbError: any) {
      logger.error('Failed to mark conversation as failed in catch block', {
        requestId,
        conversationId,
        error: dbError.message,
      });
      await sendSSEErrorEvent(
        res,
        error.message || 'Internal server error',
        error.message,
      );
    }
    res.end();
  } finally {
    if (session) {
      session.endSession();
    }
  }
}

export const regenerateAnswers =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response) => {
      await regenerateAnswersInternal(appConfig, req, res, {
        conversationModel: Conversation,
        buildQueryFilter: (conversationId, orgId, userId) => ({
          _id: conversationId,
          orgId,
          userId,
          isDeleted: false,
          $or: [
            { initiator: userId },
            { 'sharedWith.userId': userId },
            { isShared: true },
          ],
        }),
        buildAIEndpoint: (appConfig) =>
          `${appConfig.aiBackend}/api/v1/chat/stream`,
      });
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
      updatedConversation = await performArchiveConversation();
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

/**
 * Search across all archived conversations — both assistant (Conversation)
 * and agent (AgentConversation) collections — and return a unified,
 * paginated result sorted by lastActivityAt desc.
 *
 * Query params: `search` (required), `page`, `limit`
 *
 * GET /api/v1/conversations/show/archives/search?search=foo&page=1&limit=20
 */
export const searchArchivedConversations =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      const startTime = Date.now();
      try {
        const userId = req.user?.userId;
        const orgId = req.user?.orgId;

        if (!userId || !orgId) {
          throw new BadRequestError('User ID and Organization ID are required');
        }

        const deletedAgentKeys = await fetchDeletedAgentKeysForUser(appConfig, req);

        // ── Search parameter (required) ────────────────────────────────
        const rawSearch = req.query.search;
        if (!rawSearch || typeof rawSearch !== 'string' || !rawSearch.trim()) {
          throw new BadRequestError('search query parameter is required');
        }
        if (Array.isArray(rawSearch)) {
          throw new BadRequestError('Search parameter must be a string, not an array');
        }
        const searchValue = rawSearch.trim();
        validateNoXSS(searchValue, 'search parameter');
        validateNoFormatSpecifiers(searchValue, 'search parameter');
        if (searchValue.length > 1000) {
          throw new BadRequestError('Search parameter too long (max 1000 characters)');
        }
        const escapedSearch = searchValue.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

        // ── Pagination ─────────────────────────────────────────────────
        const { skip, limit, page } = getPaginationParams(req);

        logger.debug('Searching archived conversations (assistant + agent)', {
          requestId,
          userId,
          search: searchValue,
          page,
          limit,
        });

        // ── Shared base filter pieces ──────────────────────────────────
        const orgOid = new mongoose.Types.ObjectId(`${orgId}`);
        const userOid = new mongoose.Types.ObjectId(`${userId}`);

        const searchCondition = {
          $or: [
            { title: { $regex: escapedSearch, $options: 'i' } },
            { 'messages.content': { $regex: escapedSearch, $options: 'i' } },
          ],
        };

        // ── Assistant (Conversation) filter ─────────────────────────────
        const assistantFilter: any = {
          orgId: orgOid,
          isDeleted: false,
          isArchived: true,
          archivedBy: { $exists: true },
          $or: [
            { userId: userOid },
            {
              $and: [
                { 'sharedWith.userId': userOid },
                { isShared: true },
              ],
            },
          ],
          $and: [searchCondition],
        };

        // ── Agent (AgentConversation) filter ─────────────────────────────
        const agentFilter: any = {
          orgId: orgOid,
          isDeleted: false,
          isArchived: true,
          archivedBy: { $exists: true },
          userId: userOid,
          $and: [searchCondition],
        };
        if (deletedAgentKeys !== null) {
          agentFilter.agentKey = { $nin: deletedAgentKeys };
        }

        // ── Execute queries: $unionWith aggregation + per-source counts ──
        // $unionWith (Mongo 4.4+) merges both collections server-side so that
        // sort, skip, and limit are pushed to MongoDB — avoiding unbounded
        // in-memory loads when the search matches many documents.
        const [aggregateResult, assistantCount, agentCount] = await Promise.all([
          Conversation.aggregate([
            { $match: assistantFilter },
            { $addFields: { source: 'assistant' } },
            {
              $unionWith: {
                coll: AgentConversation.collection.name,
                pipeline: [
                  { $match: agentFilter },
                  { $addFields: { source: 'agent' } },
                ],
              },
            },
            { $sort: { lastActivityAt: -1 } },
            { $skip: skip },
            { $limit: limit },
            { $project: { messages: 0, __v: 0 } },
          ]),
          Conversation.countDocuments(assistantFilter),
          AgentConversation.countDocuments(agentFilter),
        ]);

        const totalCount = assistantCount + agentCount;

        // ── Tag and apply computed fields (bounded to `limit` docs) ──────
        const paginatedResults = aggregateResult.map((c: any) => ({
          ...addComputedFields(c as IConversation, userId),
          archivedAt: c.updatedAt,
          archivedBy: c.archivedBy,
          source: c.source as 'assistant' | 'agent',
          ...(c.source === 'agent' ? { agentKey: c.agentKey } : {}),
        }));

        const response = {
          conversations: paginatedResults,
          pagination: buildPaginationMetadata(totalCount, page, limit),
          summary: {
            totalMatches: totalCount,
            assistantMatches: assistantCount,
            agentMatches: agentCount,
            searchQuery: searchValue,
          },
          meta: {
            requestId,
            timestamp: new Date().toISOString(),
            duration: Date.now() - startTime,
          },
        };

        logger.debug('Successfully searched archived conversations', {
          requestId,
          totalMatches: totalCount,
          assistantMatches: assistantCount,
          agentMatches: agentCount,
          duration: Date.now() - startTime,
        });

        res.status(HTTP_STATUS.OK).json(response);
      } catch (error: any) {
        logger.error('Error searching archived conversations', {
          requestId,
          message: 'Error searching archived conversations',
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

        // Validate query parameter for XSS and format specifiers
        if (query && typeof query === 'string') {
          validateNoXSS(query, 'search query');
          validateNoFormatSpecifiers(query, 'search query');
        }

        logger.debug('Attempting to search', {
          requestId,
          query,
          limit,
          filters,
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

        if (!aiResponse || !aiResponse.data) {
          throw new InternalServerError('Failed to get response from AI service');
        }
        if (aiResponse.statusCode !== 200) {
          throw handleBackendError(
            {
              response: { status: aiResponse.statusCode, data: aiResponse.data },
            },
            'Search',
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
        if (!userIds || !Array.isArray(userIds) || userIds.length === 0) {
          throw new BadRequestError('userIds is required and must be a non-empty array');
        }

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

/////////////////////// AGENT ///////////////////////

export const createAgentTemplate =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      try {
        const orgId = req.user?.orgId;
        const userId = req.user?.userId;
        if (!orgId) {
          throw new BadRequestError('Organization ID is required');
        }
        if (!userId) {
          throw new BadRequestError('User ID is required');
        }

        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/template/create`,
          method: HttpMethod.POST,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
          body: req.body,
        };
        const aiCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponse = await aiCommand.execute();
        if (aiResponse && aiResponse.statusCode !== 200) {
          throw handleBackendError(aiResponse.data, 'Create Agent Template');
        }
        const agentTemplate = aiResponse.data;
        res.status(HTTP_STATUS.CREATED).json(agentTemplate);
      } catch (error: any) {
        logger.error('Error creating agent template', {
          requestId,
          message: 'Error creating agent template',
          error: error.message,
        });
        const backendError = handleBackendError(error, 'Create Agent Template');
        next(backendError);
      }
    };

export const getAgentTemplate =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      const { templateId } = req.params;
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }
      try {
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/template/${templateId}`,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
          method: HttpMethod.GET,
        };
        const aiCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponse = await aiCommand.execute();
        if (aiResponse && aiResponse.statusCode !== 200) {
          throw handleBackendError(aiResponse.data, 'Get Agent Template');
        }
        const agentTemplate = aiResponse.data;
        res.status(HTTP_STATUS.OK).json(agentTemplate);
      } catch (error: any) {
        logger.error('Error getting agent template', {
          requestId,
          message: 'Error getting agent template',
          error: error.message,
        });
        const backendError = handleBackendError(error, 'Get Agent Template');
        next(backendError);
      }
    };

export const listAgentTemplates =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      try {
        const orgId = req.user?.orgId;
        const userId = req.user?.userId;
        if (!orgId) {
          throw new BadRequestError('Organization ID is required');
        }
        if (!userId) {
          throw new BadRequestError('User ID is required');
        }
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/template/list`,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
          method: HttpMethod.GET,
        };
        const aiCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponse = await aiCommand.execute();
        if (aiResponse && aiResponse.statusCode !== 200) {
          throw handleBackendError(
            {
              response: { status: aiResponse.statusCode, data: aiResponse.data },
            },
            'List Agent Templates',
          );
        }
        const agentTemplates = aiResponse.data;
        res.status(HTTP_STATUS.OK).json(agentTemplates);
      } catch (error: any) {
        logger.error('Error getting agent templates', {
          requestId,
          message: 'Error getting agent templates',
          error: error.message,
        });
        const backendError = handleBackendError(error, 'List Agent Templates');
        next(backendError);
      }
    };

export const shareAgentTemplate =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      try {
        const orgId = req.user?.orgId;
        const userId = req.user?.userId;
        const templateId = req.params.templateId;
        if (!orgId) {
          throw new BadRequestError('Organization ID is required');
        }
        if (!userId) {
          throw new BadRequestError('User ID is required');
        }
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/share-template/${templateId}`,
          method: HttpMethod.POST,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
        };
        const aiCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponse = await aiCommand.execute();
        if (aiResponse && aiResponse.statusCode !== 200) {
          throw handleBackendError(aiResponse.data, 'Share Agent Template');
        }
        const agentTemplate = aiResponse.data;
        res.status(HTTP_STATUS.OK).json(agentTemplate);
      } catch (error: any) {
        logger.error('Error sharing agent template', {
          requestId,
          message: 'Error sharing agent template',
          error: error.message,
        });
        const backendError = handleBackendError(error, 'Share Agent Template');
        next(backendError);
      }
    };

export const updateAgentTemplate =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      try {
        const orgId = req.user?.orgId;
        const userId = req.user?.userId;
        const templateId = req.params.templateId;
        if (!orgId) {
          throw new BadRequestError('Organization ID is required');
        }
        if (!userId) {
          throw new BadRequestError('User ID is required');
        }
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/template/${templateId}`,
          method: HttpMethod.PUT,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
          body: req.body,
        };
        const aiCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponse = await aiCommand.execute();
        if (aiResponse && aiResponse.statusCode !== 200) {
          throw handleBackendError(aiResponse.data, 'Update Agent Template');
        }
        const agentTemplate = aiResponse.data;
        res.status(HTTP_STATUS.OK).json(agentTemplate);
      } catch (error: any) {
        logger.error('Error updating agent template', {
          requestId,
          message: 'Error updating agent template',
          error: error.message,
        });
        const backendError = handleBackendError(error, 'Update Agent Template');
        next(backendError);
      }
    };

export const deleteAgentTemplate =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      try {
        const orgId = req.user?.orgId;
        const userId = req.user?.userId;
        const templateId = req.params.templateId;
        if (!orgId) {
          throw new BadRequestError('Organization ID is required');
        }
        if (!userId) {
          throw new BadRequestError('User ID is required');
        }
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/template/${templateId}`,
          method: HttpMethod.DELETE,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
        };
        const aiCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponse = await aiCommand.execute();
        if (aiResponse && aiResponse.statusCode !== 200) {
          throw handleBackendError(aiResponse.data, 'Delete Agent Template');
        }
        const agentTemplate = aiResponse.data;
        res.status(HTTP_STATUS.OK).json(agentTemplate);
      } catch (error: any) {
        logger.error('Error deleting agent template', {
          requestId,
          message: 'Error deleting agent template',
          error: error.message,
        });
        const backendError = handleBackendError(error, 'Delete Agent Template');
        next(backendError);
      }
    };

export const createAgent =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      try {
        const orgId = req.user?.orgId;
        const userId = req.user?.userId;

        if (!orgId) {
          throw new BadRequestError('Organization ID is required');
        }
        if (!userId) {
          throw new BadRequestError('User ID is required');
        }

        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/create`,
          method: HttpMethod.POST,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
          body: req.body,
        };
        const aiCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponse = await aiCommand.execute();
        if (aiResponse && aiResponse.statusCode !== 200) {
          throw handleBackendError(aiResponse.data, 'Create Agent');
        }
        const agent = aiResponse.data;
        res.status(HTTP_STATUS.CREATED).json(agent);
      } catch (error: any) {
        logger.error('Error creating agent', {
          requestId,
          message: 'Error creating agent',
          error: error.message,
        });
        const backendError = handleBackendError(error, 'Create Agent');
        next(backendError);
      }
    };

export const getAgent =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      try {
        const orgId = req.user?.orgId;
        const userId = req.user?.userId;
        const agentKey = req.params.agentKey;
        if (!orgId) {
          throw new BadRequestError('Organization ID is required');
        }
        if (!userId) {
          throw new BadRequestError('User ID is required');
        }
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/${agentKey}`,
          method: HttpMethod.GET,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
        };
        const aiCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponse = await aiCommand.execute();
        if (aiResponse && aiResponse.statusCode !== 200) {
          throw handleBackendError(aiResponse.data, 'Get Agent');
        }
        const agent = aiResponse.data;
        res.status(HTTP_STATUS.OK).json(agent);
      } catch (error: any) {
        logger.error('Error getting agent', {
          requestId,
          message: 'Error getting agent',
          error: error.message,
        });
        const backendError = handleBackendError(error, 'Get Agent');
        next(backendError);
      }
    };

export const checkServiceAccountAccess =
  async (req: AuthenticatedServiceRequest, appConfig: AppConfig): Promise<boolean> => {
    const requestId = req.context?.requestId;
    try {

      const agentKey = req.params.agentKey;

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/agent/${agentKey}/internal/service-account`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (aiResponse && aiResponse.statusCode !== 200) {
        throw handleBackendError(aiResponse.data, 'Check Service Account Access');
      }
      const response = aiResponse.data as { isServiceAccount: boolean };
      const serviceAccountResponse = response.isServiceAccount;
      return serviceAccountResponse;
    } catch (error: any) {
      logger.error('Error checking service account access', {
        requestId,
        message: 'Error checking service account access',
        error: error.message,
      });
      return false;
    }
  };

export const listAgents =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      try {
        const orgId = req.user?.orgId;
        const userId = req.user?.userId;
        if (!orgId) {
          throw new BadRequestError('Organization ID is required');
        }
        if (!userId) {
          throw new BadRequestError('User ID is required');
        }
        // Forward pagination/search params
        const { page, limit, search, sort_by, sort_order } = req.query as Record<string, string | undefined>;
        const queryParams = new URLSearchParams();
        if (page) queryParams.set('page', page);
        if (limit) queryParams.set('limit', limit);
        if (search) queryParams.set('search', search);
        if (sort_by) queryParams.set('sort_by', sort_by);
        if (sort_order) queryParams.set('sort_order', sort_order);
        const qs = queryParams.toString();
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/${qs ? `?${qs}` : ''}`,
          method: HttpMethod.GET,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
        };
        const aiCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponse = await aiCommand.execute();
        if (aiResponse && aiResponse.statusCode !== 200) {
          res.status(HTTP_STATUS.OK).json({ success: true, agents: [], pagination: { currentPage: Number(page ?? 1), limit: Number(limit ?? 20), totalItems: 0, totalPages: 0, hasNext: false, hasPrev: false } });
          return;
        }
        res.status(HTTP_STATUS.OK).json(aiResponse.data);
      } catch (error: any) {
        logger.error('Error getting agents', {
          requestId,
          message: 'Error getting agents',
          error: error.message,
        });
        const backendError = handleBackendError(error, 'List Agents');
        next(backendError);
      }
    };

export const updateAgent =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      try {
        const orgId = req.user?.orgId;
        const userId = req.user?.userId;
        const agentKey = req.params.agentKey;
        if (!orgId) {
          throw new BadRequestError('Organization ID is required');
        }
        if (!userId) {
          throw new BadRequestError('User ID is required');
        }
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/${agentKey}`,
          method: HttpMethod.PUT,
          body: req.body,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
        };
        const aiCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponse = await aiCommand.execute();
        if (aiResponse && aiResponse.statusCode !== 200) {
          throw handleBackendError(aiResponse.data, 'Update Agent');
        }
        const agent = aiResponse.data;
        res.status(HTTP_STATUS.OK).json(agent);
      } catch (error: any) {
        logger.error('Error updating agent', {
          requestId,
          message: 'Error updating agent',
          error: error.message,
        });
        const backendError = handleBackendError(error, 'Update Agent');
        next(backendError);
      }
    };

export const deleteAgent =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      try {
        const orgId = req.user?.orgId;
        const userId = req.user?.userId;
        const agentKey = req.params.agentKey;
        if (!orgId) {
          throw new BadRequestError('Organization ID is required');
        }
        if (!userId) {
          throw new BadRequestError('User ID is required');
        }
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/${agentKey}`,
          method: HttpMethod.DELETE,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
        };
        const aiCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponse = await aiCommand.execute();
        if (aiResponse && aiResponse.statusCode !== 200) {
          throw handleBackendError(aiResponse.data, 'Delete Agent');
        }
        const agent = aiResponse.data;
        res.status(HTTP_STATUS.OK).json(agent);
      } catch (error: any) {
        logger.error('Error deleting agent', {
          requestId,
          message: 'Error deleting agent',
          error: error.message,
        });
        const backendError = handleBackendError(error, 'Delete Agent');
        next(backendError);
      }
    };

export const shareAgent =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      try {
        const orgId = req.user?.orgId;
        const userId = req.user?.userId;
        const agentKey = req.params.agentKey;
        if (!orgId) {
          throw new BadRequestError('Organization ID is required');
        }
        if (!userId) {
          throw new BadRequestError('User ID is required');
        }
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/${agentKey}/share`,
          method: HttpMethod.POST,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
          body: req.body,
        };
        const aiCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponse = await aiCommand.execute();
        if (aiResponse && aiResponse.statusCode !== 200) {
          throw handleBackendError(aiResponse.data, 'Share Agent');
        }
        const agent = aiResponse.data;
        res.status(HTTP_STATUS.OK).json(agent);
      } catch (error: any) {
        logger.error('Error sharing agent', {
          requestId,
          message: 'Error sharing agent',
          error: error.message,
        });
        const backendError = handleBackendError(error, 'Share Agent');
        next(backendError);
      }
    };

export const unshareAgent =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      try {
        const orgId = req.user?.orgId;
        const userId = req.user?.userId;
        const agentKey = req.params.agentKey;
        if (!orgId) {
          throw new BadRequestError('Organization ID is required');
        }
        if (!userId) {
          throw new BadRequestError('User ID is required');
        }
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/${agentKey}/unshare`,
          method: HttpMethod.POST,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
          body: req.body,
        };
        const aiCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponse = await aiCommand.execute();
        if (aiResponse && aiResponse.statusCode !== 200) {
          throw handleBackendError(aiResponse.data, 'Unshare Agent');
        }
        const agent = aiResponse.data;
        res.status(HTTP_STATUS.OK).json(agent);
      } catch (error: any) {
        logger.error('Error unsharing agent', {
          requestId,
          message: 'Error unsharing agent',
          error: error.message,
        });
        const backendError = handleBackendError(error, 'Unshare Agent');
        next(backendError);
      }
    };

export const updateAgentPermissions =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      try {
        const orgId = req.user?.orgId;
        const userId = req.user?.userId;
        const agentKey = req.params.agentKey;
        if (!orgId) {
          throw new BadRequestError('Organization ID is required');
        }
        if (!userId) {
          throw new BadRequestError('User ID is required');
        }
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/${agentKey}/permissions`,
          method: HttpMethod.PUT,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
          body: req.body,
        };
        const aiCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponse = await aiCommand.execute();
        if (aiResponse && aiResponse.statusCode !== 200) {
          throw handleBackendError(aiResponse.data, 'Update Agent Permissions');
        }
        const agent = aiResponse.data;
        res.status(HTTP_STATUS.OK).json(agent);
      } catch (error: any) {
        logger.error('Error updating agent permissions', {
          requestId,
          message: 'Error updating agent permissions',
          error: error.message,
        });
        const backendError = handleBackendError(
          error,
          'Update Agent Permissions',
        );
        next(backendError);
      }
    };

export const streamAgentConversation =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest | AuthenticatedServiceRequest, res: Response) => {
      const requestId = req.context?.requestId;
      const startTime = Date.now();
      let userId: Types.ObjectId | undefined;
      let orgId: Types.ObjectId | undefined;
      if ('user' in req) {
        const auth_req = req as AuthenticatedUserRequest;
        userId = auth_req.user?.userId;
        orgId = auth_req.user?.orgId;
      } else {
        const auth_req = req as AuthenticatedServiceRequest;
        userId = auth_req.tokenPayload?.userId;
        orgId = auth_req.tokenPayload?.orgId;
      }

      if (!userId) {
        throw new BadRequestError('User ID is required');
      }
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      const { agentKey } = req.params;

      let session: ClientSession | null = null;
      let savedConversation: IAgentConversationDocument | null = null;

      const modelInfo = extractModelInfo(req.body);

      if (!req.body.query) {
        throw new BadRequestError('Query is required');
      }

      try {
        // Set SSE headers
        res.writeHead(200, {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          Connection: 'keep-alive',
          'Access-Control-Allow-Origin': '*',
          'X-Accel-Buffering': 'no',
        });

        // Create initial conversation record (before `connected` so the client
        // can link the stream to a real conversationId for URL/sidebar/parallel tabs)
        const userQueryMessage = buildUserQueryMessage(req.body.query, req.body.appliedFilters);

        const userConversationData: Partial<IAgentConversation> = {
          orgId,
          userId,
          initiator: userId,
          title: req.body.query.slice(0, 100),
          messages: [userQueryMessage] as IMessageDocument[],
          lastActivityAt: Date.now(),
          status: CONVERSATION_STATUS.INPROGRESS,
          agentKey,
          modelInfo,
          // appliedFilters: req.body.appliedFilters || {},
        };

        // Start transaction if replica set is available
        if (rsAvailable) {
          session = await mongoose.startSession();
          await session.withTransaction(async () => {
            const conversation = new AgentConversation(userConversationData);
            savedConversation = (await conversation.save({
              session,
            })) as IAgentConversationDocument;
          });
        } else {
          const conversation = new AgentConversation(userConversationData);
          savedConversation =
            (await conversation.save()) as IAgentConversationDocument;
        }

        if (!savedConversation) {
          throw new InternalServerError('Failed to create conversation');
        }

        const newAgentConversationId = savedConversation._id?.toString() || '';

        logger.debug('Initial conversation created', {
          requestId,
          conversationId: savedConversation._id,
          userId,
          agentKey,
        });

        // Send initial connection event with conversationId and flush
        res.write(
          `event: connected\ndata: ${JSON.stringify({
            message: 'SSE connection established',
            conversationId: newAgentConversationId,
          })}\n\n`,
        );
        (res as any).flush?.();

        // Prepare AI payload
        const aiPayload = {
          query: req.body.query,
          quickMode: req.body.quickMode || false,
          previousConversations: req.body.previousConversations || [],
          recordIds: req.body.recordIds || [],
          filters: req.body.filters || {},
          tools: req.body.tools || [],
          chatMode: req.body.chatMode || 'auto',
          modelKey: req.body.modelKey || null,
          modelName: req.body.modelName || null,
          modelFriendlyName: req.body.modelFriendlyName || null,
          timezone: req.body.timezone || null,
          currentTime: req.body.currentTime || null,
          conversationId: newAgentConversationId || null,
        };

        logger.info('aiPayload', aiPayload);

        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/${agentKey}/chat/stream`,
          method: HttpMethod.POST,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
          body: aiPayload,
        };

        const stream = await startAIStream(
          aiCommandOptions,
          'Agent Chat Stream',
          { requestId, agentKey },
        );

        if (!stream) {
          throw new Error('Failed to get stream from AI service');
        }

        // Variables to collect complete response data
        let completeData: IAIResponse | null = null;
        let buffer = '';

        // Handle client disconnect
        req.on('close', () => {
          logger.debug('Client disconnected', { requestId });
          stream.destroy();
        });

        // Process SSE events, capture complete event, and forward non-complete events
        stream.on('data', (chunk: Buffer) => {
          const chunkStr = chunk.toString();
          buffer += chunkStr;

          // Look for complete events in the buffer
          const events = buffer.split('\n\n');
          buffer = events.pop() || ''; // Keep incomplete event in buffer

          let filteredChunk = '';

          for (const event of events) {
            if (event.trim()) {
              // Check if this is a complete event
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
                  completeData = JSON.parse(dataLine);
                  logger.debug('Captured complete event data from AI backend', {
                    requestId,
                    conversationId: savedConversation?._id,
                    answer: completeData?.answer,
                    citationsCount: completeData?.citations?.length || 0,
                    agentKey,
                  });
                  // DO NOT forward the complete event from AI backend
                  // We'll send our own complete event after processing
                } catch (parseError: any) {
                  logger.error('Failed to parse complete event data', {
                    requestId,
                    parseError: parseError.message,
                    dataLine,
                  });
                  // Forward the event if we can't parse it
                  filteredChunk += event + '\n\n';
                }
              } else {
                // Forward all non-complete events
                filteredChunk += event + '\n\n';
              }
            }
          }

          // Forward only non-complete events to client
          if (filteredChunk) {
            res.write(filteredChunk);
            (res as any).flush?.();
          }
        });

        stream.on('end', async () => {
          logger.debug('Stream ended successfully', { requestId });
          try {
            // Save the complete conversation data to database
            if (completeData && savedConversation) {
              const conversation = await saveCompleteAgentConversation(
                savedConversation,
                completeData,
                orgId?.toString(),
                session,
                modelInfo,
              );

              // Send the final conversation data in the same format as createConversation
              const responsePayload = {
                conversation: conversation,
                meta: {
                  requestId,
                  timestamp: new Date().toISOString(),
                  duration: Date.now() - startTime,
                },
              };

              // Send final response event with the complete conversation data
              res.write(
                `event: complete\ndata: ${JSON.stringify(responsePayload)}\n\n`,
              );

              logger.debug(
                'Conversation completed and saved, sent custom complete event',
                {
                  requestId,
                  conversationId: savedConversation._id,
                  duration: Date.now() - startTime,
                  agentKey,
                },
              );
            } else {
              // Mark as failed if no complete data received
              await markAgentConversationFailed(
                savedConversation as IAgentConversationDocument,
                'No complete response received from AI service',
                session,
              );

              // Send error event
              res.write(
                `event: error\ndata: ${JSON.stringify({
                  error: 'No complete response received from AI service',
                })}\n\n`,
              );
            }
          } catch (dbError: any) {
            logger.error('Failed to save complete conversation', {
              requestId,
              conversationId: savedConversation?._id,
              agentKey,
              error: dbError.message,
            });

            // Send error event
            res.write(
              `event: error\ndata: ${JSON.stringify({
                error: 'Failed to save conversation',
                details: dbError.message,
              })}\n\n`,
            );
          }

          res.end();
        });

        stream.on('error', async (error: Error) => {
          logger.error('Stream error', { requestId, error: error.message });
          try {
            // Mark conversation as failed
            if (savedConversation) {
              await markAgentConversationFailed(
                savedConversation as IAgentConversationDocument,
                `Stream error: ${error.message}`,
                session,
              );
            }
          } catch (dbError: any) {
            logger.error('Failed to mark conversation as failed', {
              requestId,
              conversationId: savedConversation?._id,
              agentKey,
              error: dbError.message,
            });
          }

          const errorEvent = `event: error\ndata: ${JSON.stringify({
            error: error.message || 'Stream error occurred',
            details: error.message,
          })}\n\n`;
          res.write(errorEvent);
          res.end();
        });
      } catch (error: any) {
        logger.error('Error in streamChat', { requestId, error: error.message });

        try {
          // Mark conversation as failed if it was created
          if (savedConversation) {
            await markAgentConversationFailed(
              savedConversation as IAgentConversationDocument,
              error.message || 'Internal server error',
              session,
            );
          }
        } catch (dbError: any) {
          logger.error('Failed to mark conversation as failed in catch block', {
            requestId,
            error: dbError.message,
          });
        }

        if (!res.headersSent) {
          res.writeHead(500, { 'Content-Type': 'text/event-stream' });
        }

        const errorEvent = `event: error\ndata: ${JSON.stringify({
          error: error.message || 'Internal server error',
          details: error.message,
        })}\n\n`;
        res.write(errorEvent);
        res.end();
      } finally {
        if (session) {
          session.endSession();
        }
      }
    };

export const streamAgentConversationInternal =
  (appConfig: AppConfig, keyValueStoreService?: KeyValueStoreService) =>
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        await hydrateScopedRequestAsUser(req, appConfig, keyValueStoreService);
        await streamAgentConversation(appConfig)(
          req as AuthenticatedUserRequest | AuthenticatedServiceRequest,
          res,
        );
      } catch (error) {
        next(error);
      }
    };

export const createAgentConversation =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      const startTime = Date.now();
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const { agentKey } = req.params;
      let session: ClientSession | null = null;
      let responseData: any;

      const modelInfo = extractModelInfo(req.body);

      // Validate query parameter for XSS and format specifiers
      if (req.body.query && typeof req.body.query === 'string') {
        validateNoXSS(req.body.query, 'query');
        validateNoFormatSpecifiers(req.body.query, 'query');

      } else if (!req.body.query) {
        throw new BadRequestError('Query is required');
      }

      // Helper function that contains the common conversation operations.
      async function createConversationUtil(
        session?: ClientSession | null,
      ): Promise<any> {
        const userQueryMessage = buildUserQueryMessage(req.body.query, req.body.appliedFilters);

        const userConversationData: Partial<IAgentConversation> = {
          orgId,
          userId,
          initiator: userId,
          title: req.body.query.slice(0, 100),
          messages: [userQueryMessage] as IMessageDocument[],
          lastActivityAt: Date.now(),
          status: CONVERSATION_STATUS.INPROGRESS,
          agentKey,
          modelInfo,
          // appliedFilters: req.body.appliedFilters || {},
        };

        const conversation = new AgentConversation(userConversationData);
        const savedConversation = session
          ? await conversation.save({ session })
          : ((await conversation.save()) as IAgentConversationDocument);
        if (!savedConversation) {
          throw new InternalServerError('Failed to create conversation');
        }

        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/${agentKey}/chat`,
          method: HttpMethod.POST,
          headers: req.headers as Record<string, string>,
          body: {
            query: req.body.query,
            previousConversations: req.body.previousConversations || [],
            recordIds: req.body.recordIds || [],
            filters: req.body.filters || {},
            // New fields for multi-model support
            modelKey: req.body.modelKey || null,
            modelName: req.body.modelName || null,
            modelFriendlyName: req.body.modelFriendlyName || null,
            chatMode: req.body.chatMode || 'auto',
            timezone: req.body.timezone || null,
            currentTime: req.body.currentTime || null,
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
            savedConversation.status = CONVERSATION_STATUS.FAILED as any;
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
            modelInfo,
          ) as IMessageDocument;
          // Add the AI message to the conversation
          savedConversation.messages.push(aiResponseMessage);
          savedConversation.lastActivityAt = Date.now();
          savedConversation.status = CONVERSATION_STATUS.COMPLETE as any; // Successful conversation

          const updatedConversation = session
            ? await savedConversation.save({ session })
            : await savedConversation.save();

          if (!updatedConversation) {
            throw new InternalServerError('Failed to update conversation');
          }
          const responseConversation = await attachPopulatedCitations(
            updatedConversation._id as mongoose.Types.ObjectId,
            updatedConversation.toObject() as IAgentConversation,
            citations,
            true,
            session,
          );
          return {
            conversation: {
              _id: updatedConversation._id,
              ...responseConversation,
            },
          };
        } catch (error: any) {
          // TODO: Add support for retry mechanism and generate response from retry
          // and append the response to the correct messageId

          savedConversation.status = CONVERSATION_STATUS.FAILED as any;
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

export const addMessageToAgentConversation =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      const startTime = Date.now();
      const { agentKey } = req.params;
      let session: ClientSession | null = null;

      const modelInfo = extractModelInfo(req.body);

      try {
        const userId = req.user?.userId;
        const orgId = req.user?.orgId;

        // Validate query parameter for XSS and format specifiers
        if (req.body.query && typeof req.body.query === 'string') {
          validateNoXSS(req.body.query, 'query');
          validateNoFormatSpecifiers(req.body.query, 'query');

        } else if (!req.body.query) {
          throw new BadRequestError('Query is required');
        }

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
          const conversation = await AgentConversation.findOne({
            _id: req.params.conversationId,
            agentKey,
            orgId,
            userId,
            isDeleted: false,
          });

          if (!conversation) {
            throw new NotFoundError('Conversation not found');
          }

          // Update status to processing when adding a new message
          conversation.status = CONVERSATION_STATUS.INPROGRESS as any;
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

          const userQueryMessage = buildUserQueryMessage(req.body.query, req.body.appliedFilters);
          // First, add the user message to the existing conversation
          conversation.messages.push(userQueryMessage as IMessageDocument);
          conversation.lastActivityAt = Date.now();

          const fieldsToUpdate: Array<keyof IAIModel> = [
            'modelKey',
            'modelName',
            'modelProvider',
            'chatMode',
          ];
          for (const field of fieldsToUpdate) {
            const value = req.body[field];
            if (value !== undefined && value !== null) {
              (conversation.modelInfo as IAIModel)[field] = value;
            }
          }

          // if (req.body.appliedFilters) {
          //   conversation.appliedFilters = req.body.appliedFilters;
          //   conversation.markModified('appliedFilters');
          // }

          // Save the user message to the existing conversation first
          const savedConversation = session
            ? await conversation.save({ session })
            : ((await conversation.save()) as IAgentConversationDocument);

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
            uri: `${appConfig.aiBackend}/api/v1/agent/${agentKey}/chat`,
            method: HttpMethod.POST,
            headers: req.headers as Record<string, string>,
            body: {
              query: req.body.query,
              previousConversations: previousConversations,
              filters: req.body.filters || {},
              // New fields for multi-model support
              modelKey: req.body.modelKey || null,
              modelName: req.body.modelName || null,
              chatMode: req.body.chatMode || 'auto',
              timezone: req.body.timezone || null,
              currentTime: req.body.currentTime || null,
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
              conversation.status = CONVERSATION_STATUS.FAILED as any;
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
              modelInfo,
            ) as IMessageDocument;
            // Add the AI message to the existing conversation
            savedConversation.messages.push(aiResponseMessage);
            savedConversation.lastActivityAt = Date.now();
            savedConversation.status = CONVERSATION_STATUS.COMPLETE as any;

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
            const responseConversation = await attachPopulatedCitations(
              updatedConversation._id as mongoose.Types.ObjectId,
              updatedConversation.toObject() as IAgentConversation,
              savedCitations,
              true,
              session,
            );
            return {
              conversation: responseConversation,
              recordsUsed: savedCitations.length, // or validated record count if needed
            };
          } catch (error: any) {
            // TODO: Add support for retry mechanism and generate response from retry
            // and append the response to the correct messageId

            // Update conversation status for general errors
            conversation.status = CONVERSATION_STATUS.FAILED as any;
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

export const addMessageStreamToAgentConversation =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest | AuthenticatedServiceRequest, res: Response) => {
      const requestId = req.context?.requestId;
      const startTime = Date.now();
      let userId: Types.ObjectId | undefined;
      let orgId: Types.ObjectId | undefined;
      if ('user' in req) {
        const auth_req = req as AuthenticatedUserRequest;
        userId = auth_req.user?.userId;
        orgId = auth_req.user?.orgId;
      } else {
        const auth_req = req as AuthenticatedServiceRequest;
        userId = auth_req.tokenPayload?.userId;
        orgId = auth_req.tokenPayload?.orgId;
      }

      if (!userId) {
        throw new BadRequestError('User ID is required');
      }
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      const { conversationId, agentKey } = req.params;

      let session: ClientSession | null = null;
      let existingConversation: IAgentConversationDocument | null = null;

      const modelInfo = extractModelInfo(req.body);

      if (!req.body.query) {
        throw new BadRequestError('Query is required');
      }

      // Helper function that contains the common conversation operations
      async function performAddMessageStream(
        session?: ClientSession | null,
      ): Promise<void> {
        // Get existing conversation
        const conversation = await AgentConversation.findOne({
          _id: conversationId,
          agentKey,
          orgId,
          userId,
          isDeleted: false,
        });

        if (!conversation) {
          throw new NotFoundError('Conversation not found');
        }

        // Update status to processing when adding a new message
        conversation.status = CONVERSATION_STATUS.INPROGRESS as any;
        conversation.failReason = undefined; // Clear previous error if any

        const fieldsToUpdate: Array<keyof IAIModel> = [
          'modelKey',
          'modelName',
          'modelProvider',
          'chatMode',
        ];
        for (const field of fieldsToUpdate) {
          const value = req.body[field];
          if (value !== undefined && value !== null) {
            (conversation.modelInfo as IAIModel)[field] = value;
          }
        }

        // First, add the user message to the existing conversation
        conversation.messages.push(
          buildUserQueryMessage(req.body.query, req.body.appliedFilters) as IMessageDocument,
        );
        conversation.lastActivityAt = Date.now();

        // if (req.body.appliedFilters) {
        //   conversation.appliedFilters = req.body.appliedFilters;
        //   conversation.markModified('appliedFilters');
        // }

        // Save the user message to the existing conversation first
        const savedConversation = session
          ? await conversation.save({ session })
          : ((await conversation.save()) as IAgentConversationDocument);

        if (!savedConversation) {
          throw new InternalServerError(
            'Failed to update conversation with user message',
          );
        }

        existingConversation = savedConversation;

        logger.debug('User message added to conversation', {
          requestId,
          conversationId: existingConversation._id,
          agentKey,
          userId,
        });
      }

      try {
        // Set SSE headers
        res.writeHead(200, {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          Connection: 'keep-alive',
          'Access-Control-Allow-Origin': '*',
          'X-Accel-Buffering': 'no',
        });

        // Send initial connection event and flush
        res.write(
          `event: connected\ndata: ${JSON.stringify({ message: 'SSE connection established' })}\n\n`,
        );
        (res as any).flush?.();

        logger.debug('Adding message to conversation via stream', {
          requestId,
          conversationId,
          userId,
          agentKey,
          query: req.body.query,
          filters: req.body.filters,
          timestamp: new Date().toISOString(),
        });

        // Get existing conversation and add user message
        if (rsAvailable) {
          session = await mongoose.startSession();
          await session.withTransaction(() => performAddMessageStream(session));
        } else {
          await performAddMessageStream();
        }

        if (!existingConversation) {
          throw new NotFoundError('Conversation not found');
        }

        // Format previous conversations for context (excluding the user message we just added)
        const previousConversations = formatPreviousConversations(
          (existingConversation as IConversationDocument).messages.slice(
            0,
            -1,
          ) as IMessage[],
        );

        // Prepare AI payload
        const aiPayload = {
          query: req.body.query,
          previousConversations: previousConversations,
          filters: req.body.filters || {},
          // New fields for multi-model support
          modelKey: req.body.modelKey || null,
          modelName: req.body.modelName || null,
          modelFriendlyName: req.body.modelFriendlyName || null,
          chatMode: req.body.chatMode || 'auto',
          timezone: req.body.timezone || null,
          currentTime: req.body.currentTime || null,
          conversationId: conversationId || null,
        };

        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/${agentKey}/chat/stream`,
          method: HttpMethod.POST,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
          body: aiPayload,
        };

        const stream = await startAIStream(
          aiCommandOptions,
          'Add Message Agent Stream',
          { requestId, agentKey },
        );

        if (!stream) {
          throw new Error('Failed to get stream from AI service');
        }

        // Variables to collect complete response data
        let completeData: IAIResponse | null = null;
        let buffer = '';

        // Handle client disconnect
        req.on('close', () => {
          logger.debug('Client disconnected', { requestId });
          stream.destroy();
        });

        // Process SSE events, capture complete event, and forward non-complete events
        stream.on('data', (chunk: Buffer) => {
          const chunkStr = chunk.toString();
          buffer += chunkStr;

          // Look for complete events in the buffer
          const events = buffer.split('\n\n');
          buffer = events.pop() || ''; // Keep incomplete event in buffer

          let filteredChunk = '';

          for (const event of events) {
            if (event.trim()) {
              // Check if this is a complete event
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
                  completeData = JSON.parse(dataLine);
                  logger.debug('Captured complete event data from AI backend', {
                    requestId,
                    conversationId: existingConversation?._id,
                    answer: completeData?.answer,
                    citationsCount: completeData?.citations?.length || 0,
                  });
                  // DO NOT forward the complete event from AI backend
                  // We'll send our own complete event after processing
                } catch (parseError: any) {
                  logger.error('Failed to parse complete event data', {
                    requestId,
                    parseError: parseError.message,
                    dataLine,
                  });
                  // Forward the event if we can't parse it
                  filteredChunk += event + '\n\n';
                }
              } else if (eventType === 'error' && dataLine) {
                try {
                  const errorData = JSON.parse(dataLine);
                  markAgentConversationFailed(
                    existingConversation as IAgentConversationDocument,
                    errorData.error,
                    session,
                  );
                  filteredChunk += event + '\n\n';
                } catch (parseError: any) {
                  logger.error('Failed to parse error event data', {
                    requestId,
                    parseError: parseError.message,
                    dataLine,
                  });
                  filteredChunk += event + '\n\n';
                }
              } else {
                // Forward all non-complete events
                filteredChunk += event + '\n\n';
              }
            }
          }

          // Forward only non-complete events to client
          if (filteredChunk) {
            res.write(filteredChunk);
            (res as any).flush?.();
          }
        });

        stream.on('end', async () => {
          logger.debug('Stream ended successfully', { requestId });
          try {
            // Save the AI response to the existing conversation
            if (completeData && existingConversation) {
              try {
                // Create and save citations
                const savedCitations: ICitation[] = await Promise.all(
                  completeData.citations?.map(async (citation: ICitation) => {
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

                // Build AI response message using existing utility
                const aiResponseMessage = buildAIResponseMessage(
                  { statusCode: 200, data: completeData },
                  savedCitations,
                  modelInfo,
                ) as IMessageDocument;

                // Add the AI message to the existing conversation
                existingConversation.messages.push(aiResponseMessage);
                existingConversation.lastActivityAt = Date.now();
                existingConversation.status = CONVERSATION_STATUS.COMPLETE as any;

                // Save the updated conversation with AI response
                const updatedConversation = session
                  ? await existingConversation.save({ session })
                  : ((await existingConversation.save()) as IAgentConversationDocument);

                if (!updatedConversation) {
                  throw new InternalServerError(
                    'Failed to update conversation with AI response',
                  );
                }

                // Return the updated conversation in the same format as addMessage
                const responseConversation = await attachPopulatedCitations(
                  updatedConversation._id as mongoose.Types.ObjectId,
                  updatedConversation.toObject() as IAgentConversation,
                  savedCitations,
                  true,
                  session,
                );

                // Send the final conversation data in the same format as addMessage
                const responsePayload = {
                  conversation: responseConversation,
                  recordsUsed: savedCitations.length,
                  meta: {
                    requestId,
                    timestamp: new Date().toISOString(),
                    duration: Date.now() - startTime,
                    recordsUsed: savedCitations.length,
                  },
                };

                // Send final response event with the complete conversation data
                res.write(
                  `event: complete\ndata: ${JSON.stringify(responsePayload)}\n\n`,
                );

                logger.debug(
                  'Message added and conversation updated, sent custom complete event',
                  {
                    requestId,
                    conversationId: existingConversation._id,
                    duration: Date.now() - startTime,
                  },
                );
              } catch (error: any) {
                // Update conversation status for general errors
                if (existingConversation) {
                  existingConversation.status = CONVERSATION_STATUS.FAILED as any;
                  existingConversation.failReason =
                    error.message || 'Unknown error occurred';

                  // Add error message using existing utility
                  const failedMessage =
                    buildAIFailureResponseMessage() as IMessageDocument;
                  existingConversation.messages.push(failedMessage);
                  existingConversation.lastActivityAt = Date.now();

                  const saveGeneralError = session
                    ? await existingConversation.save({ session })
                    : await existingConversation.save();

                  if (!saveGeneralError) {
                    logger.error(
                      'Failed to save conversation general error status',
                      {
                        requestId,
                        conversationId: existingConversation._id,
                      },
                    );
                  }
                }

                if (error.cause && error.cause.code === 'ECONNREFUSED') {
                  throw new InternalServerError(
                    AI_SERVICE_UNAVAILABLE_MESSAGE,
                    error,
                  );
                }
                throw error;
              }
            } else {
              // Mark as failed if no complete data received
              if (existingConversation) {
                existingConversation.status = CONVERSATION_STATUS.FAILED as any;
                existingConversation.failReason =
                  'No complete response received from AI service';
                existingConversation.lastActivityAt = Date.now();

                const savedWithError = session
                  ? await existingConversation.save({ session })
                  : ((await existingConversation.save()) as IAgentConversationDocument);

                if (!savedWithError) {
                  logger.error('Failed to save conversation error state', {
                    requestId,
                    conversationId: existingConversation._id,
                  });
                }
              }

              // Send error event
              res.write(
                `event: error\ndata: ${JSON.stringify({
                  error: 'No complete response received from AI service',
                })}\n\n`,
              );
            }
          } catch (dbError: any) {
            logger.error('Failed to save AI response to conversation', {
              requestId,
              conversationId: existingConversation?._id,
              error: dbError.message,
            });

            // Send error event
            res.write(
              `event: error\ndata: ${JSON.stringify({
                error: 'Failed to save AI response',
                details: dbError.message,
              })}\n\n`,
            );
          }

          res.end();
        });

        stream.on('error', async (error: Error) => {
          logger.error('Stream error', { requestId, error: error.message });
          try {
            if (existingConversation) {
              markAgentConversationFailed(
                existingConversation as IAgentConversationDocument,
                error.message,
                session,
              );
            }
          } catch (dbError: any) {
            logger.error('Failed to mark conversation as failed', {
              requestId,
              conversationId: existingConversation?._id,
              agentKey,
              error: dbError.message,
            });
          }

          const errorEvent = `event: error\ndata: ${JSON.stringify({
            error: error.message || 'Stream error occurred',
            details: error.message,
          })}\n\n`;
          res.write(errorEvent);
          res.end();
        });
      } catch (error: any) {
        logger.error('Error in addMessageStream', {
          requestId,
          conversationId,
          agentKey,
          error: error.message,
        });

        try {
          // Mark conversation as failed if it exists
          if (existingConversation) {
            (existingConversation as IAgentConversationDocument).status =
              CONVERSATION_STATUS.FAILED as any;
            (existingConversation as IAgentConversationDocument).failReason =
              error.message || 'Internal server error';

            // Add error message using existing utility
            const failedMessage =
              buildAIFailureResponseMessage() as IMessageDocument;
            (existingConversation as IAgentConversationDocument).messages.push(
              failedMessage,
            );
            (existingConversation as IAgentConversationDocument).lastActivityAt =
              Date.now();

            const saveGeneralError = session
              ? await (existingConversation as IAgentConversationDocument).save({
                session,
              })
              : await (existingConversation as IAgentConversationDocument).save();

            if (!saveGeneralError) {
              logger.error(
                'Failed to save conversation general error status in catch block',
                {
                  requestId,
                  conversationId: (
                    existingConversation as IAgentConversationDocument
                  )._id,
                },
              );
            }
          }
        } catch (dbError: any) {
          logger.error('Failed to mark conversation as failed in catch block', {
            requestId,
            conversationId,
            agentKey,
            error: dbError.message,
          });
        }

        if (!res.headersSent) {
          res.writeHead(500, { 'Content-Type': 'text/event-stream' });
        }

        const errorEvent = `event: error\ndata: ${JSON.stringify({
          error: error.message || 'Internal server error',
          details: error.message,
        })}\n\n`;
        res.write(errorEvent);
        res.end();
      } finally {
        if (session) {
          session.endSession();
        }
      }
    };

export const addMessageStreamToAgentConversationInternal =
  (appConfig: AppConfig, keyValueStoreService?: KeyValueStoreService) =>
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        await hydrateScopedRequestAsUser(req, appConfig, keyValueStoreService);
        await addMessageStreamToAgentConversation(appConfig)(
          req as AuthenticatedUserRequest | AuthenticatedServiceRequest,
          res,
        );
      } catch (error) {
        next(error);
      }
    };

export const regenerateAgentAnswers =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response) => {
      await regenerateAnswersInternal(appConfig, req, res, {
        conversationModel: AgentConversation,
        buildQueryFilter: (conversationId, orgId, userId, agentKey) => ({
          _id: conversationId,
          agentKey,
          orgId,
          userId,
          isDeleted: false,
          $or: [
            { initiator: userId },
            { 'sharedWith.userId': userId },
            { isShared: true },
          ],
        }),
        buildAIEndpoint: (appConfig, agentKey) =>
          `${appConfig.aiBackend}/api/v1/agent/${agentKey}/chat/stream`,
      });
    };

export const getAllAgentConversations = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    const { conversationId, agentKey } = req.params;
    logger.debug('Fetching conversations', {
      requestId,
      message: 'Fetching conversations',
      userId,
      agentKey,
      conversationId: req.params.conversationId,
      query: req.query,
    });

    const { skip, limit, page } = getPaginationParams(req);
    const filter = {
      ...buildAgentConversationFilter(
        req,
        orgId,
        userId,
        agentKey as string,
        conversationId as string,
      ),
      // Sidebar / chat list: omit archived threads (archived view uses a dedicated route).
      isArchived: { $ne: true },
    };
    const sortOptions = buildSortOptions(req);

    // sharedWith Me Conversation
    const sharedWithMeFilter = {
      ...buildAgentSharedWithMeFilter(req, userId, agentKey as string),
      isArchived: { $ne: true },
    };

    // Execute query
    const [conversations, totalCount, sharedWithMeConversations] =
      await Promise.all([
        AgentConversation.find(filter)
          .sort(sortOptions as any)
          .skip(skip)
          .limit(limit)
          .select('-__v')
          .select('-messages')
          .lean()
          .exec(),
        AgentConversation.countDocuments(filter),
        AgentConversation.find(sharedWithMeFilter)
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
        conversation as IAgentConversation,
        userId,
      );
      return {
        ...conversationWithComputedFields,
      };
    });
    const processedSharedWithMeConversations = sharedWithMeConversations.map(
      (sharedWithMeConversation: any) => {
        const conversationWithComputedFields = addComputedFields(
          sharedWithMeConversation as IAgentConversation,
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

export const getAgentConversationById = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  const { conversationId, agentKey } = req.params;
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
    const baseFilter = buildAgentConversationFilter(
      req,
      orgId,
      userId,
      agentKey as string,
      conversationId as string,
    );

    // Build message filter
    const messageFilter = buildMessageFilter(req);

    // Get sort options for messages
    const messageSortOptions = buildMessageSortOptions(
      sortBy as string,
      sortOrder as string,
    );

    const countResult = await AgentConversation.aggregate([
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
    const conversationWithMessages = await AgentConversation.findOne(baseFilter)
      .select({
        messages: { $slice: [skip, effectiveLimit] },
        title: 1,
        initiator: 1,
        createdAt: 1,
        isShared: 1,
        sharedWith: 1,
        status: 1,
        failReason: 1,
        modelInfo: 1,
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
      conversationWithMessages as unknown as IAgentConversationDocument,
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

export const deleteAgentConversationById = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  const { conversationId, agentKey } = req.params;
  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    const conversation = await deleteAgentConversation(
      conversationId as string,
      agentKey as string,
      userId as string,
      orgId as string,
    );

    res.status(200).json({
      message: 'Conversation deleted successfully',
      conversation,
    });
  } catch (error: any) {
    logger.error('Error deleting conversation', {
      requestId,
      conversationId,
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });
    next(error);
  }
};

export const archiveAgentConversation = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  try {
    const { conversationId, agentKey } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Attempting to archive agent conversation', {
      requestId,
      message: 'Attempting to archive agent conversation',
      conversationId,
      agentKey,
      userId,
      timestamp: new Date().toISOString(),
    });

    async function performArchive(s?: ClientSession | null) {
      const conversation = await AgentConversation.findOne(
        {
          _id: conversationId,
          userId,
          orgId,
          agentKey,
          isDeleted: false,
        },
        null,
        { session: s },
      );

      if (!conversation) {
        throw new NotFoundError('Agent conversation not found');
      }

      if (conversation.isArchived) {
        throw new BadRequestError('Agent conversation already archived');
      }

      const updated = await AgentConversation.findByIdAndUpdate(
        conversationId,
        {
          $set: {
            isArchived: true,
            archivedBy: userId,
            lastActivityAt: Date.now(),
          },
        },
        { new: true, session: s, runValidators: true },
      ).exec();

      if (!updated) {
        throw new InternalServerError('Failed to archive agent conversation');
      }
      return updated;
    }

    let updated: any = null;
    if (rsAvailable) {
      session = await mongoose.startSession();
      session.startTransaction();
      updated = await performArchive(session);
      await session.commitTransaction();
    } else {
      updated = await performArchive();
    }

    logger.debug('Agent conversation archived successfully', {
      requestId,
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json({
      id: updated._id,
      status: 'archived',
      archivedBy: userId,
      archivedAt: updated.updatedAt,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    });
  } catch (error: any) {
    logger.error('Error archiving agent conversation', {
      requestId,
      message: 'Error archiving agent conversation',
      error: error.message,
      stack: error.stack,
    });
    next(error);
  } finally {
    if (session) await session.endSession();
  }
};

export const unarchiveAgentConversation = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  let session: ClientSession | null = null;
  try {
    const { conversationId, agentKey } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Attempting to unarchive agent conversation', {
      requestId,
      message: 'Attempting to unarchive agent conversation',
      conversationId,
      agentKey,
      userId,
      timestamp: new Date().toISOString(),
    });

    async function performUnarchive(s?: ClientSession | null) {
      const conversation = await AgentConversation.findOne(
        {
          _id: conversationId,
          userId,
          orgId,
          agentKey,
          isDeleted: false,
        },
        null,
        { session: s },
      );

      if (!conversation) {
        throw new NotFoundError('Agent conversation not found');
      }

      if (!conversation.isArchived) {
        throw new BadRequestError('Agent conversation is not archived');
      }

      const updated = await AgentConversation.findByIdAndUpdate(
        conversationId,
        {
          $set: {
            isArchived: false,
            archivedBy: null,
            lastActivityAt: Date.now(),
          },
        },
        { new: true, session: s, runValidators: true },
      ).exec();

      if (!updated) {
        throw new InternalServerError('Failed to unarchive agent conversation');
      }
      return updated;
    }

    let updated: any = null;
    if (rsAvailable) {
      session = await mongoose.startSession();
      session.startTransaction();
      updated = await performUnarchive(session);
      await session.commitTransaction();
    } else {
      updated = await performUnarchive();
    }

    logger.debug('Agent conversation unarchived successfully', {
      requestId,
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json({
      id: updated._id,
      status: 'unarchived',
      unarchivedBy: userId,
      unarchivedAt: updated.updatedAt,
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    });
  } catch (error: any) {
    logger.error('Error unarchiving agent conversation', {
      requestId,
      message: 'Error unarchiving agent conversation',
      error: error.message,
      stack: error.stack,
    });
    next(error);
  } finally {
    if (session) await session.endSession();
  }
};

export const listAllArchivesAgentConversation = () =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    try {
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const { agentKey } = req.params;

      logger.debug('Fetching all archived agent conversations', {
        requestId,
        userId,
        agentKey,
      });

      const { skip, limit, page } = getPaginationParams(req);
      const filter = {
        ...buildAgentConversationFilter(req, orgId, userId, agentKey as string),
        isArchived: true,
        archivedBy: { $exists: true },
      };
      const sortOptions = buildSortOptions(req);

      const [conversations, totalCount] = await Promise.all([
        AgentConversation.find(filter)
          .sort(sortOptions as any)
          .skip(skip)
          .limit(limit)
          .select('-__v')
          .select('-messages')
          .lean()
          .exec(),
        AgentConversation.countDocuments(filter),
      ]);

      const processedConversations = conversations.map((conversation: any) => ({
        ...addComputedFields(conversation as IAgentConversation, userId),
        archivedAt: conversation.updatedAt,
        archivedBy: conversation.archivedBy,
      }));

      logger.debug('Successfully fetched archived agent conversations', {
        requestId,
        count: conversations.length,
        totalCount,
        duration: Date.now() - startTime,
      });

      res.status(HTTP_STATUS.OK).json({
        conversations: processedConversations,
        pagination: buildPaginationMetadata(totalCount, page, limit),
        filters: buildFiltersMetadata(filter, req.query),
        summary: {
          totalArchived: totalCount,
          oldestArchive: processedConversations[0]?.archivedAt,
          newestArchive: processedConversations[processedConversations.length - 1]?.archivedAt,
        },
        meta: {
          requestId,
          timestamp: new Date().toISOString(),
          duration: Date.now() - startTime,
        },
      });
    } catch (error: any) {
      logger.error('Error fetching archived agent conversations', {
        requestId,
        message: 'Error fetching archived agent conversations',
        error: error.message,
        stack: error.stack,
        duration: Date.now() - startTime,
      });
      next(error);
    }
  };

export const updateAgentConversationTitle = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => {
  const requestId = req.context?.requestId;
  const startTime = Date.now();
  try {
    const { conversationId, agentKey } = req.params;
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;

    logger.debug('Attempting to update agent conversation title', {
      requestId,
      message: 'Attempting to update agent conversation title',
      conversationId,
      agentKey,
      userId,
      title: req.body.title,
      timestamp: new Date().toISOString(),
    });

    const conversation = await AgentConversation.findOne({
      _id: conversationId,
      orgId,
      userId,
      agentKey,
      isDeleted: false,
    });

    if (!conversation) {
      throw new NotFoundError('Agent conversation not found');
    }

    const title = req.body.title?.trim();
    if (!title) {
      throw new BadRequestError('Title is required and cannot be empty');
    }

    conversation.title = title;
    await conversation.save();

    logger.debug('Agent conversation title updated successfully', {
      requestId,
      message: 'Agent conversation title updated successfully',
      conversationId,
      duration: Date.now() - startTime,
    });

    res.status(HTTP_STATUS.OK).json({
      conversation: {
        ...conversation.toObject(),
        title: conversation.title,
      },
      meta: {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      },
    });
  } catch (error: any) {
    logger.error('Error updating agent conversation title', {
      requestId,
      message: 'Error updating agent conversation title',
      error: error.message,
      stack: error.stack,
      duration: Date.now() - startTime,
    });
    next(error);
  }
};

export const getAvailableTools =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      try {
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/tools/`,
          method: HttpMethod.GET,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
        };
        const aiCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponse = await aiCommand.execute();
        if (aiResponse && aiResponse.statusCode !== 200) {
          throw handleBackendError(aiResponse.data, 'Get Available Tools');
        }
        const tools = aiResponse.data;
        res.status(HTTP_STATUS.OK).json(tools);
      } catch (error: any) {
        logger.error('Error getting available tools', {
          requestId,
          message: 'Error getting available tools',
          error: error.message,
        });
        const backendError = handleBackendError(error, 'Get Available Tools');
        next(backendError);
      }
    };

export const getAgentPermissions =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      const { agentKey } = req.params;

      try {
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/agent/${agentKey}/permissions`,
          method: HttpMethod.GET,
          headers: {
            ...(req.headers as Record<string, string>),
            'Content-Type': 'application/json',
          },
        };
        const aiCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponse = await aiCommand.execute();
        if (aiResponse && aiResponse.statusCode !== 200) {
          throw handleBackendError(aiResponse.data, 'Get Agent Permissions');
        }
        const permissions = aiResponse.data;
        res.status(200).json(permissions);
      } catch (error: any) {
        logger.error('Error getting agent permissions', {
          requestId,
          message: 'Error getting agent permissions',
          error: error.message,
        });
        const backendError = handleBackendError(error, 'Get Agent Permissions');
        next(backendError);
      }
    };


/**
 * GET /api/v1/agents/conversations/show/archives
 * Returns archived agent conversations for the current user, grouped by agentKey.
 *
 * Supports agent-level pagination via `agentPage` and `agentLimit` query params
 * (defaults: page 1, limit {@link AGENT_ARCHIVES_INITIAL_AGENT_LIMIT}).
 *
 * Each group contains the first {@link AGENT_ARCHIVES_INITIAL_CHAT_LIMIT} conversations plus
 * a totalCount so the frontend can render a "More Chats" affordance without extra round-trips.
 *
 * Conversations whose agent instance is soft-deleted in the AI backend are omitted
 * (Mongo `agentKey` not in the user's deleted-agent list from the AI backend).
 */
export const listAllAgentsArchivedConversationsGrouped =
  (appConfig: AppConfig) =>
    async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      const requestId = req.context?.requestId;
      const startTime = Date.now();
      try {
        const userId = req.user?.userId;
        const orgId = req.user?.orgId;

        if (!userId || !orgId) {
          throw new BadRequestError('User ID and Organization ID are required');
        }

        const deletedAgentKeys = await fetchDeletedAgentKeysForUser(appConfig, req);

        // Agent-level pagination params
        const agentPage = Math.max(1, parseInt(req.query.agentPage as string, 10) || 1);
        const agentLimit = Math.min(
          100,
          Math.max(1, parseInt(req.query.agentLimit as string, 10) || AGENT_ARCHIVES_INITIAL_AGENT_LIMIT),
        );
        const agentSkip = (agentPage - 1) * agentLimit;

        logger.debug('Fetching all agents archived conversations (grouped)', {
          requestId,
          userId,
          agentPage,
          agentLimit,
        });

        const filter: Record<string, unknown> = {
          orgId: new mongoose.Types.ObjectId(`${orgId}`),
          $or: [{ userId: new mongoose.Types.ObjectId(`${userId}`) }],
          isArchived: true,
          archivedBy: { $exists: true },
          isDeleted: false,
        };
        if (deletedAgentKeys !== null) {
          filter.agentKey = { $nin: deletedAgentKeys };
        }

        const aggregationResult = await AgentConversation.aggregate([
          { $match: filter },
          // Sort by lastActivityAt desc — must match the per-agent archive endpoint's default sort
          // so that the initial 5 chats here align with page 1 of the per-agent pagination
          { $sort: { lastActivityAt: -1 as const } },
          // Exclude heavy fields before grouping
          { $project: { __v: 0, messages: 0 } },
          {
            $group: {
              _id: '$agentKey',
              conversations: { $push: '$$ROOT' },
              totalCount: { $sum: 1 },
              latestActivity: { $first: '$lastActivityAt' },
            },
          },
          // Sort agent groups by most recent activity
          { $sort: { latestActivity: -1 as const } },
          {
            $facet: {
              metadata: [{ $count: 'totalAgentCount' }],
              data: [
                { $skip: agentSkip },
                { $limit: agentLimit },
                {
                  $project: {
                    agentKey: '$_id',
                    conversations: { $slice: ['$conversations', AGENT_ARCHIVES_INITIAL_CHAT_LIMIT] },
                    totalCount: 1,
                  },
                },
              ],
            },
          },
        ]);

        const totalAgentCount = aggregationResult[0]?.metadata[0]?.totalAgentCount ?? 0;
        const rawGroups = aggregationResult[0]?.data ?? [];

        // Apply computed fields (isOwner, accessLevel) to sliced conversations
        const groups = rawGroups.map((g: any) => ({
          agentKey: g.agentKey,
          conversations: g.conversations.map((c: any) => ({
            ...addComputedFields(c as IAgentConversation, userId),
            archivedAt: c.updatedAt,
            archivedBy: c.archivedBy,
          })),
          pagination: buildPaginationMetadata(g.totalCount, 1, AGENT_ARCHIVES_INITIAL_CHAT_LIMIT),
        }));

        logger.debug('Successfully fetched grouped archived agent conversations', {
          requestId,
          agentGroupCount: groups.length,
          totalAgentCount,
          duration: Date.now() - startTime,
        });

        res.status(HTTP_STATUS.OK).json({
          groups,
          agentPagination: {
            page: agentPage,
            limit: agentLimit,
            totalCount: totalAgentCount,
            totalPages: Math.ceil(totalAgentCount / agentLimit),
            hasNextPage: agentPage * agentLimit < totalAgentCount,
            hasPrevPage: agentPage > 1,
          },
          meta: {
            requestId,
            timestamp: new Date().toISOString(),
            duration: Date.now() - startTime,
          },
        });
      } catch (error: any) {
        logger.error('Error fetching grouped archived agent conversations', {
          requestId,
          error: error.message,
          stack: error.stack,
          duration: Date.now() - startTime,
        });
        next(error);
      }
    };
