import { Request } from 'express';
import { SessionData } from '../services/session.service';
import { Container } from 'inversify';
export interface AuthSessionRequest extends Request {
  sessionInfo?: SessionData;
  user?: Record<string, any>;
  container?: Container;
}

export interface ContainerRequest extends Request {
  container?: Container;
}
export interface SendMailParams {
  emailTemplateType: string;
  initiator: { orgId?: string; jwtAuthToken: string };
  usersMails: string[];
  subject: string;
  templateData?: Record<string, any>;
  fromEmailDomain?: string;
  attachedDocuments?: any[];
  ccEmails?: string[];
}
