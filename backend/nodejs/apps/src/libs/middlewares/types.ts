import { Request } from 'express';

export interface AuthenticatedUserRequest extends Request {
  user?: Record<string, any>;
}

export interface AuthenticatedServiceRequest extends Request {
  tokenPayload?: Record<string, any>;
}
