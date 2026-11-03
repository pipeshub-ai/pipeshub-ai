import { Request, Response } from 'express';

import { AppConfig } from '../../tokens_manager/config/config';
import {
  ChatModel,
  chatModelsFrom,
  findChatModel,
  toOpenAIModel,
} from '../utils/chat-models';
import { ChatRunError, runChat } from '../utils/chat-run';
import {
  asString,
  chatAnswerChunks,
  chatChunk,
  chatCompletion,
  chatMessagesToTurns,
  conversationIdFromResponseId,
  isJson,
  Json,
  newChatCompletionId,
  newMessageItemId,
  newResponseId,
  openAIError,
  responseAnswerEvents,
  responseMessageItem,
  responseObject,
  responsesInputToTurns,
  turnsToQuery,
} from '../utils/openai-format';

type Handler = (req: Request, res: Response) => Promise<void>;

const KEEPALIVE_MS = 10_000;

export interface OpenAICompatDeps {
  appConfig: AppConfig;
  /** The stored AI models config (decrypted), or null when none is saved. */
  readModels: () => Promise<unknown>;
}

function flush(res: Response): void {
  (res as Response & { flush?: () => void }).flush?.();
}

function sendError(
  res: Response,
  status: number,
  message: string,
  code: string,
  param: string | null = null,
): void {
  const type = status >= 500 ? 'server_error' : 'invalid_request_error';
  res.status(status).json(openAIError(message, code, type, param));
}

function sendRunError(res: Response, error: unknown): void {
  if (error instanceof ChatRunError) {
    const param =
      error.code === 'model_not_found'
        ? 'model'
        : error.code === 'previous_response_not_found'
          ? 'previous_response_id'
          : null;
    sendError(res, error.status, error.message, error.code, param);
  } else {
    sendError(res, 502, 'The chat run failed', 'server_error');
  }
}

function runErrorParts(error: unknown): { code: string; message: string } {
  return error instanceof ChatRunError
    ? { code: error.code, message: error.message }
    : { code: 'server_error', message: 'The chat run failed' };
}

/** Aborts the chat run when the client goes away before the answer. */
function abortOnClose(res: Response): AbortController {
  const controller = new AbortController();
  res.on('close', () => {
    if (!res.writableEnded) controller.abort();
  });
  return controller;
}

/** Opens the SSE response; the returned function writes a throttled keepalive comment. */
function openStream(res: Response): () => void {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  });
  flush(res);
  let last = Date.now();
  // The answer is only written at the end, so idle proxies would otherwise drop a long run.
  return () => {
    if (Date.now() - last < KEEPALIVE_MS) return;
    last = Date.now();
    res.write(': keepalive\n\n');
    flush(res);
  };
}

function writeData(res: Response, payload: unknown): void {
  res.write(`data: ${JSON.stringify(payload)}\n\n`);
  flush(res);
}

function requestBody(req: Request): Json {
  const body: unknown = req.body;
  return isJson(body) ? body : {};
}

async function chatModels(deps: OpenAICompatDeps): Promise<ChatModel[]> {
  return chatModelsFrom(await deps.readModels());
}

/** The requested model, or null after answering 400/404 for a missing or unknown one. */
async function resolveModel(
  deps: OpenAICompatDeps,
  body: Json,
  res: Response,
): Promise<ChatModel | null> {
  const id = asString(body.model).trim();
  if (id === '') {
    sendError(
      res,
      400,
      'model is required: pass a model id from GET /models',
      'missing_required_parameter',
      'model',
    );
    return null;
  }
  const model = findChatModel(await chatModels(deps), id);
  if (model === undefined) {
    sendError(
      res,
      404,
      `The model '${id}' does not exist`,
      'model_not_found',
      'model',
    );
    return null;
  }
  return model;
}

