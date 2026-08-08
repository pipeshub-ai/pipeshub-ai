import { Router } from 'express';
import { Container } from 'inversify';

export const createOAuthAppsRouter = (_container: Container): Router => {
  return Router({ mergeParams: true });
};
