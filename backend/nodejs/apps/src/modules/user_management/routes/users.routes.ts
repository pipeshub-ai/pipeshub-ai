import { Router, Request, Response, NextFunction } from 'express';
import { Container } from 'inversify';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { userExists } from '../middlewares/userExists';
import { userAdminCheck } from '../middlewares/userAdminCheck';
import { userAdminOrSelfCheck } from '../middlewares/userAdminOrSelfCheck';
// import { attachContainerMiddleware } from '../../auth/middlewares/attachContainer.middleware';
import { accountTypeCheck } from '../middlewares/accountTypeCheck';
import { Logger } from '../../../libs/services/logger.service';
import { smtpConfigCheck } from '../middlewares/smtpConfigCheck';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../libs/middlewares/types';
import { UserController } from '../controller/users.controller';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { FileProcessorFactory } from '../../../libs/middlewares/file_processor/fp.factory';
import { FileProcessingType } from '../../../libs/middlewares/file_processor/fp.constant';
import { AppConfig, loadAppConfig } from '../../tokens_manager/config/config';
import { Users } from '../schema/users.schema';
import { UserGroups } from '../schema/userGroup.schema';
import { NotFoundError } from '../../../libs/errors/http.errors';
import { MailService } from '../services/mail.service';
import { AuthService } from '../services/auth.service';
import { EntitiesEventProducer } from '../services/entity_events.service';
import { OrgController } from '../controller/org.controller';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { sendValidatedJson } from '../../../utils/response-validator';
import { UserIdValidationSchema,
  createUserValidationSchema, 
  updateUserFullNameValidationSchema, 
  updateUserFirstNameValidationSchema, 
  updateUserLastNameValidationSchema, 
  updateUserDesignationValidationSchema, 
  updateUserEmailValidationSchema, 
  updateUserValidationSchema, 
  emailIdValidationSchema,
  UpdateUserDisplayPictureValidationSchema,
  GetAllUsersValidationSchema,
  GetUserEmailByUserIdValidationSchema,
  UserAdminCheckResponseSchema,
  UsersHealthResponseSchema,
  InternalAdminUsersResponseSchema,
  InternalLookupUserResponseSchema,
  BulkInviteValidationSchema,
} from '../schemas/user.schemas';

