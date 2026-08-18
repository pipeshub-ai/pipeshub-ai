import { Router } from 'express';
import { Container } from 'inversify';

export function createWorkspaceAuthRouter(_container: Container): Router {
  return Router();
}