export const listModels =
  (deps: OpenAICompatDeps): Handler =>
  async (_req, res) => {
    try {
      const data = (await chatModels(deps)).map(toOpenAIModel);
      res.status(200).json({ object: 'list', data });
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const retrieveModel =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const params = req.params as Record<string, string | undefined>;
    // Ids can hold a slash (`provider/name`), so the route captures the rest of the path.
    const id = params['0'] ?? params.modelId ?? '';
    try {
      const found = findChatModel(await chatModels(deps), id);
      if (found === undefined) {
        sendError(
          res,
          404,
          `The model '${id}' does not exist`,
          'model_not_found',
          'model',
        );
        return;
      }
      res.status(200).json(toOpenAIModel(found));
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const createChatCompletion =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const body = requestBody(req);
    const query = turnsToQuery(chatMessagesToTurns(body.messages));
    if (query === '') {
      sendError(
        res,
        400,
        'messages must include a user message with text',
        'invalid_request',
        'messages',
      );
      return;
    }
    let chatModel: ChatModel | null;
    try {
      chatModel = await resolveModel(deps, body, res);
    } catch (error) {
      sendRunError(res, error);
      return;
    }
    if (chatModel === null) return;
    const model = chatModel.id;
    const stream = body.stream === true;
    const id = newChatCompletionId();
    const controller = abortOnClose(res);
    let keepalive: (() => void) | null = null;

    try {
      const answer = await runChat({
        baseUrl: deps.appConfig.esBackend,
        frontendUrl: deps.appConfig.frontendUrl,
        authorization: req.headers.authorization ?? '',
        model: chatModel,
        query,
        signal: controller.signal,
        onFrame: () => keepalive?.(),
        onConversation: () => {
          if (!stream) return;
          keepalive = openStream(res);
          writeData(
            res,
            chatChunk(id, model, { role: 'assistant', content: '' }),
          );
        },
      });
      if (!stream) {
        res.status(200).json(chatCompletion(id, model, answer));
        return;
      }
      // shortcut: the answer is sent as one delta when the run finishes, because the
      // assistant rewrites streamed text (citations, narration before tool calls);
      // stream tokens live once the chat stream exposes a final-text-only channel.
      for (const chunk of chatAnswerChunks(id, model, answer)) {
        writeData(res, chunk);
      }
      res.write('data: [DONE]\n\n');
      res.end();
    } catch (error) {
      if (!res.headersSent) {
        sendRunError(res, error);
      } else if (!res.writableEnded) {
        const { code, message } = runErrorParts(error);
        writeData(res, openAIError(message, code, 'server_error'));
        res.end();
      }
    }
  };

export const createResponse =
  (deps: OpenAICompatDeps): Handler =>
  async (req, res) => {
    const body = requestBody(req);
    const previousResponseId = asString(body.previous_response_id) || null;
    const conversationId =
      previousResponseId === null
        ? null
        : conversationIdFromResponseId(previousResponseId);
    if (previousResponseId !== null && conversationId === null) {
      sendError(
        res,
        400,
        `Previous response '${previousResponseId}' was not created by this server`,
        'invalid_request',
        'previous_response_id',
      );
      return;
    }
    const instructions = asString(body.instructions) || null;
    const query = turnsToQuery(
      responsesInputToTurns(body.input),
      instructions ?? undefined,
    );
    if (query === '') {
      sendError(
        res,
        400,
        'input must include a user message with text',
        'invalid_request',
        'input',
      );
      return;
    }
    let chatModel: ChatModel | null;
    try {
      chatModel = await resolveModel(deps, body, res);
    } catch (error) {
      sendRunError(res, error);
      return;
    }
    if (chatModel === null) return;
    const model = chatModel.id;

    const stream = body.stream === true;
    const createdAt = Math.floor(Date.now() / 1000);
    const itemId = newMessageItemId();
    const extra = { instructions, previousResponseId };
    const controller = abortOnClose(res);
    let responseId = '';
    let sequence = 0;
    let keepalive: (() => void) | null = null;
    const emit = (event: Json): void => {
      const payload = JSON.stringify({ ...event, sequence_number: sequence++ });
      res.write(`event: ${asString(event.type)}\ndata: ${payload}\n\n`);
      flush(res);
    };

    try {
      const answer = await runChat({
        baseUrl: deps.appConfig.esBackend,
        frontendUrl: deps.appConfig.frontendUrl,
        authorization: req.headers.authorization ?? '',
        model: chatModel,
        query,
        conversationId,
        signal: controller.signal,
        onFrame: () => keepalive?.(),
        onConversation: (id) => {
          responseId = newResponseId(id);
          if (!stream) return;
          keepalive = openStream(res);
          const response = responseObject(
            responseId,
            model,
            createdAt,
            'in_progress',
            [],
            extra,
          );
          emit({ type: 'response.created', response });
          emit({ type: 'response.in_progress', response });
        },
      });
      if (responseId === '') responseId = newResponseId(answer.conversationId);
      const done = responseObject(
        responseId,
        model,
        createdAt,
        'completed',
        [responseMessageItem(itemId, answer)],
        extra,
      );
      if (!stream) {
        res.status(200).json({ ...done, output_text: answer.text });
        return;
      }
      for (const event of responseAnswerEvents(itemId, answer)) emit(event);
      emit({ type: 'response.completed', response: done });
      res.end();
    } catch (error) {
      if (!res.headersSent) {
        sendRunError(res, error);
      } else if (!res.writableEnded) {
        emit({
          type: 'response.failed',
          response: responseObject(responseId, model, createdAt, 'failed', [], {
            ...extra,
            error: runErrorParts(error),
          }),
        });
        res.end();
      }
    }
  };