export function createUserRouter(container: Container) {
  const router = Router();
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const logger = container.get<Logger>('Logger');
  const config = container.get<AppConfig>('AppConfig');
  // Todo: Apply Rate Limiter Middleware
  // Todo: Apply Validation Middleware
  // Routes

  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_READ),
    ValidationMiddleware.validate(GetAllUsersValidationSchema),
    metricsMiddleware(container),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.getAllUsers(req, res);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/fetch/with-groups',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_READ),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.getAllUsersWithGroups(req, res);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/:id/email',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_READ),
    ValidationMiddleware.validate(GetUserEmailByUserIdValidationSchema),
    metricsMiddleware(container),
    userAdminCheck,
    userExists,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.getUserEmailByUserId(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.put(
    '/:id/unblock',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    ValidationMiddleware.validate(UserIdValidationSchema),
    userAdminCheck,

    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.unblockUser(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.put(
    '/dp',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    ...FileProcessorFactory.createBufferUploadProcessor({
      fieldName: 'file',
      allowedMimeTypes: [
        'image/png',
        'image/jpeg',
        'image/jpg',
        'image/webp',
        'image/gif',
      ],
      maxFilesAllowed: 1,
      isMultipleFilesAllowed: false,
      processingType: FileProcessingType.BUFFER,
      maxFileSize: 1024 * 1024,
      strictFileUpload: true,
    }).getMiddleware,
    metricsMiddleware(container),
    ValidationMiddleware.validate(UpdateUserDisplayPictureValidationSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.updateUserDisplayPicture(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.delete(
    '/dp',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    metricsMiddleware(container),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.removeUserDisplayPicture(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/dp',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_READ),
    metricsMiddleware(container),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.getUserDisplayPicture(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  // Health check endpoint
  router.get(
    '/health',
    (_req: Request, res: Response, next: NextFunction) => {
      try {
        sendValidatedJson(
          res,
          UsersHealthResponseSchema,
          {
            status: 'healthy',
            timestamp: new Date().toISOString(),
          },
          HTTP_STATUS.OK,
        );
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/:id',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_READ),
    ValidationMiddleware.validate(UserIdValidationSchema),
    metricsMiddleware(container),
    userExists,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.getUserById(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/email/exists',
    metricsMiddleware(container),
    authMiddleware.scopedTokenValidator(TokenScopes.USER_LOOKUP),
    ValidationMiddleware.validate(emailIdValidationSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.checkUserExistsByEmail(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );
  /**
   * GET /users/internal/admin-users
   * Internal service-to-service endpoint.
   * Returns admin user IDs for the given organization.
   * orgId is accepted ONLY from the scoped token payload.
   * Protected by USER_LOOKUP scoped token (generated by Python services from scopedJwtSecret).
   */
  router.get(
    '/internal/admin-users',
    authMiddleware.scopedTokenValidator(TokenScopes.USER_LOOKUP),
    metricsMiddleware(container),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        // orgId from token payload OR query param (allows migration to pass per-org id)
        const orgId =
          req.tokenPayload?.orgId;
        if (!orgId) {
          res.status(400).json({ error: 'Organization ID is required' });
          return;
        }

        const adminGroups = await UserGroups.find({
          orgId,
          type: 'admin',
          isDeleted: false,
        }).select('users');
        type AdminGroupUsers = {
          users?: Array<{ toString: () => string }>;
        };

        const adminUserIds = [
          ...new Set(
            adminGroups.flatMap((group: AdminGroupUsers) =>
              (group.users || []).map((id) => id.toString()),
            ),
          ),
        ];

        sendValidatedJson(
          res,
          InternalAdminUsersResponseSchema,
          { adminUserIds },
          HTTP_STATUS.OK,
        );
        return;
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/internal/:id',
    authMiddleware.scopedTokenValidator(TokenScopes.USER_LOOKUP),
    ValidationMiddleware.validate(UserIdValidationSchema),
    metricsMiddleware(container),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userId = req.params.id;
        const orgId = req.tokenPayload?.orgId;
        try {
          const user = await Users.findOne({
            _id: userId,
            orgId,
            isDeleted: false,
          })
            .lean()
            .exec();

          if (!user) {
            throw new NotFoundError('User not found');
          }

          sendValidatedJson(
            res,
            InternalLookupUserResponseSchema,
            user,
            HTTP_STATUS.OK,
          );
        } catch (error) {
          next(error);
        }
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_INVITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(createUserValidationSchema),
    userAdminCheck,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.createUser(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.patch(
    '/:id/fullname',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateUserFullNameValidationSchema),
    userAdminOrSelfCheck,
    userExists,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.updateFullName(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.patch(
    '/:id/firstName',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateUserFirstNameValidationSchema),
    userAdminOrSelfCheck,
    userExists,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.updateFirstName(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.patch(
    '/:id/lastName',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateUserLastNameValidationSchema),
    userAdminOrSelfCheck,
    userExists,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.updateLastName(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.patch(
    '/:id/designation',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateUserDesignationValidationSchema),
    userAdminOrSelfCheck,
    userExists,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.updateDesignation(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.patch(
    '/:id/email',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateUserEmailValidationSchema),
    userAdminOrSelfCheck,
    userExists,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.updateEmail(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.put(
    '/:id',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateUserValidationSchema),
    userAdminOrSelfCheck,
    userExists,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.updateUser(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.delete(
    '/:id',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_DELETE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(UserIdValidationSchema),
    userAdminCheck,
    userExists,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.deleteUser(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/:id/adminCheck',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(UserIdValidationSchema),
    userAdminCheck,
    async (
      _req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        sendValidatedJson(
          res,
          UserAdminCheckResponseSchema,
          { message: 'User has admin access' },
          HTTP_STATUS.OK,
        );
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/bulk/invite',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_INVITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(BulkInviteValidationSchema),
    smtpConfigCheck(config.cmBackend),
    userAdminCheck,
    accountTypeCheck,
    // attachContainerMiddleware(container),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.addManyUsers(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/:id/resend-invite',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_INVITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(UserIdValidationSchema),
    smtpConfigCheck(config.cmBackend),
    userAdminCheck,
    accountTypeCheck,
    // attachContainerMiddleware(container),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.resendInvite(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/updateAppConfig',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    async (
      _req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const updatedConfig: AppConfig = await loadAppConfig();

        container
          .rebind<AppConfig>('AppConfig')
          .toDynamicValue(() => updatedConfig);

        // Rebind services depending on AppConfig
        container.rebind<MailService>('MailService').toDynamicValue(() => {
          return new MailService(updatedConfig, logger);
        });

        container.rebind<AuthService>('AuthService').toDynamicValue(() => {
          return new AuthService(updatedConfig, logger);
        });

        // Rebind controllers
        container.rebind<OrgController>('OrgController').toDynamicValue(() => {
          return new OrgController(
            updatedConfig,
            container.get<MailService>('MailService'),
            logger,
            container.get<EntitiesEventProducer>('EntitiesEventProducer'),
          );
        });

        container
          .rebind<UserController>('UserController')
          .toDynamicValue(() => {
            return new UserController(
              updatedConfig,
              container.get<MailService>('MailService'),
              container.get<AuthService>('AuthService'),
              logger,
              container.get<EntitiesEventProducer>('EntitiesEventProducer'),
            );
          });
        res.status(200).json({
          message: 'User configuration updated successfully',
          config: updatedConfig,
        });
        return;
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/graph/list',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.USER_READ),
    metricsMiddleware(container),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const userController = container.get<UserController>('UserController');
        await userController.listUsers(req, res, next);
      } catch (error) {
        next(error);
      }
    },
  );

  return router;
}
