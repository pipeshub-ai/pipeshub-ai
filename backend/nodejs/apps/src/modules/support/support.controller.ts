import { NextFunction, Response } from 'express';
import { getCurrentOrgId } from '../../libs/context/orgContext';
import { UnauthorizedError } from '../../libs/errors/http.errors';
import { AuthenticatedUserRequest } from '../../libs/middlewares/types';
import { isValidId } from '../../libs/repositories/ids';
import { SupportService } from './support.service';
import {
  CreateFeedbackBody,
  CreateSupportRequestBody,
} from './support.validators';

/** The workspace (bound from the verified token) and the user never come from the request body. */
function requireUser(req: AuthenticatedUserRequest): {
  orgId: string;
  userId: string;
} {
  const userId: unknown = req.user?.userId;
  const orgId = getCurrentOrgId();
  if (
    typeof userId !== 'string' ||
    typeof orgId !== 'string' ||
    !isValidId(userId) ||
    !isValidId(orgId)
  ) {
    throw new UnauthorizedError('Unauthorized');
  }
  return { orgId, userId };
}

export async function getSupportContext(
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
  service: SupportService,
): Promise<void> {
  try {
    const { orgId, userId } = requireUser(req);
    res.status(200).json(await service.getContext(orgId, userId));
  } catch (error) {
    next(error);
  }
}

export async function createSupportRequest(
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
  service: SupportService,
): Promise<void> {
  try {
    const { orgId, userId } = requireUser(req);
    const body = req.body as CreateSupportRequestBody;
    const result = await service.createSupportRequest({
      orgId,
      userId,
      area: body.area,
      description: body.description,
      requestKey: body.requestKey,
      recentErrors: body.recentErrors,
      files: body.fileBuffers ?? [],
      userAgent: req.get('user-agent'),
    });
    res.status(201).json(result);
  } catch (error) {
    next(error);
  }
}

export async function createFeedback(
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
  service: SupportService,
): Promise<void> {
  try {
    const { orgId, userId } = requireUser(req);
    const body = req.body as CreateFeedbackBody;
    const result = await service.createFeedback({
      orgId,
      userId,
      kind: body.kind,
      description: body.description,
      files: body.fileBuffers ?? [],
    });
    res.status(201).json(result);
  } catch (error) {
    next(error);
  }
}
