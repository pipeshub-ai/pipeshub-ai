import type { AppConfig } from '../../tokens_manager/config/config';
import type { OrgController } from '../controller/org.controller';
import type { UserController } from '../controller/users.controller';

/**
 * Builds a controller, with every service the container composed it with, for
 * a new config: a config update rebuilds through these and so never drops the
 * deletion services (ORGDEL).
 */
export type OrgControllerFactory = (config: AppConfig) => OrgController;
export type UserControllerFactory = (config: AppConfig) => UserController;
export const ORG_CONTROLLER_FACTORY = 'OrgControllerFactory';
export const USER_CONTROLLER_FACTORY = 'UserControllerFactory';
