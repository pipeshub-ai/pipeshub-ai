import type { Router } from 'express';
import type { Container } from 'inversify';
import { CAPABILITY_ROUTES } from './capability-routes';
import { CONNECTION_ROUTES } from './connection-routes';
import { createCpRouter, type AnyCpRouteSpec } from './cp-route';

/** The control plane's admin route table (CPD-11 §3), enumerated by the guard, audit and OpenAPI tests. */
export const CONTROL_PLANE_ROUTES: readonly AnyCpRouteSpec[] = [
  ...CAPABILITY_ROUTES,
  ...CONNECTION_ROUTES,
];

/** Mounted at `CONTROL_PLANE_API_PREFIX`; `extra`, an edition's routes, after the built-in table. */
export const createControlPlaneAdminRouter = (
  container: Container,
  extra: readonly AnyCpRouteSpec[] = [],
): Router => createCpRouter(container, [...CONTROL_PLANE_ROUTES, ...extra]);
