import { Router } from 'express';
import { Container } from 'inversify';

export function createOrgConfigRouter(_container: Container): Router {
  return Router();
}
