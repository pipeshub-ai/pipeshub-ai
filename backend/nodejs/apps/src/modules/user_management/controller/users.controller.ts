import { Response, NextFunction } from 'express';
import { User, Users } from '../schema/users.schema'; // Adjust path as needed
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import mongoose from 'mongoose';
import { UserDisplayPicture } from '../schema/userDp.schema';
import sharp from 'sharp';
import {
  fetchConfigJwtGenerator,
  jwtGeneratorForNewAccountPassword,
  mailJwtGenerator,
} from '../../../libs/utils/createJwt';
import {
  BadRequestError,
  InternalServerError,
  LargePayloadError,
  NotFoundError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { inject, injectable } from 'inversify';
import { MailService } from '../services/mail.service';
import {
  EntitiesEventProducer,
  Event,
  EventType,
  UserAddedEvent,
  UserDeletedEvent,
  UserUpdatedEvent,
  SyncAction,
} from '../services/entity_events.service';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { UserGroups } from '../schema/userGroup.schema';
import { AuthService } from '../services/auth.service';
import { Org } from '../schema/org.schema';
import { UserCredentials } from '../../auth/schema/userCredentials.schema';
import { AICommandOptions } from '../../../libs/commands/ai_service/ai.service.command';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';

@injectable()
export class UserController {
  constructor(
    @inject('AppConfig') private config: AppConfig,
    @inject('MailService') private mailService: MailService,
    @inject('AuthService') private authService: AuthService,
    @inject('Logger') private logger: Logger,
    @inject('EntitiesEventProducer')
    private eventService: EntitiesEventProducer,
  ) {}

  async getAllUsers(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const users = await Users.find({
      orgId: req.user?.orgId,
      isDeleted: false,
    });
    res.json(users);
  }

  async getAllUsersWithGroups(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const orgId = req.user?.orgId;
    const orgIdObj = new mongoose.Types.ObjectId(orgId);

    const users = await Users.aggregate([
      {
        $match: {
          orgId: orgIdObj, // Only include users from the same org
          isDeleted: false, // Exclude deleted users
        },
      },
      {
        $lookup: {
          from: 'userGroups', // Collection name for user groups
          localField: '_id', // Field in appusers collection
          foreignField: 'users', // Field in appuserGroups collection (array of user IDs)
          as: 'groups', // Resulting array of groups for each user
        },
      },
      {
        $addFields: {
          // Filter groups array to keep only non-deleted groups from same org
          groups: {
            $filter: {
              input: '$groups',
              as: 'group',
              cond: {
                $and: [
                  { $eq: ['$$group.orgId', orgIdObj] },
                  { $ne: ['$$group.isDeleted', true] },
                ],
              },
            },
          },
        },
      },
      {
        $project: {
          _id: 1,
          userId: 1,
          orgId: 1,
          fullName: 1,
          email: 1,
          hasLoggedIn: 1,
          groups: {
            $map: {
              input: '$groups',
              as: 'group',
              in: {
                name: '$$group.name',
                type: '$$group.type',
              },
            },
          },
        },
      },
    ]);

    res.status(200).json(users);
  }

  async getUserById(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ) {
    const userId = req.params.id;
    const orgId = req.user?.orgId;
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

      res.json(user);
    } catch (error) {
      next(error);
    }
  }

  async getUsersByIds(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { userIds }: { userIds: string[] } = req.body;

      // Validate if userIds is an array and not empty
      if (!userIds || !Array.isArray(userIds) || userIds.length === 0) {
        throw new BadRequestError(
          'userIds must be provided as a non-empty array',
        );
      }

      // Ensure that userIds are valid MongoDB ObjectIds
      const userObjectIds = userIds.map(
        (id) => new mongoose.mongo.ObjectId(id),
      );

      // Fetch the users using the provided list of user IDs
      const users = await Users.find({
        orgId: req.user?.orgId, // Assuming orgId is in decodedToken
        isDeleted: false,
        _id: { $in: userObjectIds },
      });

      res.status(200).json(users);
    } catch (error) {
      next(error);
    }
  }

  async checkUserExistsByEmail(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { email } = req.body;

      const users = await Users.find({
        email: email,
        isDeleted: false,
      });

      res.json(users);
      return;
    } catch (error) {
      next(error);
    }
  }

  async createUser(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const newUser = new Users({
        ...req.body,
        orgId: req.user?.orgId,
      });

      await UserGroups.updateOne(
        { orgId: newUser.orgId, type: 'everyone' }, // Find the everyone group in the same org
        { $addToSet: { users: newUser._id } }, // Add user to the group if not already present
      );

      await this.eventService.start();
      const event: Event = {
        eventType: EventType.NewUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: newUser.orgId.toString(),
          userId: newUser._id,
          fullName: newUser.fullName,
          email: newUser.email,
          syncAction: SyncAction.Immediate,
        } as UserAddedEvent,
      };
      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      await newUser.save();
      this.logger.debug('user created');
      res.status(201).json(newUser);
    } catch (error) {
      next(error);
    }
  }

  /**
   * Just-In-Time user provisioning from SAML assertion
   * Creates user, adds to everyone group, and publishes creation event
   */
  async provisionSamlUser(
    email: string,
    samlUser: any,
    orgId: string,
    logger: Logger,
  ) {
    logger.info('Auto-provisioning user from SAML', { email, orgId });

    const userDetails = this.extractSamlUserDetails(samlUser, email);
    const newUser = new Users({
      email,
      ...userDetails,
      orgId,
      hasLoggedIn: false,
      isDeleted: false,
    });

    await newUser.save();

    // Add to everyone group
    await UserGroups.updateOne(
      { orgId, type: 'everyone', isDeleted: false },
      { $addToSet: { users: newUser._id } },
    );

    // Publish user creation event
    try {
      await this.eventService.start();
      await this.eventService.publishEvent({
        eventType: EventType.NewUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: orgId.toString(),
          userId: newUser._id,
          fullName: newUser.fullName,
          email: newUser.email,
          syncAction: SyncAction.Immediate,
        } as UserAddedEvent,
      });
    } catch (eventError) {
      logger.error('Failed to publish user creation event', {
        error: eventError,
        userId: newUser._id,
      });
    } finally {
      await this.eventService.stop();
    }

    logger.info('User auto-provisioned successfully', {
      userId: newUser._id,
      email,
    });

    return newUser.toObject();
  }

  async updateUser(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { orgId, _id, slug, ...updateFields } = req.body; // Exclude restricted fields
      if (!req.user) {
        throw new UnauthorizedError('Unauthorized to update the user');
      }
      const { id } = req.params;
      const user = await Users.findOne({
        orgId: req.user.orgId,
        _id: id,
        isDeleted: false,
      });

      if (!user) {
        throw new NotFoundError('User not found');
      }

      if (updateFields.firstName) {
        user.firstName = updateFields.firstName;
      }
      if (updateFields.lastName) {
        user.lastName = updateFields.lastName;
      }
      if (updateFields.fullName) {
        user.fullName = updateFields.fullName;
      }
      if (updateFields.middleName) {
        user.middleName = updateFields.middleName;
      }
      if (updateFields.email) {
        user.email = updateFields.email;
      }
      if (updateFields.designation) {
        user.designation = updateFields.designation;
      }
      if (updateFields.mobile) {
        user.mobile = updateFields.mobile;
      }
      if (updateFields.address) {
        user.address = updateFields.address;
      }
      if (updateFields.hasLoggedIn) {
        user.hasLoggedIn = updateFields.hasLoggedIn;
      }
      await user.save();

      await this.eventService.start();

      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: user.orgId.toString(),
          userId: user._id,
          fullName: user.fullName,
          ...(user.firstName && { firstName: user.firstName }),
          ...(user.lastName && { lastName: user.lastName }),
          ...(user.designation && { designation: user.designation }),
          email: user.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      // Save the updated user
      res.json(user.toObject());
    } catch (error) {
      next(error);
    }
  }
  async updateFullName(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      if (!req.user) {
        throw new UnauthorizedError('Unauthorized to update the user');
      }

      const { id } = req.params;
      const user = await Users.findOne({
        orgId: req.user.orgId,
        _id: id,
        isDeleted: false,
      });

      if (!user) {
        throw new NotFoundError('User not found');
      }

      user.fullName = req.body.fullName;
      await user.save();

      await this.eventService.start();
      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: user.orgId.toString(),
          userId: user._id,
          fullName: user.fullName,
          ...(user.firstName && { firstName: user.firstName }),
          ...(user.lastName && { lastName: user.lastName }),
          ...(user.designation && { designation: user.designation }),
          email: user.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      res.json(user.toObject());
    } catch (error) {
      next(error);
    }
  }

  async updateFirstName(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      if (!req.user) {
        throw new UnauthorizedError('Unauthorized to update the user');
      }

      const { id } = req.params;
      const user = await Users.findOne({
        orgId: req.user.orgId,
        _id: id,
        isDeleted: false,
      });

      if (!user) {
        throw new NotFoundError('User not found');
      }

      user.firstName = req.body.firstName;
      await user.save();

      await this.eventService.start();
      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: user.orgId.toString(),
          userId: user._id,
          fullName: user.fullName,
          ...(user.firstName && { firstName: user.firstName }),
          ...(user.lastName && { lastName: user.lastName }),
          ...(user.designation && { designation: user.designation }),
          email: user.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      res.json(user.toObject());
    } catch (error) {
      next(error);
    }
  }

  async updateLastName(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      if (!req.user) {
        throw new UnauthorizedError('Unauthorized to update the user');
      }

      const { id } = req.params;
      const user = await Users.findOne({
        orgId: req.user.orgId,
        _id: id,
        isDeleted: false,
      });

      if (!user) {
        throw new NotFoundError('User not found');
      }

      user.lastName = req.body.lastName;
      await user.save();

      await this.eventService.start();
      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: user.orgId.toString(),
          userId: user._id,
          fullName: user.fullName,
          ...(user.firstName && { firstName: user.firstName }),
          ...(user.lastName && { lastName: user.lastName }),
          ...(user.designation && { designation: user.designation }),
          email: user.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      res.json(user.toObject());
    } catch (error) {
      next(error);
    }
  }

  async updateDesignation(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      if (!req.user) {
        throw new UnauthorizedError('Unauthorized to update the user');
      }

      const { id } = req.params;
      const user = await Users.findOne({
        orgId: req.user.orgId,
        _id: id,
        isDeleted: false,
      });

      if (!user) {
        throw new NotFoundError('User not found');
      }

      user.designation = req.body.designation;
      await user.save();

      await this.eventService.start();
      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: user.orgId.toString(),
          userId: user._id,
          fullName: user.fullName,
          ...(user.firstName && { firstName: user.firstName }),
          ...(user.lastName && { lastName: user.lastName }),
          ...(user.designation && { designation: user.designation }),
          email: user.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      res.json(user.toObject());
    } catch (error) {
      next(error);
    }
  }

  async updateEmail(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      if (!req.user) {
        throw new UnauthorizedError('Unauthorized to update the user');
      }

      const { id } = req.params;
      const user = await Users.findOne({
        orgId: req.user.orgId,
        _id: id,
        isDeleted: false,
      });

      if (!user) {
        throw new NotFoundError('User not found');
      }

      user.email = req.body.email;
      await user.save();

      await this.eventService.start();
      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: user.orgId.toString(),
          userId: user._id,
          fullName: user.fullName,
          ...(user.firstName && { firstName: user.firstName }),
          ...(user.lastName && { lastName: user.lastName }),
          ...(user.designation && { designation: user.designation }),
          email: user.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      res.json(user.toObject());
    } catch (error) {
      next(error);
    }
  }

  async deleteUser(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      if (!req.user) {
        throw new UnauthorizedError('Unauthorized to delete the user');
      }
      const { id } = req.params;

      const user = await Users.findOne({
        orgId: req.user.orgId,
        _id: id,
      });

      if (!user) {
        throw new NotFoundError('User not found');
      }

      const userId = user?._id;
      const orgId = user?.orgId;
      if (!userId || !orgId) {
        throw new NotFoundError('Account not found');
      }

      const groups = await UserGroups.find({
        orgId,
        users: { $in: [userId] },
        isDeleted: false,
      }).select('type');

      const isAdmin = groups.find(
        (userGroup: any) => userGroup.type === 'admin',
      );

      if (isAdmin) {
        throw new BadRequestError('Admin User deletion is not allowed');
      }

      await UserGroups.updateMany(
        { orgId, users: userId },
        { $pull: { users: userId } },
      );

      user.isDeleted = true;
      user.hasLoggedIn = false;
      user.deletedBy = req.user._id;

      await UserCredentials.updateOne(
        { userId },
        { $unset: { hashedPassword: '' } },
      );

      await user.save();

      await this.eventService.start();
      const event: Event = {
        eventType: EventType.DeleteUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: user.orgId.toString(),
          userId: user._id,
          email: user.email,
        } as UserDeletedEvent,
      };
      await this.eventService.publishEvent(event);
      await this.eventService.stop();

      res.json({ message: 'User deleted successfully' });
    } catch (error) {
      next(error);
    }
  }

  async updateUserDisplayPicture(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const dpFile = req.body.fileBuffer;
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;

      if (!dpFile) {
        throw new BadRequestError('DP File is required');
      }
      let quality = 100;
      let compressedImageBuffer = await sharp(dpFile.buffer)
        .jpeg({ quality })
        .toBuffer();
      while (compressedImageBuffer.length > 100 * 1024 && quality > 10) {
        quality -= 10;
        compressedImageBuffer = await sharp(dpFile.buffer)
          .jpeg({ quality })
          .toBuffer();
      }

      if (compressedImageBuffer.length > 100 * 1024) {
        throw new LargePayloadError('File too large , limit:1MB');
      }
      const compressedPic = compressedImageBuffer.toString('base64');
      const compressedPicMimeType = 'image/jpeg';

      await UserDisplayPicture.findOneAndUpdate(
        {
          orgId,
          userId,
        },
        {
          orgId,
          userId,
          pic: compressedPic,
          mimeType: compressedPicMimeType,
        },
        { new: true, upsert: true },
      );
      res.setHeader('Content-Type', compressedPicMimeType);
      res.status(201).send(compressedImageBuffer);
      return;
    } catch (error) {
      next(error);
    }
  }

  async getUserDisplayPicture(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;

      const userDp = await UserDisplayPicture.findOne({ orgId, userId })
        .lean()
        .exec();
      if (!userDp || !userDp.pic) {
        res.status(200).json({ errorMessage: 'User pic not found' });
        return;
      }

      const userDisplayBuffer = Buffer.from(userDp.pic, 'base64');
      if (userDp.mimeType) {
        res.setHeader('Content-Type', userDp.mimeType);
      }
      res.status(200).send(userDisplayBuffer);
      return;
    } catch (error) {
      next(error);
    }
  }

  async removeUserDisplayPicture(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;

      const userDp = await UserDisplayPicture.findOne({
        orgId,
        userId,
      }).exec();

      if (!userDp) {
        res
          .status(200)
          .json({ errorMessage: 'User display picture not found' });
        return;
      }

      userDp.pic = null;
      userDp.mimeType = null;

      await userDp.save();

      res.status(200).send(userDp);
    } catch (error) {
      next(error);
    }
  }

  async resendInvite(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { id } = req.params;
      if (!id) {
        throw new BadRequestError('Id is required');
      }

      if (!req.user) {
        throw new NotFoundError('User not found');
      }
      const org = await Org.findOne({ _id: req.user.orgId, isDeleted: false });
      const user = await Users.findOne({ _id: id, isDeleted: false });
      if (!user) {
        throw new UnauthorizedError('Error getting the user');
      }
      if (user?.hasLoggedIn) {
        throw new BadRequestError('User has already accepted the invite');
      }

      const email = user?.email;
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const authToken = fetchConfigJwtGenerator(
        userId,
        orgId,
        this.config.scopedJwtSecret,
      );
      let result = await this.authService.passwordMethodEnabled(authToken);

      if (result.statusCode !== 200) {
        throw new InternalServerError('Error fetching auth methods');
      }
      if (result.data?.isPasswordAuthEnabled) {
        const { passwordResetToken, mailAuthToken } =
          jwtGeneratorForNewAccountPassword(
            email,
            id,
            orgId,
            this.config.scopedJwtSecret,
          );

        result = await this.mailService.sendMail({
          emailTemplateType: 'appuserInvite',
          initiator: {
            jwtAuthToken: mailAuthToken,
          },
          usersMails: [email],
          subject: `You are invited to join ${org?.registeredName} `,
          templateData: {
            invitee: user?.fullName,
            orgName: org?.shortName || org?.registeredName,
            link: `${this.config.frontendUrl}/reset-password?token=${passwordResetToken}`,
          },
        });
        if (result.statusCode !== 200) {
          throw new InternalServerError('Error sending invite');
        }
      } else {
        result = await this.mailService.sendMail({
          emailTemplateType: 'appuserInvite',
          initiator: {
            jwtAuthToken: mailJwtGenerator(email, this.config.scopedJwtSecret),
          },
          usersMails: [email],
          subject: `You are invited to join ${org?.registeredName} `,
          templateData: {
            invitee: user?.fullName,
            orgName: org?.shortName || org?.registeredName,
            link: `${this.config.frontendUrl}/sign-in`,
          },
        });
        if (result.statusCode !== 200) {
          throw new InternalServerError('Error sending invite');
        }
      }

      res.status(200).json({ message: 'Invite sent successfully' });
      return;
    } catch (error) {
      next(error);
    }
  }

  async addManyUsers(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { emails } = req.body;
      const { groupIds } = req.body;

      if (!req.user) {
        throw new NotFoundError('User not found');
      }
      if (!emails) {
        throw new BadRequestError('emails are required');
      }

      const orgId = req.user?.orgId;
      const org = await Org.findOne({ _id: req.user.orgId, isDeleted: false });
      // Check if emails array is provided
      if (!emails || !Array.isArray(emails)) {
        throw new BadRequestError('Please provide an array of email addresses');
      }

      // Email validation regex
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

      // Validate all emails
      const invalidEmails = emails.filter((email) => !emailRegex.test(email));
      if (invalidEmails.length > 0) {
        throw new BadRequestError('Invalid emails are found');
      }

      // Find all users (both active and deleted) with the provided emails
      const existingUsers = await Users.find({
        email: { $in: emails },
      });
      // Separate active and deleted users
      const activeUsers = existingUsers.filter((user) => !user.isDeleted);
      const deletedUsers = existingUsers.filter((user) => user.isDeleted);

      const activeEmails = activeUsers.map((user) => user.email);
      const deletedEmails = deletedUsers.map((user) => user.email);

      // Restore deleted accounts
      let restoredUsers: User[] = [];
      if (deletedUsers.length > 0) {
        await Users.updateMany(
          {
            email: { $in: deletedEmails },
            isDeleted: true,
            orgId: req.user?.orgId,
          },
          {
            $set: {
              isDeleted: false,
            },
          },
        );

        // Fetch the restored users for response
        restoredUsers = await Users.find({
          email: { $in: deletedEmails },
        });
      }
      for (let i = 0; i < existingUsers.length; ++i) {
        const userId = existingUsers[i]?._id;

        await UserGroups.updateMany(
          { _id: { $in: groupIds }, orgId },
          { $addToSet: { users: userId } },
          { new: true },
        );

        await UserGroups.updateOne(
          { orgId: req.user?.orgId, type: 'everyone' }, // Find the everyone group in the same org
          { $addToSet: { users: userId } }, // Add user to the group if not already present
        );
      }

      // Filter emails that need new accounts
      // (excluding both active and restored accounts)
      const emailsForNewAccounts = emails.filter(
        (email) =>
          !activeEmails.includes(email) && !deletedEmails.includes(email),
      );

      // Create new users for remaining emails
      let newUsers: User[] = [];
      if (emailsForNewAccounts.length > 0) {
        newUsers = await Users.create(
          emailsForNewAccounts.map((email) => ({
            email,
            isDeleted: false,
            hasLoggedIn: false,
            orgId: req.user?.orgId,
          })),
        );
      }
      // If nothing was done, return 409
      if (newUsers.length === 0 && restoredUsers.length === 0) {
        res.status(200).json({
          errorMessage: 'All provided emails already have active accounts',
        });
        return;
      }
      let errorSendingMail = false;

      await this.eventService.start();
      for (let i = 0; i < emailsForNewAccounts.length; ++i) {
        const email = emailsForNewAccounts[i];
        const userId = newUsers[i]?._id;
        if (!userId) {
          throw new InternalServerError(
            'User ID missing while inviting restored user. Please ensure user restoration was successful.',
          );
        }
        await UserGroups.updateMany(
          { _id: { $in: groupIds }, orgId },
          { $addToSet: { users: userId } },
          { new: true },
        );

        await UserGroups.updateOne(
          { orgId: req.user?.orgId, type: 'everyone' }, // Find the everyone group in the same org
          { $addToSet: { users: userId } }, // Add user to the group if not already present
        );

        const event: Event = {
          eventType: EventType.NewUserEvent,
          timestamp: Date.now(),
          payload: {
            orgId: req.user?.orgId.toString(),
            userId: userId,
            email: email,
            syncAction: SyncAction.Immediate,
          } as UserAddedEvent,
        };
        await this.eventService.publishEvent(event);

        const authToken = fetchConfigJwtGenerator(
          userId.toString(),
          req.user?.orgId,
          this.config.scopedJwtSecret,
        );
        let result = await this.authService.passwordMethodEnabled(authToken);

        if (result.statusCode !== 200) {
          throw new InternalServerError('Error fetching auth methods');
        }

        if (result.data?.isPasswordAuthEnabled) {
          const { passwordResetToken, mailAuthToken } =
            jwtGeneratorForNewAccountPassword(
              email,
              userId.toString(),
              orgId,
              this.config.scopedJwtSecret,
            );

          result = await this.mailService.sendMail({
            emailTemplateType: 'appuserInvite',
            initiator: {
              jwtAuthToken: mailAuthToken,
            },
            usersMails: [email],
            subject: `You are invited to join ${org?.registeredName} `,
            templateData: {
              invitee: req.user?.fullName,
              orgName: org?.shortName || org?.registeredName,
              link: `${this.config.frontendUrl}/reset-password?token=${passwordResetToken}`,
            },
          });
          if (result.statusCode !== 200) {
            errorSendingMail = true;
            continue;
          }
        } else {
          result = await this.mailService.sendMail({
            emailTemplateType: 'appuserInvite',
            initiator: {
              jwtAuthToken: mailJwtGenerator(
                email,
                this.config.scopedJwtSecret,
              ),
            },
            usersMails: [email],
            subject: `You are invited to join ${org?.registeredName} `,
            templateData: {
              invitee: req.user?.fullName,
              orgName: org?.shortName || org?.registeredName,
              link: `${this.config.frontendUrl}/sign-in`,
            },
          });
          if (result.statusCode !== 200) {
            errorSendingMail = true;
            continue;
          }
        }
      }

      const emailsForRestoredAccounts = restoredUsers.map((user) => user.email);

      for (let i = 0; i < emailsForRestoredAccounts.length; ++i) {
        const email = emailsForRestoredAccounts[i];
        const userId = restoredUsers[i]?._id;

        if (!email) {
          continue;
        }
        if (!userId) {
          throw new InternalServerError(
            'User ID missing while inviting restored user. Please ensure user restoration was successful.',
          );
        }
        const event: Event = {
          eventType: EventType.NewUserEvent,
          timestamp: Date.now(),
          payload: {
            orgId: req.user?.orgId.toString(),
            userId: userId,
            email: email,
            syncAction: SyncAction.Immediate,
          } as UserAddedEvent,
        };
        await this.eventService.publishEvent(event);

        const authToken = fetchConfigJwtGenerator(
          userId.toString(),
          req.user?.orgId,
          this.config.scopedJwtSecret,
        );
        let result = await this.authService.passwordMethodEnabled(authToken);

        if (result.statusCode !== 200) {
          throw new InternalServerError('Error fetching auth methods');
        }

        if (result.data?.isPasswordAuthEnabled) {
          const { passwordResetToken, mailAuthToken } =
            jwtGeneratorForNewAccountPassword(
              email,
              userId.toString(),
              orgId,
              this.config.scopedJwtSecret,
            );

          result = await this.mailService.sendMail({
            emailTemplateType: 'appuserInvite',
            initiator: {
              jwtAuthToken: mailAuthToken,
            },
            usersMails: [email],
            subject: `You are invited to re-join ${org?.registeredName} `,
            templateData: {
              invitee: req.user?.fullName,
              orgName: org?.shortName || org?.registeredName,
              link: `${this.config.frontendUrl}/reset-password?token=${passwordResetToken}`,
            },
          });
          if (result.statusCode !== 200) {
            errorSendingMail = true;
            continue;
          }
        } else {
          result = await this.mailService.sendMail({
            emailTemplateType: 'appuserInvite',
            initiator: {
              jwtAuthToken: mailJwtGenerator(
                email,
                this.config.scopedJwtSecret,
              ),
            },
            usersMails: [email],
            subject: `You are invited to re-join ${org?.registeredName} `,
            templateData: {
              invitee: req.user?.fullName,
              orgName: org?.shortName || org?.registeredName,
              link: `${this.config.frontendUrl}/sign-in`,
            },
          });
          if (result.statusCode !== 200) {
            errorSendingMail = true;
            continue;
          }
        }
      }

      await this.eventService.stop();

      if (errorSendingMail) {
        res.status(200).json({
          message: 'Error sending mail invite. Check your SMTP configuration.',
        });
        return;
      }

      res.status(200).json({ message: 'Invite sent successfully' });
    } catch (error) {
      next(error);
    }
  }

  async listUsers(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }
      
      const { page, limit, search } = req.query;
      const queryParams = new URLSearchParams();
      if (page) queryParams.append('page', String(page));
      if (limit) queryParams.append('limit', String(limit));
      if (search) queryParams.append('search', String(search));
      const queryString = queryParams.toString();

      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/user/list?${queryString}`,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        method: HttpMethod.GET,
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (aiResponse && aiResponse.statusCode !== 200) {
        throw new BadRequestError('Failed to get users');
      }
      const users = aiResponse.data;
      res.status(HTTP_STATUS.OK).json(users);
    } catch (error: any) {
      this.logger.error('Error getting users', {
        requestId,
        message: 'Error getting users',
        error: error.message,
      });
      next(error);
    }
  }

  async getUserTeams(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }
      const { page, limit, search } = req.query;
      let queryString = '';
      if (page) {
        queryString += `&page=${page}`;
      }
      if (limit) {
        queryString += `&limit=${limit}`;
      }
      if (search) {
        queryString += `&search=${search}`;
      }
      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/user/teams?${queryString}`,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        method: HttpMethod.GET,
      };
      const aiCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiCommand.execute();
      if (aiResponse && aiResponse.statusCode !== 200) {
        throw new BadRequestError('Failed to get user teams');
      }
      const userTeams = aiResponse.data;
      res.status(HTTP_STATUS.OK).json(userTeams);
    } catch (error: any) {
      this.logger.error('Error getting user teams', {
        requestId,
        message: 'Error getting user teams',
        error: error.message,
      });
      next(error);
    }
  }

  /**
   * Extract user details from SAML assertion with fallbacks for different IdP formats
   */
  private extractSamlUserDetails(samlUser: any, email: string) {
    const SAML_CLAIM_GIVENNAME =
      'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname';
    const SAML_OID_GIVENNAME = 'urn:oid:2.5.4.42';
    const SAML_CLAIM_SURNAME =
      'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname';
    const SAML_OID_SURNAME = 'urn:oid:2.5.4.4';
    const SAML_CLAIM_NAME =
      'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name';
    const SAML_OID_DISPLAYNAME = 'urn:oid:2.16.840.1.113730.3.1.241';

    // Try multiple SAML attribute names for first name
    const firstName =
      samlUser.firstName ||
      samlUser.givenName ||
      samlUser[SAML_CLAIM_GIVENNAME] ||
      samlUser[SAML_OID_GIVENNAME];

    // Try multiple SAML attribute names for last name
    const lastName =
      samlUser.lastName ||
      samlUser.surname ||
      samlUser.sn ||
      samlUser[SAML_CLAIM_SURNAME] ||
      samlUser[SAML_OID_SURNAME];

    // Try multiple SAML attribute names for display name
    const displayName =
      samlUser.displayName ||
      samlUser.name ||
      samlUser.fullName ||
      samlUser[SAML_CLAIM_NAME] ||
      samlUser[SAML_OID_DISPLAYNAME];

    // Construct full name with fallbacks
    const fullName =
      displayName ||
      [firstName, lastName].filter(Boolean).join(' ') ||
      email.split('@')[0];

    return {
      firstName: firstName || undefined,
      lastName: lastName || undefined,
      fullName,
    };
  }
}
