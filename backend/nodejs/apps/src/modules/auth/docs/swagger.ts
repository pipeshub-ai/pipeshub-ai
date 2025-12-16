// services/nodejs/apps/src/modules/auth/docs/swagger.ts
import * as path from 'path';
import {
  ModuleSwaggerInfo,
  SwaggerService,
} from '../../docs/swagger.container';

/**
 * Auth module Swagger configuration
 */
export const authSwaggerConfig: ModuleSwaggerInfo = {
  moduleId: 'auth',
  tagName: 'Authentication',
  tagDescription:
    'Authentication service operations for user authentication, authorization, and session management',
  yamlFilePath: path.join(process.cwd(), 'src/modules/auth/docs/swagger.yaml'),
  baseUrl: '/api/v1', // Base URL for auth module (includes /saml, /userAccount, /orgAuthConfig)
};

/**
 * Function to register auth module Swagger documentation
 * @param swaggerService The application's SwaggerService instance
 */
export function registerAuthSwagger(swaggerService: SwaggerService): void {
  swaggerService.registerModule(authSwaggerConfig);
}

