import { Router, Request, Response } from 'express';
import { Container } from 'inversify';
import jwt from 'jsonwebtoken';
import mongoose from 'mongoose';
import { AppConfig } from '../../tokens_manager/config/config';
import { Logger } from '../../../libs/services/logger.service';
import { Conversation } from '../../enterprise_search/schema/conversation.schema';
import { CONVERSATION_STATUS } from '../../enterprise_search/constants/constants';
import type { IMessageDocument } from '../../enterprise_search/types/conversation.interfaces';

const logger = Logger.getInstance({ service: 'Workflows Internal' });

/**
 * Internal, scoped-JWT-gated route hit by Python's `NodeConversationWriter`
 * (§5.3 of the workflow plan) after a run finishes, so a background/scheduled
 * workflow's result is visible in the conversation that created it, not just
 * in the dashboard/notification center.
 *
 * Appends messages directly to the conversation document rather than going
 * through the streaming AI-service pipeline (`addMessageStream`) -- there is
 * no new user turn and no LLM call here, just output to record. A terminal
 * run result goes to `/messages`; a mid-run `ctx.emit` goes to `/emit`, which
 * writes an ordinary assistant message and leaves the conversation's status
 * alone because the run is still going.
 *
 * A missing/deleted conversation is reported as a 2xx skip -- there is
 * nothing to write and nothing to retry. A write that was attempted and
 * failed answers 5xx instead, so the failure is visible to the caller and
 * its retry/alerting; the Python side already treats a write-back failure as
 * non-fatal to the run (see `IConversationWriter`'s port contract), so an
 * honest status here cannot fail a run.
 */
