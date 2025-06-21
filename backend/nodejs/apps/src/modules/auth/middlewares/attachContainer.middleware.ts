import { Response, NextFunction } from 'express';
import { Container } from 'inversify';
import { ContainerRequest } from './types';

export function attachContainerMiddleware(container: Container) {
  return (req: ContainerRequest, _res: Response, next: NextFunction) => {
    req.container = container;
    next();
  };
}
