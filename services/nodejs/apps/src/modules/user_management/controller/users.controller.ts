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
} from '../services/entity_events.service';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { UserGroups } from '../schema/userGroup.schema';
import { AuthService } from '../services/auth.service';
import { Org } from '../schema/org.schema';
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
          syncAction: 'immediate',
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
          orgId: req.user.orgId,
          userId: req.user._id,
          fullName: req.user.fullName,
          ...(req.user.firstName && { firstName: req.user.firstName }),
          ...(req.user.lastName && { lastName: req.user.lastName }),
          ...(req.user.designation && { designation: req.user.designation }),
          email: req.user.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();

      await req.user.save(); // Save the updated user
      res.json(req.user.toObject());
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
      req.user.fullName = req.body.fullName;
      await this.eventService.start();
      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: req.user.orgId,
          userId: req.user._id,
          fullName: req.user.fullName,
          ...(req.user.firstName && { firstName: req.user.firstName }),
          ...(req.user.lastName && { lastName: req.user.lastName }),
          ...(req.user.designation && { designation: req.user.designation }),
          email: req.user.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      await req.user.save();
      res.json(req.user);
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
      const { firstName } = req.body;
      req.user.firstName = firstName;
      await this.eventService.start();
      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: req.user.orgId,
          userId: req.user._id,
          fullName: req.user.fullName,
          ...(req.user.firstName && { firstName: req.user.firstName }),
          ...(req.user.lastName && { lastName: req.user.lastName }),
          ...(req.user.designation && { designation: req.user.designation }),
          email: req.user.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      await req.user.save();
      res.json(req.user);
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
      const { lastName } = req.body;
      req.user.lastName = lastName;
      await this.eventService.start();
      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: req.user.orgId,
          userId: req.user._id,
          fullName: req.user.fullName,
          ...(req.user.firstName && { firstName: req.user.firstName }),
          ...(req.user.lastName && { lastName: req.user.lastName }),
          ...(req.user.designation && { designation: req.user.designation }),
          email: req.user.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      await req.user.save();
      res.json(req.user);
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
      const { designation } = req.body;

      req.user.designation = designation;
      await this.eventService.start();
      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: req.user.orgId,
          userId: req.user._id,
          fullName: req.user.fullName,
          ...(req.user.firstName && { firstName: req.user.firstName }),
          ...(req.user.lastName && { lastName: req.user.lastName }),
          ...(req.user.designation && { designation: req.user.designation }),
          email: req.user.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      await req.user.save();
      res.json(req.user);
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
      const { email } = req.body;
      req.user.email = email;
      await this.eventService.start();
      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: req.user.orgId,
          userId: req.user._id,
          fullName: req.user.fullName,
          ...(req.user.firstName && { firstName: req.user.firstName }),
          ...(req.user.lastName && { lastName: req.user.lastName }),
          ...(req.user.designation && { designation: req.user.designation }),
          email: req.user.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      await req.user.save();
      res.json({ message: 'User Email updated successfully' });
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
      req.user.isDeleted = true;
      req.user.deletedBy = req.user._id;

      await this.eventService.start();
      const event: Event = {
        eventType: EventType.DeleteUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: req.user.orgId.toString(),
          userId: req.user._id,
          email: req.user.email,
        } as UserDeletedEvent,
      };
      await this.eventService.publishEvent(event);
      await this.eventService.stop();

      await req.user.save();
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
              jwtAuthToken: mailJwtGenerator(
                email,
                this.config.scopedJwtSecret,
              ),
            },
            usersMails: [email],
            subject: `You are invited to join ${org?.registeredName} `,
            templateData: {
              invitee: req.user?.fullName,
              link: `${this.config.frontendUrl}/sign-in`,
            },
          });
          if (result.statusCode !== 200) {
            throw new InternalServerError('Error sending invite');
          }
        }

        const event: Event = {
          eventType: EventType.NewUserEvent,
          timestamp: Date.now(),
          payload: {
            orgId: req.user?.orgId.toString(),
            userId: userId,
            email: email,
            syncAction: 'immediate',
          } as UserAddedEvent,
        };

        await this.eventService.publishEvent(event);
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
              jwtAuthToken: mailJwtGenerator(
                email,
                this.config.scopedJwtSecret,
              ),
            },
            usersMails: [email],
            subject: `You are invited to re-join ${org?.registeredName} `,
            templateData: {
              invitee: req.user?.fullName,
              link: `${this.config.frontendUrl}/sign-in`,
            },
          });
          if (result.statusCode !== 200) {
            throw new InternalServerError('Error sending invite');
          }
        }

        const event: Event = {
          eventType: EventType.NewUserEvent,
          timestamp: Date.now(),
          payload: {
            orgId: req.user?.orgId.toString(),
            userId: userId,
            email: email,
            syncAction: 'immediate',
          } as UserAddedEvent,
        };

        await this.eventService.publishEvent(event);
        res.status(200).json({ message: 'Invite sent successfully' });
        return;
      }
      await this.eventService.stop();
      res.status(200).json({ message: 'Invite sent successfully' });
    } catch (error) {
      next(error);
    }
  }
}
