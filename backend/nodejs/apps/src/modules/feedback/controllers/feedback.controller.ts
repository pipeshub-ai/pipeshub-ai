import { Response, NextFunction } from 'express';
import mongoose from 'mongoose';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { FileBufferInfo } from '../../../libs/middlewares/file_processor/fp.interface';
import { BadRequestError, UnauthorizedError } from '../../../libs/errors/http.errors';
import { FeedbackKind } from '../schema/feedback.schema';
import { FeedbackService } from '../service/feedback.service';

export async function createFeedback(
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
  service: FeedbackService,
): Promise<void> {
  try {
    const userId = req.user?.userId;
    const orgId = req.user?.orgId;
    if (!userId || !orgId || !mongoose.isValidObjectId(userId) || !mongoose.isValidObjectId(orgId)) {
      throw new UnauthorizedError('Unauthorized');
    }

    const kind = req.body?.kind as FeedbackKind;
    const description = typeof req.body?.description === 'string' ? req.body.description.trim() : '';
    if (!kind || !description) {
      throw new BadRequestError('Kind and description are required');
    }

    const files: FileBufferInfo[] = req.body?.fileBuffers?.length
      ? req.body.fileBuffers
      : req.body?.fileBuffer
        ? [req.body.fileBuffer]
        : [];

    const created = await service.createFeedback({
      orgId,
      userId,
      kind,
      description,
      files,
    });

    res.status(201).json({ id: created.id });
  } catch (error) {
    next(error);
  }
}
