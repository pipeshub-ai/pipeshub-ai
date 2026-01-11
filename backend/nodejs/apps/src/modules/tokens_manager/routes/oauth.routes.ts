/**
 * OAuth Config Routes
 * 
 * RESTful routes for managing OAuth app configurations.
 * These routes proxy to the Python backend for OAuth config management.
 * 
 * @module oauth/routes
 */

import { Router } from 'express';
import { Container } from 'inversify';
import { z } from 'zod';

import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { AppConfig } from '../config/config';
import {
  getOAuthConfigRegistry,
  getOAuthConfigRegistryByType,
  getAllOAuthConfigs,
  listOAuthConfigs,
  createOAuthConfig,
  getOAuthConfig,
  updateOAuthConfig,
  deleteOAuthConfig,
} from '../controllers/oauth.controllers';

// ============================================================================
// Validation Schemas
// ============================================================================

/**
 * Schema for OAuth config registry query parameters
 */
const oauthConfigRegistrySchema = z.object({
  query: z.object({
    page: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1))
      .optional(),
    limit: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1).max(200))
      .optional(),
    search: z.string().optional(),
  }),
});

/**
 * Schema for getting all OAuth configs query parameters
 */
const getAllOAuthConfigsSchema = z.object({
  query: z.object({
    page: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1))
      .optional(),
    limit: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1).max(200))
      .optional(),
    search: z.string().optional(),
  }),
});

/**
 * Schema for OAuth config list query parameters
 */
const oauthConfigListSchema = z.object({
  params: z.object({
    connectorType: z.string().min(1, 'Connector type is required'),
  }),
  query: z.object({
    page: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1))
      .optional(),
    limit: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1).max(200))
      .optional(),
    search: z.string().optional(),
  }),
});

/**
 * Schema for creating OAuth config
 */
const createOAuthConfigSchema = z.object({
  params: z.object({
    connectorType: z.string().min(1, 'Connector type is required'),
  }),
  body: z.object({
    oauthInstanceName: z.string().min(1, 'OAuth instance name is required'),
    config: z.any(),
  }),
});

/**
 * Schema for getting/deleting OAuth config
 */
const oauthConfigIdSchema = z.object({
  params: z.object({
    connectorType: z.string().min(1, 'Connector type is required'),
    configId: z.string().min(1, 'Config ID is required'),
  }),
});

/**
 * Schema for updating OAuth config
 */
const updateOAuthConfigSchema = z.object({
  params: z.object({
    connectorType: z.string().min(1, 'Connector type is required'),
    configId: z.string().min(1, 'Config ID is required'),
  }),
  body: z.object({
    oauthInstanceName: z.string().min(1, 'OAuth instance name is required').optional(),
    config: z.any().optional(),
  }),
});

// ============================================================================
// Router Factory
// ============================================================================

/**
 * Create and configure the OAuth config router.
 * 
 * @param container - Dependency injection container
 * @returns Configured Express router
 */
export function createOAuthRouter(container: Container): Router {
  const router = Router();
  const config = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  // ============================================================================
  // OAuth Config Routes
  // ============================================================================

  /**
   * GET /registry
   * Get OAuth config registry (available connector/tool types with OAuth support)
   */
  router.get(
    '/registry',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(oauthConfigRegistrySchema),
    getOAuthConfigRegistry(config)
  );

  /**
   * GET /registry/:connectorType
   * Get OAuth config registry information for a specific connector type
   * This must come before /:connectorType to avoid route conflicts
   */
  router.get(
    '/registry/:connectorType',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(oauthConfigRegistrySchema),
    getOAuthConfigRegistryByType(config)
  );

  /**
   * GET /
   * Get all OAuth configs across all connector types
   * This must come before /:connectorType to avoid route conflicts
   */
  router.get(
    '/',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(getAllOAuthConfigsSchema),
    getAllOAuthConfigs(config)
  );

  /**
   * GET /:connectorType
   * List OAuth configs for a connector type
   */
  router.get(
    '/:connectorType',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(oauthConfigListSchema),
    listOAuthConfigs(config)
  );

  /**
   * POST /:connectorType
   * Create a new OAuth config
   */
  router.post(
    '/:connectorType',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(createOAuthConfigSchema),
    createOAuthConfig(config)
  );

  /**
   * GET /:connectorType/:configId
   * Get a specific OAuth config
   */
  router.get(
    '/:connectorType/:configId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(oauthConfigIdSchema),
    getOAuthConfig(config)
  );

  /**
   * PUT /:connectorType/:configId
   * Update an OAuth config
   */
  router.put(
    '/:connectorType/:configId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateOAuthConfigSchema),
    updateOAuthConfig(config)
  );

  /**
   * DELETE /:connectorType/:configId
   * Delete an OAuth config
   */
  router.delete(
    '/:connectorType/:configId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(oauthConfigIdSchema),
    deleteOAuthConfig(config)
  );

  return router;
}

