import { Router } from 'express';
import { Container } from 'inversify';

export function createFeatureFlagRouter(_container: Container): Router {
  return Router();
}
