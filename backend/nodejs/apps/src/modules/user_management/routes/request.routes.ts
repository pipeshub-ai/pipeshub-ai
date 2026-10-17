import { Router } from 'express';
import { Container } from 'inversify';

export function createRequestRouter(_container: Container): Router {
  return Router();
}
