// services/nodejs/apps/src/modules/user_management/docs/swagger.ts
import * as path from 'path';
import {
  ModuleSwaggerInfo,
  SwaggerService,
} from '../../docs/swagger.container';

/**
 * User Management service Swagger configuration
 */
export const userManagementSwaggerConfig: ModuleSwaggerInfo = {
  moduleId: 'user-management',
  tagName: 'User Management',
  tagDescription:
    'User management service operations for users, teams, organizations, and user groups with role-based access control',
  yamlFilePath: path.join(process.cwd(), 'src/modules/user_management/docs/swagger.yaml'),
  baseUrl: '/api/v1', // for user management module
};

/**
 * Function to register user management service Swagger documentation
 * @param swaggerService The application's SwaggerService instance
 */
export function registerUserManagementSwagger(swaggerService: SwaggerService): void {
  swaggerService.registerModule(userManagementSwaggerConfig);
}

