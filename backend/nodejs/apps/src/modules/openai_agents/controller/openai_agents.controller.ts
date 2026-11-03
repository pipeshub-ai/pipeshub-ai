import { Request, Response } from 'express';

import { AppConfig } from '../../tokens_manager/config/config';
import { AgentRunError, runAgent } from '../utils/agent-run';
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
const MODEL_PAGE_LIMIT = 200;
// shortcut: lists at most 10 pages (2,000 agents); page by `search` if an org outgrows it.
const MODEL_MAX_PAGES = 10;

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
  if (error instanceof AgentRunError) {
    const param = error.code === 'model_not_found' ? 'model' : null;
    sendError(res, error.status, error.message, error.code, param);
  } else {
    sendError(res, 502, 'The agent run failed', 'server_error');
  }
}

function runErrorParts(error: unknown): { code: string; message: string } {
  return error instanceof AgentRunError
    ? { code: error.code, message: error.message }
    : { code: 'server_error', message: 'The agent run failed' };
}

/** Aborts the agent run when the client goes away before the answer. */
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

function requireModel(body: Json, res: Response): string | null {
  const model = asString(body.model).trim();
  if (model === '') {
    sendError(
      res,
      400,
      'model is required: pass an agent id from GET /models',
      'missing_required_parameter',
      'model',
    );
    return null;
  }
  return model;
}

interface AgentModel {
  id: string;
  object: 'model';
  created: number;
  owned_by: 'pipeshub';
  name: string;
  description: string;
}

function toModel(agent: Json): AgentModel {
  const id = asString(agent._key);
  const createdMs = Number(agent.createdAtTimestamp);
  return {
    id,
    object: 'model',
    created: Number.isFinite(createdMs) ? Math.floor(createdMs / 1000) : 0,
    owned_by: 'pipeshub',
    name: asString(agent.name) || id,
    description: asString(agent.description),
  };
}

async function listAgentModels(
  appConfig: AppConfig,
  req: Request,
): Promise<AgentModel[]> {
  const data: AgentModel[] = [];
  for (let page = 1; page <= MODEL_MAX_PAGES; page++) {
    const query = `page=${String(page)}&limit=${String(MODEL_PAGE_LIMIT)}`;
    const response = await fetch(
      `${appConfig.esBackend.replace(/\/$/, '')}/api/v1/agents?${query}`,
      {
        headers: {
          Authorization: req.headers.authorization ?? '',
          Accept: 'application/json',
        },
      },
    );
    const body: unknown = await response.json().catch(() => null);
    if (response.status !== 200 || !isJson(body)) {
      const error = isJson(body) && isJson(body.error) ? body.error : {};
      throw new AgentRunError(
        asString(error.message) || 'Could not list agents',
        response.status === 200 ? 502 : response.status,
        'server_error',
      );
    }
    const agents = Array.isArray(body.agents) ? body.agents.filter(isJson) : [];
    data.push(
      ...agents
        .filter((a) => asString(a._key) !== '' && a.isDeleted !== true)
        .map(toModel),
    );
    if (!isJson(body.pagination) || body.pagination.hasNext !== true) break;
  }
  return data;
}

export const listModels =
  (appConfig: AppConfig): Handler =>
  async (req, res) => {
    try {
      const data = await listAgentModels(appConfig, req);
      res.status(200).json({ object: 'list', data });
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const retrieveModel =
  (appConfig: AppConfig): Handler =>
  async (req, res) => {
    const id = req.params.modelId ?? '';
    try {
      const models = await listAgentModels(appConfig, req);
      const found = models.find((m) => m.id === id);
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
      res.status(200).json(found);
    } catch (error) {
      sendRunError(res, error);
    }
  };

export const createChatCompletion =
  (appConfig: AppConfig): Handler =>
  async (req, res) => {
    const body = requestBody(req);
    const model = requireModel(body, res);
    if (model === null) return;
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
    const stream = body.stream === true;
    const id = newChatCompletionId();
    const controller = abortOnClose(res);
    let keepalive: (() => void) | null = null;

    try {
      const answer = await runAgent({
        baseUrl: appConfig.esBackend,
        frontendUrl: appConfig.frontendUrl,
        authorization: req.headers.authorization ?? '',
        agentKey: model,
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
      // agent rewrites streamed text (citations, narration before tool calls);
      // stream tokens live once the agent stream exposes a final-text-only channel.
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
  (appConfig: AppConfig): Handler =>
  async (req, res) => {
    const body = requestBody(req);
    const model = requireModel(body, res);
    if (model === null) return;
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
      const answer = await runAgent({
        baseUrl: appConfig.esBackend,
        frontendUrl: appConfig.frontendUrl,
        authorization: req.headers.authorization ?? '',
        agentKey: model,
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