export function createWorkflowsInternalRouter(container: Container): Router {
  const router = Router();
  const appConfig = container.get<AppConfig>('AppConfig');

  /**
   * Verifies the internal scoped JWT and returns its `org_id`, replying 401
   * itself when either is missing. `org_id` is required rather than
   * optional: every conversation lookup below is scoped by it, and treating
   * it as optional turned a token minted without one into a cross-tenant
   * read of any conversation whose id the caller could guess.
   */
  const authenticate = (req: Request, res: Response): string | null => {
    const authHeader = req.headers.authorization || '';
    if (!authHeader.startsWith('Bearer ')) {
      res.status(401).json({ error: 'Missing internal token' });
      return null;
    }
    try {
      // No `|| ''` fallback: an empty secret makes every HS256 signature
      // verify against the empty key. The container already fails startup
      // when the secret is absent, so an undefined here is a bug, not a
      // configuration state to degrade into.
      const decoded = jwt.verify(authHeader.slice(7), appConfig.scopedJwtSecret, {
        algorithms: ['HS256'],
        audience: 'pipeshub-node-internal',
        issuer: 'pipeshub-python',
      }) as { org_id?: string };
      if (!decoded.org_id) {
        logger.warn('[workflows-internal] internal token carries no org_id', {
          path: req.path,
        });
        res.status(401).json({ error: 'Internal token is missing org_id' });
        return null;
      }
      return decoded.org_id;
    } catch (err) {
      // Python treats a write-back failure as non-fatal to the run, so a
      // rejected token is otherwise invisible on both sides: the run succeeds
      // and the chat simply never shows the result. The reason (expired,
      // wrong issuer/audience, secret mismatch) is what distinguishes a
      // misconfigured scopedJwtSecret from a genuine attack.
      logger.warn('[workflows-internal] rejected internal token', {
        path: req.path,
        reason: err instanceof Error ? err.message : String(err),
      });
      res.status(401).json({ error: 'Invalid internal token' });
      return null;
    }
  };

  router.post('/conversations/:conversationId/messages', async (req: Request, res: Response) => {
    const orgId = authenticate(req, res);
    if (!orgId) return;

    const { conversationId } = req.params;
    const {
      workflowId,
      runId,
      status,
      outputSummary,
      redirectLink,
      workflowName,
      error,
      isDryRun,
      triggerKind,
      startedAt,
      completedAt,
      suspensionKind,
    } = req.body as {
      workflowId?: string;
      runId?: string;
      status?: string;
      outputSummary?: string;
      redirectLink?: string;
      workflowName?: string;
      error?: string;
      isDryRun?: boolean;
      triggerKind?: string;
      startedAt?: string;
      completedAt?: string;
      suspensionKind?: string;
    };

    logger.info('[workflows-internal] append_run_result', {
      conversationId,
      workflowId,
      runId,
      status,
      // The persisted shape decides how the chat renders this: `bot_response`
      // goes through the markdown/tabs/actions path, the older `tool_call`
      // through a card. Logging it is the quickest way to tell which build is
      // actually serving the request.
      messageType: 'bot_response',
      contentChars: (outputSummary ?? error ?? '').length,
      isDryRun: isDryRun ?? false,
    });

    if (!conversationId || !mongoose.Types.ObjectId.isValid(conversationId)) {
      // Never fail the run over a malformed/stale conversation id.
      res.json({ ok: true, skipped: 'invalid_conversation_id' });
      return;
    }

    // A run result is an ordinary assistant answer whose text happens to be
    // markdown produced by a workflow rather than a chat turn. Persisting it
    // as `bot_response` is what makes the frontend render it through
    // `AnswerContent` (markdown, tabs, copy/feedback) for free; the
    // `workflow_run_result` tool entry rides along as metadata for the run
    // header strip.
    const message: Partial<IMessageDocument> = {
      messageType: 'bot_response',
      content: outputSummary ?? error ?? '',
      contentFormat: 'MARKDOWN',
      createdAt: new Date(),
      updatedAt: new Date(),
      tools: [
        {
          toolName: 'workflow_run_result',
          toolResult: {
            workflowId: workflowId ?? '',
            runId: runId ?? '',
            status: status ?? 'unknown',
            outputSummary: outputSummary ?? null,
            redirectLink: redirectLink ?? null,
            workflowName: workflowName ?? null,
            error: error ?? null,
            isDryRun: isDryRun ?? false,
            triggerKind: triggerKind ?? null,
            startedAt: startedAt ?? null,
            completedAt: completedAt ?? null,
            suspensionKind: suspensionKind ?? null,
          },
        },
      ],
    };

    try {
      // `$push` rather than load-modify-`save()`: a background run finishing
      // while a user turn is streaming into the same document would otherwise
      // write back a stale `messages` array and drop the other side's
      // message. It also avoids pulling the whole transcript into memory on
      // every run completion.
      const appended = await Conversation.updateOne(
        { _id: conversationId, isDeleted: { $ne: true }, orgId },
        { $push: { messages: message }, $set: { lastActivityAt: Date.now() } },
      );

      if (appended.matchedCount === 0) {
        logger.warn('[workflows-internal] conversation not found, skipping write-back', {
          conversationId,
          workflowId,
          runId,
        });
        res.json({ ok: true, skipped: 'conversation_not_found' });
        return;
      }

      // An awaiting_input run has asked a question and is still alive, so
      // closing the conversation out here would tell the user the workflow
      // finished at the exact moment it needs an answer from them.
      const awaitingInput = status === 'awaiting_input';
      // A background run that lands while the user is mid-turn must not mark
      // their live conversation finished -- hence the `Inprogress` guard
      // rather than an unconditional COMPLETE.
      const failed = status === 'failed' || status === 'dlq';
      const statusUpdate = awaitingInput
        ? { modifiedCount: 0 }
        : await Conversation.updateOne(
            {
              _id: conversationId,
              isDeleted: { $ne: true },
              orgId,
              status: { $ne: CONVERSATION_STATUS.INPROGRESS },
            },
            {
              $set: {
                status: failed ? CONVERSATION_STATUS.FAILED : CONVERSATION_STATUS.COMPLETE,
              },
            },
          );

      logger.info('[workflows-internal] run result appended', {
        conversationId,
        runId,
        // 0 here means the conversation was mid-turn and deliberately left
        // alone, which otherwise looks like a silently dropped update.
        conversationStatusUpdated: statusUpdate.modifiedCount,
      });

      res.json({ ok: true });
    } catch (err) {
      logger.error('[workflows-internal] failed to append workflow result', {
        conversationId,
        workflowId,
        runId,
        error: err instanceof Error ? err.message : String(err),
      });
      res.status(500).json({ ok: false, error: 'append_failed' });
    }
  });

  /**
   * POST /conversations/:conversationId/emit
   * Body: { runId, content, kind? }
   *
   * Backs `ctx.emit()` from inside a running workflow. Separate from
   * `/messages` because the two disagree on every field that matters: this
   * one carries free text with no run status, records no `workflow_run_result`
   * metadata, and must leave the conversation's status untouched since the
   * run has not finished.
   */
  router.post('/conversations/:conversationId/emit', async (req: Request, res: Response) => {
    const orgId = authenticate(req, res);
    if (!orgId) return;

    const { conversationId } = req.params;
    const { runId, content, kind } = req.body as {
      runId?: string;
      content?: string;
      kind?: string;
    };

    if (!conversationId || !mongoose.Types.ObjectId.isValid(conversationId)) {
      res.json({ ok: true, skipped: 'invalid_conversation_id' });
      return;
    }
    if (typeof content !== 'string' || content.length === 0) {
      res.status(400).json({ ok: false, error: 'content_required' });
      return;
    }

    const message: Partial<IMessageDocument> = {
      messageType: kind === 'error' ? 'error' : 'bot_response',
      content,
      contentFormat: 'MARKDOWN',
      createdAt: new Date(),
      updatedAt: new Date(),
    };

    try {
      const appended = await Conversation.updateOne(
        { _id: conversationId, isDeleted: { $ne: true }, orgId },
        { $push: { messages: message }, $set: { lastActivityAt: Date.now() } },
      );

      if (appended.matchedCount === 0) {
        logger.warn('[workflows-internal] emit skipped, conversation not found', {
          conversationId,
          runId,
        });
        res.json({ ok: true, skipped: 'conversation_not_found' });
        return;
      }
      logger.info('[workflows-internal] emit appended', {
        conversationId,
        runId,
        kind: kind ?? 'text',
        contentChars: content.length,
      });
      res.json({ ok: true });
    } catch (err) {
      logger.error('[workflows-internal] failed to append emitted message', {
        conversationId,
        runId,
        error: err instanceof Error ? err.message : String(err),
      });
      res.status(500).json({ ok: false, error: 'emit_failed' });
    }
  });

  /**
   * PATCH /conversations/:conversationId/workflows
   * Body: { action: "add" | "remove", workflowId: string }
   * Pushes or pulls a workflow ID from the conversation's connectedWorkflowIds array.
   */
  router.patch('/conversations/:conversationId/workflows', async (req: Request, res: Response) => {
    const orgId = authenticate(req, res);
    if (!orgId) return;

    const { conversationId } = req.params;
    const { action, workflowId } = req.body as { action?: string; workflowId?: string };

    if (!conversationId || !mongoose.Types.ObjectId.isValid(conversationId) || !workflowId) {
      res.json({ ok: true, skipped: 'invalid_params' });
      return;
    }

    try {
      if (action !== 'add' && action !== 'remove') {
        res.json({ ok: true, skipped: 'unknown_action' });
        return;
      }

      // A single-field update rather than read-modify-save: the document also
      // holds the whole message array, and saving it back would clobber any
      // message appended between the read and the write.
      const update =
        action === 'add'
          ? { $addToSet: { connectedWorkflowIds: workflowId } }
          : { $pull: { connectedWorkflowIds: workflowId } };
      const result = await Conversation.updateOne(
        { _id: conversationId, isDeleted: { $ne: true }, orgId },
        update,
      );
      if (result.matchedCount === 0) {
        res.json({ ok: true, skipped: 'conversation_not_found' });
        return;
      }
      res.json({ ok: true });
    } catch (err) {
      logger.error('[workflows-internal] failed to update connectedWorkflowIds', { err });
      res.status(500).json({ ok: false, error: 'update_failed' });
    }
  });

  /**
   * GET /conversations/:conversationId/workflows
   * Returns { workflowIds: string[] } from the conversation's connectedWorkflowIds.
   */
  router.get('/conversations/:conversationId/workflows', async (req: Request, res: Response) => {
    const orgId = authenticate(req, res);
    if (!orgId) return;

    const { conversationId } = req.params;
    if (!conversationId || !mongoose.Types.ObjectId.isValid(conversationId)) {
      res.json({ workflowIds: [] });
      return;
    }

    try {
      const conversation = await Conversation.findOne(
        { _id: conversationId, isDeleted: { $ne: true }, orgId },
        { connectedWorkflowIds: 1 },
      );
      if (!conversation) {
        res.json({ workflowIds: [] });
        return;
      }
      res.json({ workflowIds: conversation.connectedWorkflowIds ?? [] });
    } catch (err) {
      logger.error('[workflows-internal] failed to get connectedWorkflowIds', { err });
      // An empty list here is indistinguishable from "no workflows", which
      // would make the panel silently look correct while being wrong.
      res.status(500).json({ error: 'lookup_failed' });
    }
  });

  return router;
}
