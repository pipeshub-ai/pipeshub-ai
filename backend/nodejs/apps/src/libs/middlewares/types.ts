import { Request } from 'express';

export interface AuthenticatedUserRequest extends Request {
  user?: Record<string, any>;
}

export interface AuthenticatedServiceRequest extends Request {
  tokenPayload?: Record<string, any>;
  // The exact token scopedTokenValidator verified, for handlers that must
  // identify the credential itself (a single-use link) rather than re-parse
  // the header.
  verifiedToken?: string;
}
