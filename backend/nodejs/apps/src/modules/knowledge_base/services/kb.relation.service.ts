import { inject, injectable } from 'inversify';
import { v4 as uuidv4 } from 'uuid';
import { ArangoService } from '../../../libs/services/arango.service';
import { DocumentCollection, EdgeCollection } from 'arangojs/collections';
import { Database } from 'arangojs';
import { aql } from 'arangojs/aql';
import { Logger } from '../../../libs/services/logger.service';
import {
  COLLECTIONS,
  ENTITY_TYPE,
  RELATIONSHIP_TYPE,
} from '../constants/record.constants';
import { IRecordDocument } from '../types/record';
import { IFileRecordDocument } from '../types/file_record';
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import {
  DeletedRecordEvent,
  Event,
  EventType,
  NewRecordEvent,
  RecordsEventProducer,
  UpdateRecordEvent,
} from './records_events.service';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { endpoint } from '../../storage/constants/constants';
import { DefaultStorageConfig } from '../../tokens_manager/services/cm.service';
import { configPaths } from '../../configuration_manager/paths/paths';
import { storageTypes } from '../../configuration_manager/constants/constants';
import {
  ReindexAllRecordEvent,
  SyncEventProducer,
  Event as SyncEvent,
  BaseSyncEvent,
} from './sync_events.service';
import {
  IServiceFileRecord,
  IServiceRecord,
  IServiceRecordsResponse,
} from '../types/service.records.response';

const logger = Logger.getInstance({
  service: 'Knowledge Base Service',
});

@injectable()
export class RecordRelationService {
  private db: Database;
  private recordCollection: DocumentCollection;
  private fileRecordCollection: DocumentCollection;
  private userCollection: DocumentCollection;
  private kbCollection: DocumentCollection;
  private recordToRecordEdges: EdgeCollection;
  private isOfTypeEdges: EdgeCollection;
  private permissionEdges: EdgeCollection;
  private kbToRecordEdges: EdgeCollection;
  private userToRecordEdges: EdgeCollection;

  constructor(
    @inject(ArangoService) private readonly arangoService: ArangoService,
    @inject(RecordsEventProducer) readonly eventProducer: RecordsEventProducer,
    @inject(SyncEventProducer) readonly syncEventProducer: SyncEventProducer,
    private readonly defaultConfig: DefaultStorageConfig,
  ) {
    this.db = this.arangoService.getConnection();

    // Document collections
    this.recordCollection = this.db.collection(COLLECTIONS.RECORDS);
    this.fileRecordCollection = this.db.collection(COLLECTIONS.FILES);
    this.userCollection = this.db.collection(COLLECTIONS.USERS);
    this.kbCollection = this.db.collection(COLLECTIONS.KNOWLEDGE_BASE);

    // Edge collections
    this.recordToRecordEdges = this.db.collection(
      COLLECTIONS.RECORD_TO_RECORD,
    ) as EdgeCollection;
    this.isOfTypeEdges = this.db.collection(
      COLLECTIONS.IS_OF_TYPE,
    ) as EdgeCollection;
    this.permissionEdges = this.db.collection(
      COLLECTIONS.PERMISSIONS_TO_KNOWLEDGE_BASE,
    ) as EdgeCollection;
    this.kbToRecordEdges = this.db.collection(
      COLLECTIONS.BELONGS_TO_KNOWLEDGE_BASE,
    ) as EdgeCollection;
    this.userToRecordEdges = this.db.collection(
      COLLECTIONS.PERMISSIONS,
    ) as EdgeCollection;

    this.initializeEventProducer();
    this.initializeSyncEventProducer();
  }

  /**
   * Checks if a record exists in ArangoDB with linear backoff retry
   * @param recordKey The key of the record to check
   * @returns Promise<boolean> True if record exists, false otherwise
   */
  public async checkRecordExists(recordKey: string): Promise<boolean> {
    const maxRetries = 5;
    const baseDelay = 3000; // 3 second base delay
    let recordExists = false;

    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        const aql = `
          FOR doc IN records
          FILTER doc._key == @recordKey
          RETURN doc
        `;
        const bindVars = { recordKey };
        const result = await this.db.query(aql, bindVars);
        const records = await result.all();

        if (records && records.length > 0) {
          recordExists = true;
          break;
        }
        // Linear backoff
        const delay = baseDelay * attempt;
        await new Promise((resolve) => setTimeout(resolve, delay));
        logger.debug(`Retry attempt ${attempt} checking record existence`, {
          recordId: recordKey,
          attempt,
          delay,
        });
      } catch (error) {
        logger.error(`Error checking record existence on attempt ${attempt}`, {
          recordId: recordKey,
          error,
          attempt,
        });
      }
    }

    if (!recordExists) {
      logger.warn(
        `Record ${recordKey} not found in database after ${maxRetries} attempts`,
      );
    }

    return recordExists;
  }

  private async initializeEventProducer() {
    try {
      await this.eventProducer.start();
      logger.info('Event producer initialized successfully');
    } catch (error) {
      logger.error('Failed to initialize event producer', error);
      throw new InternalServerError(
        error instanceof Error
          ? error.message
          : 'Failed to initialize event producer',
      );
    }
  }

  private async initializeSyncEventProducer() {
    try {
      await this.syncEventProducer.start();
      logger.info('Sync Event producer initialized successfully');
    } catch (error) {
      logger.error('Failed to initialize Sync event producer', error);
      throw new InternalServerError(
        `Failed to initialize sync event producer: ${error instanceof Error ? error.message : 'Unknown error'}`,
      );
    }
  }

  /**
   * Publishes events for multiple records and their associated file records
   * @param records The inserted records
   * @param fileRecords The associated file records
   */
  async publishRecordEvents(
    records: IRecordDocument[],
    fileRecords: IFileRecordDocument[],
    keyValueStoreService: KeyValueStoreService,
  ): Promise<void> {
    try {
      // Create a batch of promises for event publishing
      const publishPromises = records.map(async (record, index) => {
        try {
          // Create the payload using the separate function
          const newRecordPayload = await this.createNewRecordEventPayload(
            record,
            keyValueStoreService,
            fileRecords[index],
          );

          const event: Event = {
            eventType: EventType.NewRecordEvent,
            timestamp: Date.now(),
            payload: newRecordPayload,
          };

          // Return the promise for event publishing
          return this.eventProducer.publishEvent(event);
        } catch (error) {
          logger.error(
            `Failed to create event payload for record ${record._key}`,
            { error },
          );
          // Return a resolved promise to avoid failing the Promise.all
          return Promise.resolve();
        }
      });

      // Execute all publish operations concurrently
      await Promise.all(publishPromises);
      logger.info(`Published events for ${records.length} records`);
    } catch (error) {
      // Log but don't throw the error to avoid affecting the main operation
      logger.error('Error publishing batch record events', { error });
    }
  }

  /**
   * Creates a standardized new record event payload considering both record and file record information
   * @param record The record document
   * @param fileRecord Optional associated file record for additional metadata
   * @returns NewRecordEvent payload for Kafka
   */
  async createNewRecordEventPayload(
    record: IRecordDocument,
    keyValueStoreService: KeyValueStoreService,
    fileRecord?: IFileRecordDocument,
  ): Promise<NewRecordEvent> {
    // Generate signed URL route based on record information

    const url = (await keyValueStoreService.get<string>(endpoint)) || '{}';

    const storageUrl =
      JSON.parse(url).storage.endpoint || this.defaultConfig.endpoint;
    const signedUrlRoute = `${storageUrl}/api/v1/document/internal/${record.externalRecordId}/download`;

    // Determine the appropriate extension by prioritizing different sources
    let extension = '';
    if (fileRecord && fileRecord.extension) {
      extension = fileRecord.extension;
    }
    let mimeType = '';
    if (fileRecord && fileRecord.mimeType) {
      mimeType = fileRecord.mimeType;
    }

    return {
      orgId: record.orgId,
      recordId: record._key,
      recordName: record.recordName,
      recordType: record.recordType,
      version: record.version || 1,
      signedUrlRoute: signedUrlRoute,
      origin: record.origin,
      extension: extension,
      mimeType: mimeType,
      createdAtTimestamp: (record.createdAtTimestamp || Date.now()).toString(),
      updatedAtTimestamp: (record.updatedAtTimestamp || Date.now()).toString(),
      sourceCreatedAtTimestamp: (
        record.sourceCreatedAtTimestamp ||
        record.createdAtTimestamp ||
        Date.now()
      ).toString(),
    };
  }

  /**
   * Creates a standardized update record event payload
   * @param record The updated record
   * @returns UpdateRecordEvent payload for Kafka
   */
  async createUpdateRecordEventPayload(
    record: IRecordDocument,
    fileRecord: IFileRecordDocument,
    keyValueStoreService: KeyValueStoreService,
  ): Promise<UpdateRecordEvent> {
    // Generate signed URL route based on record information
    const url = (await keyValueStoreService.get<string>(endpoint)) || '{}';

    const storageUrl =
      JSON.parse(url).storage.endpoint || this.defaultConfig.endpoint;
    const signedUrlRoute = `${storageUrl}/api/v1/document/internal/${record.externalRecordId}/download`;
    let extension = '';
    if (fileRecord && fileRecord.extension) {
      extension = fileRecord.extension;
    }
    let mimeType = '';
    if (fileRecord && fileRecord.mimeType) {
      mimeType = fileRecord.mimeType;
    }
    return {
      orgId: record.orgId,
      recordId: record._key,
      version: record.version || 1,
      signedUrlRoute: signedUrlRoute,
      extension: extension,
      mimeType: mimeType,
      summaryDocumentId: record.summaryDocumentId,
      updatedAtTimestamp: (record.updatedAtTimestamp || Date.now()).toString(),
      sourceLastModifiedTimestamp: (
        record.sourceLastModifiedTimestamp ||
        record.updatedAtTimestamp ||
        Date.now()
      ).toString(),
      virtualRecordId: record.virtualRecordId,
    };
  }

  /**
   * Creates a standardized delete record event payload
   * @param record The record being deleted
   * @param userId The user performing the deletion
   * @returns DeletedRecordEvent payload for Kafka
   */
  createDeletedRecordEventPayload(
    record: IRecordDocument | IServiceRecord,
    fileRecord: IFileRecordDocument | IServiceFileRecord,
  ): DeletedRecordEvent {
    let extension = '';
    if (fileRecord && fileRecord.extension) {
      extension = fileRecord.extension;
    }
    let mimeType = '';
    if (fileRecord && fileRecord.mimeType) {
      mimeType = fileRecord.mimeType;
    }
    return {
      orgId: record.orgId,
      recordId: record._key,
      version: record.version || 1,
      extension: extension,
      mimeType: mimeType,
      summaryDocumentId: record.summaryDocumentId,
      virtualRecordId: record.virtualRecordId,
    };
  }

  /**
   * Get or create a knowledge base for a specific organization
   * @param orgId The organization ID
   * @param name Optional name for the knowledge base
   * @returns The knowledge base document
   */
  async getOrCreateKnowledgeBase(
    userId: string,
    orgId: string,
    name: string = 'Default',
  ): Promise<any> {
    try {
      if (!userId || !orgId) {
        throw new NotFoundError(
          'Both User ID and Organization ID are required to get or create a knowledge base',
        );
      }

      // Check if a knowledge base already exists for this organization
      const cursor = await this.db.query(aql`
        FOR kb IN ${this.kbCollection}
          FILTER kb.userId == ${userId} AND kb.orgId == ${orgId} AND kb.isDeleted == false
          RETURN kb
      `);

      const existingKBs = await cursor.all();

      if (existingKBs.length > 0) {
        logger.info(
          `Found existing knowledge base for user ${userId} in organization ${orgId}`,
        );
        return existingKBs[0];
      }

      // Create a new knowledge base
      const currentTime = Date.now();
      const kb = {
        userId,
        orgId,
        name,
        createdAtTimestamp: currentTime,
        updatedAtTimestamp: currentTime,
        isDeleted: false,
        isArchived: false,
      };

      const result = await this.kbCollection.save(kb);
      logger.info(`Created new knowledge base for organization ${orgId}`);

      return {
        ...kb,
        _id: result._id,
        _key: result._key,
        _rev: result._rev,
      };
    } catch (error) {
      logger.error('Failed to get or create knowledge base', {
        orgId,
        error,
      });
      throw error;
    }
  }

  /**
   * Check if a knowledge base exists for a specific organization
   * @param orgId The organization ID
   * @returns An object with exists flag and the knowledge base document if found
   */
  async checkKBExists(
    userId: string,
    orgId: string,
  ): Promise<{ exists: boolean; knowledgeBase?: any }> {
    try {
      if (!userId || !orgId) {
        throw new NotFoundError(
          'User ID and Organization ID are required to check if a knowledge base exists',
        );
      }

      // Check if a knowledge base already exists for this organization
      const cursor = await this.db.query(aql`
        FOR kb IN ${this.kbCollection}
          FILTER kb.userId == ${userId} AND kb.orgId == ${orgId} AND kb.isDeleted == false
          RETURN kb
      `);

      const existingKBs = await cursor.all();

      if (existingKBs.length > 0) {
        logger.info(
          `Found existing knowledge base for user ${userId} in organization ${orgId}`,
        );
        return {
          exists: true,
          knowledgeBase: existingKBs[0],
        };
      }

      // No knowledge base found
      logger.info(
        `No knowledge base found for user ${userId} in organization ${orgId}`,
      );
      return { exists: false };
    } catch (error) {
      logger.error('Failed to check if knowledge base exists', {
        orgId,
        error,
      });
      throw error;
    }
  }

  /**
   * Inserts a record document into the record collection
   * @param record The record document to insert
   * @returns The inserted record document
   */
  async insertRecord(record: IRecordDocument): Promise<IRecordDocument> {
    try {
      const result = await this.recordCollection.save(record, {
        returnNew: true,
      });
      logger.info(`Inserted record with ID ${result._key}`);
      return result.new as IRecordDocument;
    } catch (error) {
      logger.error('Failed to insert record', { record, error });
      throw error;
    }
  }

  /**
   * Inserts a file record document into the file record collection
   * @param fileRecord The file record document to insert
   * @returns The inserted file record document
   */
  async insertFileRecord(
    fileRecord: IFileRecordDocument,
  ): Promise<IFileRecordDocument> {
    try {
      const result = await this.fileRecordCollection.save(fileRecord, {
        returnNew: true,
      });
      logger.info(`Inserted file record with ID ${result._key}`);
      return result.new as IFileRecordDocument;
    } catch (error) {
      logger.error('Failed to insert file record', { fileRecord, error });
      throw error;
    }
  }

  /**
   * Inserts multiple records and their associated file records in a transaction
   * @param records Array of record documents
   * @param fileRecords Array of file record documents
   * @returns Object containing arrays of inserted records and file records
   */
  async insertRecordsAndFileRecords(
    records: IRecordDocument[],
    fileRecords: IFileRecordDocument[],
    keyValueStoreService: KeyValueStoreService,
  ): Promise<{
    insertedRecords: IRecordDocument[];
    insertedFileRecords: IFileRecordDocument[];
  }> {
    if (records.length !== fileRecords.length) {
      throw new BadRequestError(
        'Records and file records arrays must be of the same length',
      );
    }

    const trx = await this.db.beginTransaction({
      write: [COLLECTIONS.RECORDS, COLLECTIONS.FILES],
    });

    try {
      // Insert records
      const insertedRecords: IRecordDocument[] = [];
      for (const record of records) {
        const recordResult = await trx.step(() =>
          this.recordCollection.save(record, { returnNew: true }),
        );

        insertedRecords.push(recordResult.new as IRecordDocument);
      }

      // Insert file records
      const insertedFileRecords: IFileRecordDocument[] = [];
      for (let i = 0; i < fileRecords.length; i++) {
        const fileRecord: IFileRecordDocument = {
          ...fileRecords[i],
          _key: insertedRecords[i]?._key,
        } as IFileRecordDocument;

        const fileRecordResult = await trx.step(() =>
          this.fileRecordCollection.save(fileRecord, { returnNew: true }),
        );
        insertedFileRecords.push(fileRecordResult.new as IFileRecordDocument);
      }

      // Commit the transaction
      await trx.commit();

      logger.info(
        `Successfully inserted ${insertedRecords.length} records and file records`,
      );

      const storageConfig =
        (await keyValueStoreService.get<string>(configPaths.storageService)) ||
        '{}';

      const parsedConfig = JSON.parse(storageConfig); // Parse JSON string

      const storageType = parsedConfig.storageType;
      if (storageType === storageTypes.LOCAL) {
        await this.publishRecordEvents(
          insertedRecords,
          insertedFileRecords,
          keyValueStoreService,
        );
      }
      return { insertedRecords, insertedFileRecords };
    } catch (error) {
      // Try to abort the transaction
      try {
        await trx.abort();
      } catch (abortError: any) {
        if (!abortError.message?.includes('already committed')) {
          logger.error('Error aborting transaction', { error: abortError });
        }
      }

      logger.error('Failed to insert records and file records', { error });
      throw error;
    }
  }

  /**
   * Creates a relationship between two records
   */
  async createRecordRelationship(
    fromRecordId: string,
    toRecordId: string,
    relationshipType: string,
  ): Promise<string> {
    try {
      const fromHandle = `${COLLECTIONS.RECORDS}/${fromRecordId}`;
      const toHandle = `${COLLECTIONS.RECORDS}/${toRecordId}`;

      // Check if relationship already exists
      const cursor = await this.db.query(aql`
        FOR edge IN ${this.recordToRecordEdges}
          FILTER edge._from == ${fromHandle} AND edge._to == ${toHandle}
          RETURN edge
      `);

      const existing = await cursor.all();

      if (existing.length > 0) {
        // Relationship already exists
        logger.info(
          `Relationship between records ${fromRecordId} and ${toRecordId} already exists`,
        );
        return existing[0]._key;
      }

      // Create the edge
      const edge = {
        _from: fromHandle,
        _to: toHandle,
        relationshipType,
        createdAtTimestamp: Date.now(),
        updatedAtTimestamp: Date.now(),
      };

      const result = await this.recordToRecordEdges.save(edge);
      logger.info(
        `Created relationship between records ${fromRecordId} and ${toRecordId}`,
      );

      return result._key;
    } catch (error) {
      logger.error('Failed to create record relationship', {
        fromRecordId,
        toRecordId,
        relationshipType,
        error,
      });
      throw error;
    }
  }

  /**
   * Creates a relationship between a record and its file record using the is_of_type edge
   */
  async createRecordToFileRecordRelationship(
    recordId: string,
    fileRecordId: string,
  ): Promise<string> {
    try {
      const recordHandle = `${COLLECTIONS.RECORDS}/${recordId}`;
      const fileRecordHandle = `${COLLECTIONS.FILES}/${fileRecordId}`;

      // Check if relationship already exists
      const cursor = await this.db.query(aql`
          FOR edge IN ${this.isOfTypeEdges}
            FILTER edge._from == ${recordHandle} AND edge._to == ${fileRecordHandle}
            RETURN edge
        `);

      const existing = await cursor.all();

      if (existing.length > 0) {
        // Relationship already exists
        logger.info(
          `Relationship between record ${recordId} and file record ${fileRecordId} already exists`,
        );
        return existing[0]._key;
      }

      const currentTime = Date.now();

      // Create the edge
      const edge = {
        _from: recordHandle,
        _to: fileRecordHandle,
        createdAtTimestamp: currentTime,
        updatedAtTimestamp: currentTime,
      };

      const result = await this.isOfTypeEdges.save(edge);
      logger.info(
        `Created is_of_type relationship between record ${recordId} and file record ${fileRecordId}`,
      );

      return result._key;
    } catch (error) {
      logger.error('Failed to create record-to-file-record relationship', {
        recordId,
        fileRecordId,
        error,
      });
      throw error;
    }
  }

  /**
   * Add a record to a knowledge base with a belongs_to relationship
   */
  async addRecordToKnowledgeBase(
    kbId: string,
    recordId: string,
  ): Promise<string> {
    try {
      const kbHandle = `${COLLECTIONS.KNOWLEDGE_BASE}/${kbId}`;
      const recordHandle = `${COLLECTIONS.RECORDS}/${recordId}`;

      // Check if relationship already exists
      const cursor = await this.db.query(aql`
        FOR edge IN ${this.kbToRecordEdges}
          FILTER edge._from == ${recordHandle} AND edge._to == ${kbHandle}
          RETURN edge
      `);

      const existing = await cursor.all();

      if (existing.length > 0) {
        // Relationship already exists
        logger.info(
          `Record ${recordId} is already part of knowledge base ${kbId}`,
        );
        return existing[0]._key;
      }

      const currentTime = Date.now();

      // Create the edge
      const edge = {
        _from: recordHandle,
        _to: kbHandle,
        entityType: ENTITY_TYPE.KNOWLEDGE_BASE,
        createdAtTimestamp: currentTime,
        updatedAtTimestamp: currentTime,
        isDeleted: false,
      };

      const result = await this.kbToRecordEdges.save(edge);
      logger.info(`Added record ${recordId} to knowledge base ${kbId}`);

      return result._key;
    } catch (error) {
      logger.error('Failed to add record to knowledge base', {
        kbId,
        recordId,
        error,
      });
      throw error;
    }
  }

  /**
   * Create a permission relationship between a knowledge base and a user
   */
  async createKbUserPermission(
    kbId: string,
    userId: string,
    type: string = RELATIONSHIP_TYPE.USER,
    role: string = 'OWNER',
  ): Promise<string> {
    try {
      const kbHandle = `${COLLECTIONS.KNOWLEDGE_BASE}/${kbId}`;
      const userHandle = `${COLLECTIONS.USERS}/${userId}`;

      // Check if permission already exists
      const cursor = await this.db.query(aql`
        FOR edge IN ${this.permissionEdges}
          FILTER edge._from == ${userHandle} AND edge._to == ${kbHandle}
          RETURN edge
      `);

      const existing = await cursor.all();

      if (existing.length > 0) {
        // Permission already exists
        logger.info(
          `User ${userId} already has permissions for knowledge base ${kbId}`,
        );
        return existing[0]._key;
      }

      const currentTime = Date.now();

      // Create the edge
      const edge = {
        _from: userHandle,
        _to: kbHandle,
        externalPermissionId: '',
        type,
        role,
        createdAtTimestamp: currentTime,
        updatedAtTimestamp: currentTime,
        lastUpdatedTimestampAtSource: currentTime,
      };

      const result = await this.permissionEdges.save(edge);
      logger.info(
        `Created ${role} permission for user ${userId} on knowledge base ${kbId}`,
      );

      return result._key;
    } catch (error) {
      logger.error('Failed to create knowledge base permission', {
        kbId,
        userId,
        role,
        error,
      });
      throw error;
    }
  }

  /**
   * Finds or creates a user in the user collection based on userId and orgId
   * @param userId External user ID reference
   * @param email User's email
   * @param orgId Organization ID
   * @param firstName User's first name
   * @param lastName User's last name
   * @param designation User's designation/title
   * @returns The found or created user document
   */
  async findOrCreateUser(
    userId: string,
    email: string,
    orgId: string,
    firstName?: string,
    lastName?: string,
    middleName?: string,
    designation?: string,
  ): Promise<any> {
    try {
      // Validate required parameters
      if (!userId) {
        throw new NotFoundError('UserId is required to find or create a user');
      }

      if (!orgId) {
        throw new NotFoundError(
          'Organization ID is required to find or create a user',
        );
      }

      if (!email) {
        logger.warn('Email is missing for user creation', { userId, orgId });
        // Use a default email if none provided
        throw new NotFoundError('Email is required');
      }

      // First, try to find the user by both userId and orgId
      const userByIdCursor = await this.db.query(aql`
      FOR user IN ${this.userCollection}
        FILTER user.userId == ${userId} AND user.orgId == ${orgId} AND user.isActive == true
        RETURN user
    `);

      const usersById = await userByIdCursor.all();

      if (usersById.length > 0) {
        logger.info(`User with userId ${userId} and orgId ${orgId} found`);
        return usersById[0];
      }

      // Next, try to find by email and orgId if it's not the default placeholder
      if (email && email !== `user-${userId}@example.com`) {
        const emailCursor = await this.db.query(aql`
      FOR user IN ${this.userCollection}
        FILTER user.email == ${email} AND user.orgId == ${orgId} AND user.isActive == true
        RETURN user
      `);

        const usersByEmail = await emailCursor.all();

        if (usersByEmail.length > 0) {
          logger.info(
            `User with email ${email} for organization ${orgId} already exists`,
          );

          // If found by email but userId doesn't match, update the userId
          if (usersByEmail[0].userId !== userId) {
            await this.userCollection.update(usersByEmail[0]._key, {
              userId: userId,
              updatedAtTimestamp: Date.now(),
            });

            logger.info(`Updated existing user with new userId ${userId}`);

            // Fetch the updated user
            return await this.userCollection.document(usersByEmail[0]._key);
          }

          return usersByEmail[0];
        }
      }

      // No existing user found, create a new one
      const currentTime = Date.now();
      const fullName =
        firstName && lastName
          ? `${firstName} ${lastName}`
          : firstName || email.split('@')[0];

      // Generate a unique key for the document
      const key = uuidv4();

      // Prepare user document
      const user = {
        _key: key,
        userId: userId, // Store the external userId as a regular field
        orgId: orgId, // Ensure orgId is stored in the document
        email,
        firstName: firstName || '',
        middleName: middleName || '',
        lastName: lastName || '',
        fullName,
        designation: designation || '',
        isActive: true,
        createdAtTimestamp: currentTime,
        updatedAtTimestamp: currentTime,
      };

      // Log before saving to help with debugging
      logger.debug('Creating new user', { user });

      try {
        const result = await this.userCollection.save(user);
        logger.info(`User created successfully`, { key, userId, orgId, email });

        // Return the created user
        return {
          ...user,
          _id: result._id,
          _rev: result._rev,
        };
      } catch (saveError) {
        logger.error('Failed to save user to database', {
          error: saveError,
          userId,
          orgId,
          email,
        });
        throw new InternalServerError(
          saveError instanceof Error
            ? saveError.message
            : 'Unexpected error occurred',
        );
      }
    } catch (error) {
      logger.error('Error in findOrCreateUser', {
        userId,
        orgId,
        email,
        error,
      });
      throw error;
    }
  }

  /**
   * Checks if a user exists in the user collection based on userId and orgId
   * @param userId External user ID reference
   * @param email User's email (optional)
   * @param orgId Organization ID
   * @returns Boolean indicating whether the user exists and the user object if found
   */
  async checkUserExists(
    userId: string,
    orgId: string,
    email?: string,
  ): Promise<{ exists: boolean; user?: any }> {
    try {
      // Validate required parameters
      if (!userId) {
        throw new NotFoundError('UserId is required to check if a user exists');
      }

      if (!orgId) {
        throw new NotFoundError(
          'Organization ID is required to check if a user exists',
        );
      }

      // First, try to find the user by both userId and orgId
      const userByIdCursor = await this.db.query(aql`
      FOR user IN ${this.userCollection}
        FILTER user.userId == ${userId} AND user.orgId == ${orgId} AND user.isActive == true
        RETURN user
    `);

      const usersById = await userByIdCursor.all();

      if (usersById.length > 0) {
        logger.info(`User with userId ${userId} and orgId ${orgId} found`);
        return { exists: true, user: usersById[0] };
      }

      // If email is provided, try to find by email and orgId
      if (email && email !== `user-${userId}@example.com`) {
        const emailCursor = await this.db.query(aql`
        FOR user IN ${this.userCollection}
          FILTER user.email == ${email} AND user.orgId == ${orgId} AND user.isActive == true
          RETURN user
      `);

        const usersByEmail = await emailCursor.all();

        if (usersByEmail.length > 0) {
          logger.info(
            `User with email ${email} for organization ${orgId} already exists`,
          );
          return { exists: true, user: usersByEmail[0] };
        }
      }

      // No existing user found
      logger.info(
        `No user found with userId ${userId} or email ${email} for orgId ${orgId}`,
      );
      return { exists: false };
    } catch (error) {
      logger.error('Error in checkUserExists', {
        userId,
        orgId,
        email,
        error,
      });
      throw error;
    }
  }

  /**
   * Gets a record by ID, including its file record and relationships
   * @param recordId The record ID to retrieve
   * @param userId The user ID requesting the record
   * @param orgId The organization ID for the user
   * @returns The record with related data and permission information
   */
  async getRecordById(
    recordId: string,
    userId: string,
    orgId: string,
  ): Promise<any> {
    try {
      logger.debug('Getting record by ID', { recordId, userId, orgId });

      // Find the user by userId and orgId fields
      let user;
      try {
        const userCursor = await this.db.query(aql`
        FOR user IN ${this.userCollection}
          FILTER user.userId == ${userId} AND user.orgId == ${orgId} AND user.isActive == true
          RETURN user
      `);

        const users = await userCursor.all();

        if (users.length === 0) {
          throw new Error(
            `User with ID ${userId} in organization ${orgId} not found`,
          );
        }

        user = users[0];
      } catch (error) {
        logger.error('User not found', { userId, orgId, error });
        throw new UnauthorizedError(
          `User with ID ${userId} in organization ${orgId} not found`,
        );
      }

      // Find the knowledge bases that the user has access to
      const userKbCursor = await this.db.query(aql`
      FOR edge IN ${this.permissionEdges}
        FILTER edge._from == ${user._id}
        LET kb = DOCUMENT(PARSE_IDENTIFIER(edge._to).collection, PARSE_IDENTIFIER(edge._to).key)
        FILTER kb.isDeleted != true AND kb.orgId == ${orgId}
        RETURN { 
          kb, 
          permissions: edge.role,
          role: edge.type
        }
    `);

      const userKbs = await userKbCursor.all();

      if (userKbs.length === 0) {
        logger.warn(
          'User does not have access to any knowledge bases in this organization',
          {
            userId,
            orgId,
          },
        );
        throw new UnauthorizedError(
          `User ${userId} does not have access to any knowledge bases in organization ${orgId}`,
        );
      }

      // Check if the record exists and its relationship to the user's knowledge bases
      const kbRecordCursor = await this.db.query(aql`
      LET record = DOCUMENT(${COLLECTIONS.RECORDS}, ${recordId})
      FILTER record != null AND record.isDeleted != true
      
      LET recordKbs = (
        FOR edge IN ${this.kbToRecordEdges}
          FILTER edge._from == ${COLLECTIONS.RECORDS + '/' + recordId}
          LET knowledgeBaseKey = PARSE_IDENTIFIER(edge._to).key
          RETURN knowledgeBaseKey
      )
      
      LET authorizedKbs = (
        FOR userKb IN ${userKbs}
          FILTER userKb.kb._key IN recordKbs
          RETURN userKb.kb._key
      )
      
      RETURN {
        record: record,
        kbs: (
          FOR knowledgeBaseKey IN authorizedKbs
            LET kbDoc = DOCUMENT(${COLLECTIONS.KNOWLEDGE_BASE}, knowledgeBaseKey)
            FILTER kbDoc.orgId == ${orgId}
            RETURN kbDoc
        ),
        permissions: (
          FOR knowledgeBaseKey IN authorizedKbs
            FOR userKbObj IN ${userKbs}
              FILTER userKbObj.kb._key == knowledgeBaseKey
              RETURN userKbObj.permissions
        )[0]
      }
    `);

      const kbRecords = await kbRecordCursor.all();

      if (kbRecords.length === 0 || !kbRecords[0].record) {
        logger.warn(
          "Record not found or not in any of the user's knowledge bases for this organization",
          { userId, orgId, recordId },
        );
        throw new NotFoundError(
          `Record ${recordId} not found in any knowledge bases accessible to user ${userId} in organization ${orgId}`,
        );
      }

      const record = kbRecords[0].record;
      const permissions = kbRecords[0].permissions;
      const kb = kbRecords[0].kbs.length > 0 ? kbRecords[0].kbs[0] : null;

      if (!kb) {
        logger.warn(
          'Knowledge base not found for record in this organization',
          { recordId, orgId },
        );
        throw new NotFoundError(
          `No knowledge base found for record ${recordId} that user ${userId} has access to in organization ${orgId}`,
        );
      }

      // Get the associated file record
      let fileRecord = null;
      try {
        // First check for is_of_type relationship
        const fileRelationCursor = await this.db.query(aql`
        FOR edge IN ${this.isOfTypeEdges}
          FILTER edge._from == ${COLLECTIONS.RECORDS + '/' + recordId}
          LET fileRec = DOCUMENT(PARSE_IDENTIFIER(edge._to).collection, PARSE_IDENTIFIER(edge._to).key)
          RETURN fileRec
      `);

        const fileRecords = await fileRelationCursor.all();

        if (fileRecords.length > 0) {
          fileRecord = fileRecords[0];
        } else {
          // Fallback to direct lookup using the same ID
          try {
            fileRecord = await this.fileRecordCollection.document(recordId);
          } catch (error) {
            // File record not found with direct lookup, not an error
            logger.debug('No direct file record found for record ID', {
              recordId,
            });
          }
        }
      } catch (error) {
        logger.warn('Error looking up file record for record', {
          recordId,
          error,
        });
        // Continue without file record - it may be a non-file type record
      }

      // Get related records
      const relatedRecordsCursor = await this.db.query(aql`
      FOR edge IN ${this.recordToRecordEdges}
        FILTER edge._from == ${COLLECTIONS.RECORDS + '/' + recordId} OR edge._to == ${COLLECTIONS.RECORDS + '/' + recordId}
        LET otherRecord = edge._from == ${COLLECTIONS.RECORDS + '/' + recordId} ? 
          DOCUMENT(${COLLECTIONS.RECORDS}, PARSE_IDENTIFIER(edge._to).key) : 
          DOCUMENT(${COLLECTIONS.RECORDS}, PARSE_IDENTIFIER(edge._from).key)
        FILTER otherRecord != null AND otherRecord.isDeleted != true
        RETURN {
          record: otherRecord,
          relationship: {
            type: edge.relationshipType,
            direction: edge._from == ${COLLECTIONS.RECORDS + '/' + recordId} ? 'outbound' : 'inbound',
            createdAt: edge.createdAt
          }
        }
    `);

      const relatedRecords = await relatedRecordsCursor.all();

      // Build and return the response
      return {
        record: {
          ...record,
          fileRecord: fileRecord
            ? {
                name: fileRecord.name,
                extension: fileRecord.extension,
                mimeType: fileRecord.mimeType,
                sizeInBytes: fileRecord.sizeInBytes,
                isFile: fileRecord.isFile,
                webUrl: fileRecord.webUrl,
              }
            : null,
        },
        knowledgeBase: {
          id: kb._key,
          name: kb.name,
          orgId: kb.orgId,
        },
        permissions,
        relatedRecords: relatedRecords.map((item) => ({
          id: item.record._key,
          name: item.record.recordName,
          type: item.record.recordType,
          relationship: item.relationship,
        })),
      };
    } catch (error) {
      logger.error('Error in getRecordById', {
        recordId,
        userId,
        orgId,
        error,
      });
      throw error;
    }
  }

  /**
   * Get records with pagination, filtering, and sorting
   * @param options Search and pagination options
   * @returns Paginated records with metadata
   */
  async getRecords(options: {
    orgId: string;
    userId: string;
    page?: number;
    limit?: number;
    search?: string;
    recordTypes?: string[];
    origins?: string[]; // "UPLOAD" for local KB, "CONNECTOR" for connector records
    connectors?: string[]; // Specific connectors: "GMAIL", "DRIVE", "ONEDRIVE", "CONFLUENCE", "SLACK"
    indexingStatus?: string[];
    permissions?: string[]; // Filter by permission types: "READER", "WRITER", etc.
    dateFrom?: number;
    dateTo?: number;
    sortBy?: string;
    sortOrder?: 'asc' | 'desc';
    source?: 'all' | 'local' | 'connector'; // Filter by record source: 'all' (default), 'local' (knowledge base only), 'connector' (direct permissions only)
  }): Promise<{
    records: any[];
    pagination: {
      page: number;
      limit: number;
      totalCount: number;
      totalPages: number;
    };
    filters: {
      applied: Record<string, any>;
      available: Record<string, any>;
    };
  }> {
    try {
      // Extract and normalize options
      const normalizedOptions = this.normalizeRecordOptions(options);
      const {
        orgId,
        userId,
        page,
        limit,
        search,
        recordTypes,
        origins,
        connectors,
        indexingStatus,
        permissions,
        dateFrom,
        dateTo,
        sortBy,
        sortDirection,
        source,
        skip,
      } = normalizedOptions;

      logger.debug('Getting records with options', normalizedOptions);

      // Get user and knowledge base
      const { user, kb } = await this.getUserAndKnowledgeBase(userId, orgId);

      // Build filter queries
      const filterQueries = this.buildFilterQueries({
        orgId,
        search,
        recordTypes,
        origins,
        indexingStatus,
        dateFrom,
        dateTo,
        connectors,
      });

      // Build permission filter
      const permissionFilter = this.buildPermissionFilter(permissions);

      // Get records using the built queries
      const records = await this.executeRecordsQuery({
        user,
        kb,
        filters: filterQueries,
        permissionFilter,
        source,
        skip,
        limit,
        sortBy,
        sortDirection,
      });

      // Get total count for pagination
      const totalCount = await this.getRecordsCount({
        user,
        kb,
        filters: filterQueries,
        permissionFilter,
        source,
      });

      // Get available filter options
      const filterOptions = await this.getFilterOptions({
        user,
        kb,
        orgId,
        source,
      });

      // Process filter options into usable format
      const availableFilters = this.processFilterOptions(filterOptions);

      // Prepare applied filters for response
      const appliedFilters = this.buildAppliedFilters({
        search,
        recordTypes,
        origins,
        connectors,
        indexingStatus,
        permissions,
        source,
        dateFrom,
        dateTo,
      });

      // Calculate total pages
      const totalPages = Math.ceil(totalCount / limit);

      logger.info(`Found ${records.length} records (total: ${totalCount})`, {
        orgId,
        userId,
        page,
        limit,
      });

      // Construct and return the result
      return {
        records,
        pagination: {
          page,
          limit,
          totalCount,
          totalPages,
        },
        filters: {
          applied: appliedFilters,
          available: availableFilters,
        },
      };
    } catch (error) {
      this.handleGetRecordsError(error, options);
    }
  }

  /**
   * Normalizes and defaults record query options
   */
  private normalizeRecordOptions(options: any): {
    orgId: string;
    userId: string;
    page: number;
    limit: number;
    skip: number;
    search?: string;
    recordTypes?: string[];
    origins?: string[];
    connectors?: string[];
    indexingStatus?: string[];
    permissions?: string[];
    dateFrom?: number;
    dateTo?: number;
    sortBy: string;
    sortDirection: string;
    source: 'all' | 'local' | 'connector';
  } {
    const {
      orgId,
      userId,
      page = 1,
      limit = 20,
      search,
      recordTypes,
      origins,
      connectors,
      indexingStatus,
      permissions,
      dateFrom,
      dateTo,
      sortBy = 'createdAtTimestamp',
      sortOrder = 'desc',
      source: explicitSource = 'all',
    } = options;

    // Calculate skip value for pagination
    const skip = (page - 1) * limit;

    // Determine the effective source - if connectors are specified without an explicit source,
    // we should default to connector source only
    let source = explicitSource;
    if (connectors && connectors.length > 0 && explicitSource === 'all') {
      // If user specified connectors but no explicit source, default to connector source
      source = 'connector';
      logger.debug(
        'Automatically setting source to "connector" because connectors were specified',
        {
          connectors,
          explicitSource,
          effectiveSource: source,
        },
      );
    }

    // Validate and normalize sortBy
    const allowedSortFields = [
      'recordName',
      'createdAtTimestamp',
      'updatedAtTimestamp',
      'recordType',
      'origin',
      'indexingStatus',
    ];

    const normalizedSortBy = allowedSortFields.includes(sortBy)
      ? sortBy
      : 'createdAtTimestamp';

    // Convert sort order to direction
    const sortDirection = sortOrder === 'asc' ? 'ASC' : 'DESC';

    return {
      orgId,
      userId,
      page,
      limit,
      skip,
      search,
      recordTypes,
      origins,
      connectors,
      indexingStatus,
      permissions,
      dateFrom,
      dateTo,
      sortBy: normalizedSortBy,
      sortDirection,
      source,
    };
  }

  /**
   * Gets user and knowledge base documents for the request
   */
  private async getUserAndKnowledgeBase(
    userId: string,
    orgId: string,
  ): Promise<{
    user: any;
    kb: any;
  }> {
    // Get the user document
    const userCursor = await this.db.query(aql`
    FOR user IN ${this.userCollection}
      FILTER user.userId == ${userId} AND user.orgId == ${orgId} AND user.isActive == true
      RETURN user
  `);

    const users = await userCursor.all();
    if (users.length === 0) {
      throw new NotFoundError(
        `User with ID ${userId} in organization ${orgId} not found`,
      );
    }
    const user = users[0];

    // Validate user has access to the knowledge base
    const { knowledgeBase: kb } = await this.validateUserKbAccess(
      userId,
      orgId,
      ['OWNER', 'READER', 'FILEORGANIZER', 'WRITER', 'COMMENTER', 'ORGANIZER'],
    );

    return { user, kb };
  }

  /**
   * Builds filter queries for record fetching
   */
  private buildFilterQueries(params: {
    orgId: string;
    search?: string;
    recordTypes?: string[];
    origins?: string[];
    indexingStatus?: string[];
    dateFrom?: number;
    dateTo?: number;
    connectors?: string[];
  }): {
    baseFilter: any;
    localFilter: any;
    connectorFilter: any;
    localOriginFilter: any;
    connectorOriginFilter: any;
  } {
    const {
      orgId,
      search,
      recordTypes,
      origins,
      indexingStatus,
      dateFrom,
      dateTo,
      connectors,
    } = params;

    const tempRecordTypeFilter = ['FILE', 'MAIL'];

    // Base filter for all records
    let baseFilter = aql`FILTER record.isDeleted != true AND record.recordType in ${tempRecordTypeFilter}`;
    baseFilter = aql`${baseFilter} AND record.orgId == ${orgId}`;

    // Add search filter if provided
    if (search) {
      baseFilter = aql`
      ${baseFilter} AND (
        LIKE(LOWER(record.recordName), ${'%' + search.toLowerCase() + '%'}) OR 
        LIKE(LOWER(record.externalRecordId), ${'%' + search.toLowerCase() + '%'})
      )
    `;
    }

    // Add record type filter if provided
    if (recordTypes && recordTypes.length > 0) {
      baseFilter = aql`${baseFilter} AND record.recordType IN ${recordTypes}`;
    }

    // Add origin filter if provided
    if (origins && origins.length > 0) {
      baseFilter = aql`${baseFilter} AND record.origin IN ${origins}`;
    }

    // Create copies for local and connector specific filters
    let localFilter = aql`${baseFilter}`;
    let connectorFilter = aql`${baseFilter}`;

    // Add connector filter - only applies to connector records
    if (connectors && connectors.length > 0) {
      // Apply this filter only to connector records
      connectorFilter = aql`${connectorFilter} AND record.connectorName IN ${connectors}`;
    }

    // Add indexing status filter if provided
    if (indexingStatus && indexingStatus.length > 0) {
      baseFilter = aql`${baseFilter} AND record.indexingStatus IN ${indexingStatus}`;
      localFilter = aql`${localFilter} AND record.indexingStatus IN ${indexingStatus}`;
      connectorFilter = aql`${connectorFilter} AND record.indexingStatus IN ${indexingStatus}`;
    }

    // Add date range filter if provided
    if (dateFrom) {
      baseFilter = aql`${baseFilter} AND record.createdAtTimestamp >= ${dateFrom}`;
      localFilter = aql`${localFilter} AND record.createdAtTimestamp >= ${dateFrom}`;
      connectorFilter = aql`${connectorFilter} AND record.createdAtTimestamp >= ${dateFrom}`;
    }

    if (dateTo) {
      baseFilter = aql`${baseFilter} AND record.createdAtTimestamp <= ${dateTo}`;
      localFilter = aql`${localFilter} AND record.createdAtTimestamp <= ${dateTo}`;
      connectorFilter = aql`${connectorFilter} AND record.createdAtTimestamp <= ${dateTo}`;
    }

    // Fixed origin filters
    const localOriginFilter = aql`AND record.origin == "UPLOAD"`;
    const connectorOriginFilter = aql`AND record.origin == "CONNECTOR"`;

    return {
      baseFilter,
      localFilter,
      connectorFilter,
      localOriginFilter,
      connectorOriginFilter,
    };
  }

  /**
   * Builds permission filter for direct record access
   */
  private buildPermissionFilter(permissions?: string[]): any {
    // Add permissions filter if provided - only applies to direct permissions
    let permissionFilter = aql``;
    if (permissions && permissions.length > 0) {
      permissionFilter = aql`FILTER permissionEdge.role IN ${permissions}`;
    }
    return permissionFilter;
  }

  /**
   * Executes the main query to fetch records
   */
  private async executeRecordsQuery(params: {
    user: any;
    kb: any;
    filters: any;
    permissionFilter: any;
    source: 'all' | 'local' | 'connector';
    skip: number;
    limit: number;
    sortBy: string;
    sortDirection: string;
  }): Promise<any[]> {
    const {
      user,
      kb,
      filters,
      permissionFilter,
      source,
      skip,
      limit,
      sortBy,
      sortDirection,
    } = params;

    const {
      localFilter,
      connectorFilter,
      localOriginFilter,
      connectorOriginFilter,
    } = filters;

    // Main query to get records from both sources with conditional inclusion
    const query = aql`
    // Define both record sets based on source
    LET kbRecords = ${
      source === 'all' || source === 'local'
        ? aql`(
        FOR edge IN ${this.kbToRecordEdges}
          FILTER edge._to == ${COLLECTIONS.KNOWLEDGE_BASE + '/' + kb._key}
          LET record = DOCUMENT(PARSE_IDENTIFIER(edge._from).collection, PARSE_IDENTIFIER(edge._from).key)
          FILTER record != null
          ${localFilter}
          ${localOriginFilter}
          RETURN {
            record: record,
            source: "local",
            permission: null
          }
      )`
        : aql`[]`
    }
    
    LET directPermissionRecords = ${
      source === 'all' || source === 'connector'
        ? aql`(
        FOR permissionEdge IN ${this.userToRecordEdges}
          FILTER permissionEdge._to == ${user._id}
          ${permissionFilter}
          
          LET record = DOCUMENT(PARSE_IDENTIFIER(permissionEdge._from).collection, PARSE_IDENTIFIER(permissionEdge._from).key)
          FILTER record != null
          ${connectorFilter}
          ${connectorOriginFilter}
          
          // Include only records not already in KB records to avoid duplicates
          FILTER record NOT IN kbRecords[*].record
          
          RETURN {
            record: record,
            source: "connector",
            permission: {
              role: permissionEdge.role,
              type: permissionEdge.type,
              createdAtTimestamp: permissionEdge.createdAtTimestamp,
              updatedAtTimestamp: permissionEdge.updatedAtTimestamp
            }
          }
      )`
        : aql`[]`
    }
    
    // Combine both record sets
    LET allRecords = APPEND(kbRecords, directPermissionRecords)
    
    // Apply sorting and pagination on the combined set
    FOR item IN allRecords
      // Dynamic sort based on the selected field and direction
      SORT item.record[${sortBy}] ${sortDirection}
      
      LIMIT ${skip}, ${limit}
      
      // Get associated file record
      LET fileRecord = (
        FOR fileEdge IN ${this.isOfTypeEdges}
          FILTER fileEdge._from == item.record._id
          RETURN DOCUMENT(PARSE_IDENTIFIER(fileEdge._to).collection, PARSE_IDENTIFIER(fileEdge._to).key)
      )[0]
      
      // Return formatted result with all necessary information
      RETURN {
        id: item.record._key,
        externalRecordId: item.record.externalRecordId,
        externalRevisionId: item.record.externalRevisionId,
        recordName: item.record.recordName,
        recordType: item.record.recordType,
        origin: item.record.origin,
        connectorName: item.record.connectorName,
        indexingStatus: item.record.indexingStatus,
        createdAtTimestamp: item.record.createdAtTimestamp,
        updatedAtTimestamp: item.record.updatedAtTimestamp,
        sourceCreatedAtTimestamp: item.record.sourceCreatedAtTimestamp,
        sourceLastModifiedTimestamp: item.record.sourceLastModifiedTimestamp,
        orgId: item.record.orgId,
        version: item.record.version,
        isDeleted: item.record.isDeleted,
        deletedByUserId: item.record.deletedByUserId,
        isLatestVersion: item.record.isLatestVersion,
        source: item.source,
        permission: item.permission,
        fileRecord: fileRecord ? {
          name: fileRecord.name,
          extension: fileRecord.extension,
          mimeType: fileRecord.mimeType,
          sizeInBytes: fileRecord.sizeInBytes,
          isFile: fileRecord.isFile,
          webUrl: fileRecord.webUrl
        } : null
      }
  `;

    // Execute the query to get the records
    const cursor = await this.db.query(query);
    return await cursor.all();
  }

  /**
   * Gets the total count of records matching the filters
   */
  private async getRecordsCount(params: {
    user: any;
    kb: any;
    filters: any;
    permissionFilter: any;
    source: 'all' | 'local' | 'connector';
  }): Promise<number> {
    const { user, kb, filters, permissionFilter, source } = params;
    const {
      localFilter,
      connectorFilter,
      localOriginFilter,
      connectorOriginFilter,
    } = filters;

    // Optimized count query
    const countQuery = aql`
    // Define both record sets for counting using a more efficient approach
    LET kbRecordsCount = ${
      source === 'all' || source === 'local'
        ? aql`LENGTH(
          FOR edge IN ${this.kbToRecordEdges}
            FILTER edge._to == ${COLLECTIONS.KNOWLEDGE_BASE + '/' + kb._key}
            LET record = DOCUMENT(PARSE_IDENTIFIER(edge._from).collection, PARSE_IDENTIFIER(edge._from).key)
            FILTER record != null
            ${localFilter}
            ${localOriginFilter}
            RETURN 1
        )`
        : aql`0`
    }
    
    LET kbRecords = ${
      source === 'all' || source === 'local'
        ? aql`(
          FOR edge IN ${this.kbToRecordEdges}
            FILTER edge._to == ${COLLECTIONS.KNOWLEDGE_BASE + '/' + kb._key}
            LET record = DOCUMENT(PARSE_IDENTIFIER(edge._from).collection, PARSE_IDENTIFIER(edge._from).key)
            FILTER record != null
            ${localFilter}
            ${localOriginFilter}
            RETURN record
        )`
        : aql`[]`
    }
    
    LET directPermissionCount = ${
      source === 'all' || source === 'connector'
        ? aql`LENGTH(
          FOR permissionEdge IN ${this.userToRecordEdges}
            FILTER permissionEdge._to == ${user._id}
            ${permissionFilter}
            
            LET record = DOCUMENT(PARSE_IDENTIFIER(permissionEdge._from).collection, PARSE_IDENTIFIER(permissionEdge._from).key)
            FILTER record != null
            ${connectorFilter}
            ${connectorOriginFilter}
            
            // Don't count records already in KB
            FILTER record NOT IN kbRecords
            
            RETURN 1
        )`
        : aql`0`
    }
    
    // Return total count
    RETURN kbRecordsCount + directPermissionCount
  `;

    const countCursor = await this.db.query(countQuery);
    const countResult = await countCursor.all();
    return countResult[0] || 0;
  }

  /**
   * Gets available filter options based on existing records
   */
  private async getFilterOptions(params: {
    user: any;
    kb: any;
    orgId: string;
    source: 'all' | 'local' | 'connector';
  }): Promise<any[]> {
    const { user, kb, orgId, source } = params;

    // Query to get available filter options
    const filterOptionsQuery = aql`
    // Get filter options from knowledge base records
    LET kbRecordsOptions = ${
      source === 'all' || source === 'local'
        ? aql`(
        FOR edge IN ${this.kbToRecordEdges}
          FILTER edge._to == ${COLLECTIONS.KNOWLEDGE_BASE + '/' + kb._key}
          LET record = DOCUMENT(PARSE_IDENTIFIER(edge._from).collection, PARSE_IDENTIFIER(edge._from).key)
          FILTER record != null AND record.isDeleted != true AND record.orgId == ${orgId}
          FILTER record.origin == "UPLOAD"
          RETURN {
            recordType: record.recordType,
            origin: record.origin,
            connectorName: record.connectorName,
            indexingStatus: record.indexingStatus,
            source: "local",
            permission: null
          }
      )`
        : aql`[]`
    }
    
    // Get filter options from direct permission records
    LET directOptionsOptions = ${
      source === 'all' || source === 'connector'
        ? aql`(
        FOR permissionEdge IN ${this.userToRecordEdges}
          FILTER permissionEdge._to == ${user._id}
          
          LET record = DOCUMENT(PARSE_IDENTIFIER(permissionEdge._from).collection, PARSE_IDENTIFIER(permissionEdge._from).key)
          FILTER record != null AND record.isDeleted != true AND record.orgId == ${orgId}
          FILTER record.origin == "CONNECTOR"
          
          RETURN {
            recordType: record.recordType,
            origin: record.origin,
            connectorName: record.connectorName,
            indexingStatus: record.indexingStatus,
            source: "connector",
            permission: permissionEdge.role
          }
      )`
        : aql`[]`
    }
    
    // Combine and return all options
    RETURN APPEND(kbRecordsOptions, directOptionsOptions)
  `;

    // Execute the query for filter options
    const filterOptionsCursor = await this.db.query(filterOptionsQuery);
    const allFilterOptions = await filterOptionsCursor.all();

    // Flatten the result array if needed
    return Array.isArray(allFilterOptions[0])
      ? allFilterOptions[0].concat(allFilterOptions[1] || [])
      : allFilterOptions;
  }

  /**
   * Processes raw filter options into formatted filter groups
   */
  private processFilterOptions(filterOptions: any[]): {
    recordTypes: Array<{ value: string; count: number | undefined }>;
    origins: Array<{ value: string; count: number | undefined }>;
    connectors: Array<{ value: string; count: number | undefined }>;
    indexingStatus: Array<{ value: string; count: number | undefined }>;
    permissions: Array<{ value: string; count: number | undefined }>;
    sources: Array<{ value: string; count: number | undefined }>;
  } {
    // Extract available filter options
    const recordTypesSet = new Set<string>();
    const originsSet = new Set<string>();
    const connectorsSet = new Set<string>();
    const statusSet = new Set<string>();
    const permissionSet = new Set<string>();
    const sourceSet = new Set<string>();

    const recordTypeCount: Record<string, number> = {};
    const originCount: Record<string, number> = {};
    const connectorCount: Record<string, number> = {};
    const statusCount: Record<string, number> = {};
    const permissionCount: Record<string, number> = {};
    const sourceCount: Record<string, number> = {};

    filterOptions.forEach((item: any) => {
      if (item.recordType) {
        recordTypesSet.add(item.recordType);
        recordTypeCount[item.recordType] =
          (recordTypeCount[item.recordType] || 0) + 1;
      }

      if (item.origin) {
        originsSet.add(item.origin);
        originCount[item.origin] = (originCount[item.origin] || 0) + 1;
      }

      if (item.connectorName) {
        connectorsSet.add(item.connectorName);
        connectorCount[item.connectorName] =
          (connectorCount[item.connectorName] || 0) + 1;
      }

      if (item.indexingStatus) {
        statusSet.add(item.indexingStatus);
        statusCount[item.indexingStatus] =
          (statusCount[item.indexingStatus] || 0) + 1;
      }

      if (item.permission) {
        permissionSet.add(item.permission);
        permissionCount[item.permission] =
          (permissionCount[item.permission] || 0) + 1;
      }

      if (item.source) {
        sourceSet.add(item.source);
        sourceCount[item.source] = (sourceCount[item.source] || 0) + 1;
      }
    });

    return {
      recordTypes: Array.from(recordTypesSet).map((type) => ({
        value: type,
        count: recordTypeCount[type],
      })),
      origins: Array.from(originsSet).map((origin) => ({
        value: origin,
        count: originCount[origin],
      })),
      connectors: Array.from(connectorsSet).map((connector) => ({
        value: connector,
        count: connectorCount[connector],
      })),
      indexingStatus: Array.from(statusSet).map((status) => ({
        value: status,
        count: statusCount[status],
      })),
      permissions: Array.from(permissionSet).map((permission) => ({
        value: permission,
        count: permissionCount[permission],
      })),
      sources: Array.from(sourceSet).map((source) => ({
        value: source,
        count: sourceCount[source],
      })),
    };
  }

  /**
   * Builds the applied filters object for the response
   */
  private buildAppliedFilters(params: {
    search?: string;
    recordTypes?: string[];
    origins?: string[];
    connectors?: string[];
    indexingStatus?: string[];
    permissions?: string[];
    source: 'all' | 'local' | 'connector';
    dateFrom?: number;
    dateTo?: number;
  }): Record<string, any> {
    const {
      search,
      recordTypes,
      origins,
      connectors,
      indexingStatus,
      permissions,
      source,
      dateFrom,
      dateTo,
    } = params;

    const appliedFilters: Record<string, any> = {};

    if (search) appliedFilters.search = search;
    if (recordTypes) appliedFilters.recordTypes = recordTypes;
    if (origins) appliedFilters.origins = origins;
    if (connectors) appliedFilters.connectors = connectors;
    if (indexingStatus) appliedFilters.indexingStatus = indexingStatus;
    if (permissions) appliedFilters.permissions = permissions;
    if (source !== 'all') appliedFilters.source = source;

    if (dateFrom || dateTo) {
      appliedFilters.dateRange = {
        from: dateFrom,
        to: dateTo,
      };
    }

    return appliedFilters;
  }

  /**
   * Handles errors in the getRecords method
   */
  private handleGetRecordsError(error: any, options: any): never {
    if (error) {
      // Handle database-specific errors
      logger.error('ArangoDB error in getRecords', {
        code: error.code,
        errorNum: error.errorNum,
        options,
        error,
      });

      if (error.errorNum === 1203) {
        // Collection not found
        throw new InternalServerError('Database configuration error');
      }
    } else if (
      error instanceof NotFoundError ||
      error instanceof UnauthorizedError
    ) {
      // Pass through specific error types
      logger.error(`${error.constructor.name} in getRecords`, {
        options,
        error,
      });
      throw error;
    } else {
      // General error handling
      logger.error('Error in getRecords', { options, error });
    }
    throw error;
  }

  /**
   * Ensures that all necessary indexes exist for optimal query performance
   */
  async ensureRecordIndexes(): Promise<void> {
    try {
      // Create index on records collection
      await this.recordCollection.ensureIndex({
        type: 'persistent',
        fields: ['orgId', 'isDeleted'],
        name: 'idx_records_org_deleted',
      });

      await this.recordCollection.ensureIndex({
        type: 'persistent',
        fields: ['recordName'],
        name: 'idx_records_name',
      });

      await this.recordCollection.ensureIndex({
        type: 'persistent',
        fields: ['recordType'],
        name: 'idx_records_type',
      });

      await this.recordCollection.ensureIndex({
        type: 'persistent',
        fields: ['origin', 'connectorName'],
        name: 'idx_records_origin_connector',
      });

      await this.recordCollection.ensureIndex({
        type: 'persistent',
        fields: ['indexingStatus'],
        name: 'idx_records_indexing_status',
      });

      await this.recordCollection.ensureIndex({
        type: 'persistent',
        fields: ['createdAtTimestamp'],
        name: 'idx_records_created_at',
      });

      // Create indexes on edge collections
      await this.kbToRecordEdges.ensureIndex({
        type: 'persistent',
        fields: ['_to'],
        name: 'idx_kb_edges_to',
      });

      await this.userToRecordEdges.ensureIndex({
        type: 'persistent',
        fields: ['_to', 'role'],
        name: 'idx_user_record_edges_to_role',
      });

      await this.isOfTypeEdges.ensureIndex({
        type: 'persistent',
        fields: ['_from'],
        name: 'idx_is_of_type_from',
      });

      logger.info('Record indexes created successfully');
    } catch (error) {
      logger.error('Failed to create record indexes', { error });
      throw new InternalServerError('Failed to initialize database indexes');
    }
  }

  /**
   * Validate that a user has access to a specific knowledge base
   * @param userId The user ID
   * @param orgId The organization ID
   * @param requiredPermissions Array of required permission types (e.g. ['READ', 'WRITE'])
   * @returns Object with knowledge base and permissions information
   */
  async validateUserKbAccess(
    userId: string,
    orgId: string,
    requiredPermissions: string[] = ['READER'],
  ): Promise<{
    knowledgeBase: any;
    permissions: string[];
    role: string;
  }> {
    try {
      if (!userId || !orgId) {
        throw new NotFoundError('User ID and organization ID are required');
      }

      logger.debug('Validating user knowledge base access', { userId, orgId });

      // Get the knowledge base for this organization
      const kb = await this.getOrCreateKnowledgeBase(userId, orgId);

      // Find the user by userId and orgId
      let user;
      try {
        const userCursor = await this.db.query(aql`
        FOR user IN ${this.userCollection}
          FILTER user.userId == ${userId} AND user.orgId == ${orgId} AND user.isDeleted != true
          RETURN user
      `);

        const users = await userCursor.all();

        if (users.length === 0) {
          throw new NotFoundError(
            `User with ID ${userId} in organization ${orgId} not found`,
          );
        }

        user = users[0];
      } catch (error) {
        logger.error('User not found', { userId, orgId, error });
        throw new Error(
          `User with ID ${userId} in organization ${orgId} not found`,
        );
      }

      // Check if the user has permission to access this knowledge base
      const permissionCursor = await this.db.query(aql`
      FOR edge IN ${this.permissionEdges}
        FILTER edge._from == ${user._id}
          AND edge._to == ${COLLECTIONS.KNOWLEDGE_BASE + '/' + kb._key}
        RETURN {
          permissions: edge.role,
          role: edge.type
        }
    `);

      const permissions = await permissionCursor.all();

      if (permissions.length === 0) {
        logger.warn('User does not have access to knowledge base', {
          userId,
          orgId,
          kbId: kb._key,
        });
        throw new UnauthorizedError(
          `User ${userId} does not have permission to access knowledge base for organization ${orgId}`,
        );
      }

      // Check if user has all required permissions
      const userPermissions = permissions[0].permissions || [];
      const hasAllRequiredPermissions = requiredPermissions.every(
        (permission) =>
          userPermissions.includes(permission) ||
          userPermissions.includes('OWNER'),
      );

      if (!hasAllRequiredPermissions) {
        logger.warn('User lacks required permissions', {
          userId,
          orgId,
          kbId: kb._key,
          userPermissions,
          requiredPermissions,
        });
        throw new UnauthorizedError(
          `User ${userId} does not have the required permissions (${requiredPermissions.join(', ')}) for knowledge base ${kb._key}`,
        );
      }

      logger.info('User has required knowledge base access', {
        userId,
        orgId,
        kbId: kb._key,
        permissions: userPermissions,
        role: permissions[0].role,
      });

      return {
        knowledgeBase: kb,
        permissions: userPermissions,
        role: permissions[0].role,
      };
    } catch (error) {
      logger.error('Error validating user knowledge base access', {
        userId,
        orgId,
        error,
      });
      throw error;
    }
  }

  /**
   * Updates a record with new data
   * @param recordId Record ID to update
   * @param updateData Data to update on the record
   * @returns Updated record
   */
  async updateRecord(
    recordId: string,
    updateData: any,
    keyValueStoreService: KeyValueStoreService,
  ): Promise<any> {
    try {
      logger.debug('Updating record', { recordId });

      // First check if record exists and get current data
      let existingRecord;
      try {
        existingRecord = await this.recordCollection.document(recordId);
      } catch (error) {
        logger.error('Record not found for update', { recordId, error });
        throw new NotFoundError(`Record with ID ${recordId} not found`);
      }

      let existingFileRecord = null;
      // Only fetch file record for FILE type records
      if (existingRecord.recordType === 'FILE') {
        try {
          existingFileRecord =
            await this.fileRecordCollection.document(recordId);
        } catch (error) {
          logger.warn('File record not found for FILE type record', {
            recordId,
            error: error,
          });
          // Continue without file record - we'll handle this case appropriately
        }
      }

      // Extract file metadata before filtering
      const fileMetadata = updateData.fileMetadata;
      const hasFileUpload = fileMetadata && fileMetadata.originalname;

      // Validate file extension if uploading a new file for existing file record
      if (
        hasFileUpload &&
        existingRecord.recordType === 'FILE' &&
        existingFileRecord
      ) {
        const newExtension = fileMetadata.extension;
        const existingExtension = existingFileRecord.extension?.toLowerCase();

        if (newExtension !== existingExtension) {
          logger.error('File extension mismatch detected in service', {
            recordId,
            existingExtension,
            newExtension,
            originalname: fileMetadata.originalname,
          });

          throw new BadRequestError(
            `File type mismatch: existing file has extension '${existingExtension}', ` +
              `but uploaded file has extension '${newExtension}'. Please upload a file with the same extension.`,
          );
        }

        logger.info('File extension validation passed', {
          recordId,
          extension: newExtension,
          originalname: fileMetadata.originalname,
        });
      }

      // Filter out immutable properties and non-record fields for safety
      const safeUpdateData = { ...updateData };
      const immutableProps = [
        '_id',
        '_key',
        '_rev',
        'orgId',
        'createdAtTimestamp',
        'externalRecordId',
        'recordType',
        'origin',
      ];

      // Remove immutable properties
      immutableProps.forEach((prop) => {
        delete safeUpdateData[prop];
      });

      // Remove non-record fields - clean up before validation
      delete safeUpdateData.fileMetadata;

      // Remove any fields that don't exist in the schema
      const nonSchemaFields = [
        'sizeInBytes',
        'fileBuffer',
        'deletedAtTimestamp',
        'userId',
        'mimeType',
        'extension',
        'originalname',
      ];

      nonSchemaFields.forEach((field) => {
        if (safeUpdateData[field] !== undefined) {
          logger.debug(`Removing non-schema field: ${field}`, {
            recordId,
            field,
          });
          delete safeUpdateData[field];
        }
      });

      // Validate allowed updates based on ArangoDB schema
      const allowedUpdates = [
        // Basic record fields
        'recordName',
        'externalRevisionId',
        'version',
        'connectorName',
        'summaryDocumentId',
        'virtualRecordId',

        // Timestamp fields
        'updatedAtTimestamp',
        'lastSyncTimestamp',
        'sourceCreatedAtTimestamp',
        'sourceLastModifiedTimestamp',
        'lastIndexTimestamp',
        'lastExtractionTimestamp',

        // Status fields
        'indexingStatus',
        'extractionStatus',
        'isLatestVersion',
        'isDirty',
        'reason',

        // Deletion related fields
        'isDeleted',
        'isArchived',
        'deletedByUserId',

        // Source sync fields (for connector records)
        'isDeletedAtSource',
        'deletedAtSourceTimestamp',
      ];

      logger.debug('Validating update fields against allowed schema fields', {
        recordId,
        providedFields: Object.keys(safeUpdateData),
        allowedFields: allowedUpdates,
      });

      // Remove any update properties not in allowed list
      Object.keys(safeUpdateData).forEach((key) => {
        if (!allowedUpdates.includes(key)) {
          logger.warn(`Removing disallowed update field: ${key}`, {
            recordId,
            field: key,
            value: safeUpdateData[key],
            allowedFields: allowedUpdates,
          });
          delete safeUpdateData[key];
        }
      });

      logger.debug('Final update data after filtering', {
        recordId,
        updateFields: Object.keys(safeUpdateData),
        updateData: safeUpdateData,
      });

      safeUpdateData.updatedAtTimestamp = Date.now();

      const criticalNullChecks = [
        'recordName',
        'externalRecordId',
        'recordType',
        'origin',
      ];
      const existingCriticalFields = criticalNullChecks.filter(
        (field) =>
          existingRecord[field] === null || existingRecord[field] === undefined,
      );

      if (existingCriticalFields.length > 0) {
        logger.error(
          'Critical required fields are null/undefined in existing record',
          {
            recordId,
            missingFields: existingCriticalFields,
            existingRecord: {
              recordName: existingRecord.recordName,
              externalRecordId: existingRecord.externalRecordId,
              recordType: existingRecord.recordType,
              origin: existingRecord.origin,
            },
          },
        );
      }

      if (existingRecord.recordType === 'FILE') {
        if (safeUpdateData.version !== undefined) {
          logger.debug('Using provided version number', {
            recordId,
            version: safeUpdateData.version,
          });
        } else if (hasFileUpload) {
          const currentVersion =
            typeof existingRecord.version === 'number'
              ? existingRecord.version
              : 0;
          safeUpdateData.version = currentVersion + 1;
          logger.debug('Auto-incrementing file version due to file upload', {
            recordId,
            previousVersion: currentVersion,
            newVersion: safeUpdateData.version,
          });
        }

        if (safeUpdateData.isDeleted === true) {
          safeUpdateData.isLatestVersion = false;
        }
      }

      if (
        safeUpdateData.indexingStatus &&
        safeUpdateData.indexingStatus !== existingRecord.indexingStatus
      ) {
        logger.info('Updating indexing status', {
          recordId,
          from: existingRecord.indexingStatus,
          to: safeUpdateData.indexingStatus,
        });

        if (safeUpdateData.indexingStatus === 'COMPLETED') {
          safeUpdateData.lastIndexTimestamp = Date.now();
        }
      }

      let recordResult;
      try {
        recordResult = await this.recordCollection.update(
          recordId,
          safeUpdateData,
          {
            returnNew: true,
          },
        );
      } catch (updateError: any) {
        logger.error('ArangoDB update failed', {
          recordId,
          error: updateError.message,
          errorNum: updateError.errorNum,
          code: updateError.code,
          updateData: safeUpdateData,
          existingRecord: {
            recordType: existingRecord.recordType,
            version: existingRecord.version,
            orgId: existingRecord.orgId,
          },
        });

        if (updateError.errorNum === 1620) {
          throw new Error(
            `Schema validation failed: ${updateError.message}. Check that all fields match the record schema.`,
          );
        }

        throw updateError;
      }

      logger.info('Record updated successfully', {
        recordId,
        updatedFields: Object.keys(safeUpdateData),
        version: recordResult.new.version,
        hasFileUpload,
      });

      if (existingRecord.recordType === 'FILE' && existingFileRecord) {
        try {
          const fileRecordUpdateData: any = {};

          if (hasFileUpload && fileMetadata) {
            const { originalname, size } = fileMetadata;

            if (originalname) {
              fileRecordUpdateData.name = originalname;
              logger.debug('Updating file record name from uploaded file', {
                recordId,
                originalname,
              });
            }

            if (size !== undefined) {
              fileRecordUpdateData.sizeInBytes = size;
              logger.debug('Updating file record size from uploaded file', {
                recordId,
                size,
              });
            }
          } else {
            const fieldMappings: Record<string, string> = {
              recordName: 'name', // Record name -> File name
              sizeInBytes: 'sizeInBytes',
            };

            Object.entries(fieldMappings).forEach(
              ([recordField, fileField]) => {
                if (safeUpdateData[recordField] !== undefined) {
                  fileRecordUpdateData[fileField] = safeUpdateData[recordField];
                  logger.debug('Mapping record field to file record field', {
                    recordId,
                    recordField,
                    fileField,
                    value: safeUpdateData[recordField],
                  });
                }
              },
            );
          }

          if (Object.keys(fileRecordUpdateData).length > 0) {
            await this.fileRecordCollection.update(
              recordId,
              fileRecordUpdateData,
              {
                returnNew: true,
              },
            );

            logger.info('File record updated successfully', {
              recordId,
              updatedFields: Object.keys(fileRecordUpdateData),
              hasFileUpload,
            });
          } else {
            logger.debug('No file record updates needed', { recordId });
          }
        } catch (fileRecordError) {
          logger.error('Failed to update associated file record', {
            recordId,
            error: fileRecordError,
          });
        }
      } else if (existingRecord.recordType === 'FILE' && !existingFileRecord) {
        logger.warn('File record not found for FILE type record', {
          recordId,
          recordType: existingRecord.recordType,
        });
      }

      // Publish update event
      try {
        const updatedRecord = recordResult.new as IRecordDocument;
        const updateEventPayload = await this.createUpdateRecordEventPayload(
          updatedRecord,
          existingFileRecord,
          keyValueStoreService,
        );

        const event: Event = {
          eventType: EventType.UpdateRecordEvent,
          timestamp: Date.now(),
          payload: updateEventPayload,
        };

        await this.eventProducer.publishEvent(event);
        logger.info(`Published update event for record ${recordId}`);
      } catch (eventError) {
        logger.error('Failed to publish update record event', {
          recordId,
          error: eventError,
        });
      }

      return recordResult.new;
    } catch (error) {
      logger.error('Error updating record', { recordId, error });
      throw error;
    }
  }

  async reindexRecord(
    recordId: string,
    record: any,
    keyValueStoreService: KeyValueStoreService,
  ): Promise<any> {
    try {
      let existingFileRecord;
      try {
        existingFileRecord = await this.fileRecordCollection.document(recordId);
      } catch (error) {
        logger.error('File record not found', { recordId, error });
        throw new NotFoundError(`File record with ID ${recordId} not found`);
      }

      try {
        const reindexEventPayload = await this.createReindexRecordEventPayload(
          record,
          existingFileRecord,
          keyValueStoreService,
        );

        const event: Event = {
          eventType: EventType.NewRecordEvent,
          timestamp: Date.now(),
          payload: reindexEventPayload,
        };

        await this.eventProducer.publishEvent(event);
        logger.info(`Published reindex event for record ${recordId}`);

        return { success: true, recordId };
      } catch (eventError: any) {
        logger.error('Failed to publish reindex record event', {
          recordId,
          error: eventError,
        });
        // Don't throw the error to avoid affecting the main operation
        return { success: false, error: eventError.message };
      }
    } catch (error) {
      logger.error('Error reindexing record', { recordId, error });
      throw error;
    }
  }

  // New method for creating reindex event payload
  async createReindexRecordEventPayload(
    record: any,
    fileRecord: IFileRecordDocument,
    keyValueStoreService: KeyValueStoreService,
  ): Promise<NewRecordEvent> {
    // Generate signed URL route based on record information
    const url = (await keyValueStoreService.get<string>('endpoint')) || '{}';

    const storageUrl =
      JSON.parse(url).storage?.endpoint || this.defaultConfig.endpoint;
    const signedUrlRoute = `${storageUrl}/api/v1/document/internal/${record.externalRecordId}/download`;
    let mimeType = '';
    if (fileRecord && fileRecord.mimeType) {
      mimeType = fileRecord.mimeType;
    }
    return {
      orgId: record.orgId,
      recordId: record._key,
      version: record.version || 1,
      signedUrlRoute: signedUrlRoute,
      recordName: record.recordName,
      recordType: record.recordType,
      origin: record.origin,
      extension: record.fileRecord.extension,
      mimeType: mimeType,
      createdAtTimestamp: Date.now().toString(),
      updatedAtTimestamp: Date.now().toString(),
      sourceCreatedAtTimestamp: Date.now().toString(),
    };
  }

  async reindexAllRecords(reindexPayload: any): Promise<any> {
    try {
      const reindexAllRecordEventPayload =
        await this.createReindexAllRecordEventPayload(reindexPayload);

      const event: SyncEvent = {
        eventType: 'reindexAllRecord',
        timestamp: Date.now(),
        payload: reindexAllRecordEventPayload,
      };

      await this.syncEventProducer.publishEvent(event);
      logger.info(`Published reindex all record for app ${reindexPayload.app}`);

      return { success: true };
    } catch (eventError: any) {
      logger.error('Failed to publish reindex record event', {
        error: eventError,
      });
      // Don't throw the error to avoid affecting the main operation
      return { success: false, error: eventError.message };
    }
  }

  async createReindexAllRecordEventPayload(
    reindexPayload: any,
  ): Promise<ReindexAllRecordEvent> {
    return {
      orgId: reindexPayload.orgId,
      origin: reindexPayload.origin,
      connector: reindexPayload.app,
      createdAtTimestamp: Date.now().toString(),
      updatedAtTimestamp: Date.now().toString(),
      sourceCreatedAtTimestamp: Date.now().toString(),
    };
  }

  async resyncConnectorRecords(resyncConnectorPayload: any): Promise<any> {
    try {
      const resyncPayload =
        await this.createResyncConnectorEventPayload(resyncConnectorPayload);
      const eventType = resyncPayload.connector.replace(' ', '').toLowerCase() + '.resync';
      const event: SyncEvent = {
        eventType: eventType,
        timestamp: Date.now(),
        payload: resyncPayload,
      };

      await this.syncEventProducer.publishEvent(event);
      logger.info(
        `Published resync connector event for app ${resyncConnectorPayload.connectorName}`,
      );

      return { success: true };
    } catch (eventError: any) {
      logger.error('Failed to publish resync connector event', {
        error: eventError,
      });
      // Don't throw the error to avoid affecting the main operation
      return { success: false, error: eventError.message };
    }
  }

  async createResyncConnectorEventPayload(
    resyncConnectorEventPayload: any,
  ): Promise<BaseSyncEvent> {
    const connectorName = resyncConnectorEventPayload.connectorName;

    return {
      orgId: resyncConnectorEventPayload.orgId,
      origin: resyncConnectorEventPayload.origin,
      connector: connectorName,
      createdAtTimestamp: Date.now().toString(),
      updatedAtTimestamp: Date.now().toString(),
      sourceCreatedAtTimestamp: Date.now().toString(),
    };
  }

  /**
   * Soft-deletes a record by setting isDeleted flag
   * @param recordId Record ID to delete
   * @param userId User performing the deletion
   * @returns Deletion status
   */
  async softDeleteRecord(recordId: string, userId: string): Promise<boolean> {
    try {
      logger.debug('Soft deleting record', { recordId, userId });

      // First check if record exists
      let record;
      try {
        record = await this.recordCollection.document(recordId);
      } catch (error) {
        logger.error('Record not found for deletion', { recordId, error });
        throw new NotFoundError(`Record with ID ${recordId} not found`);
      }

      // Check if already deleted
      if (record.isDeleted) {
        logger.info('Record is already deleted', { recordId });
        throw new NotFoundError(`Record with ID ${recordId} not found`);
      }

      // Update the record to set deleted flag
      const updateData = {
        isDeleted: true,
        deletedByUserId: userId,
        // deletedAtTimestamp: Date.now(),
        updatedAtTimestamp: Date.now(),
      };

      await this.recordCollection.update(recordId, updateData);

      // Also mark the file record as deleted if it exists
      try {
        const fileRecord = await this.fileRecordCollection.document(recordId);
        if (fileRecord) {
          await this.fileRecordCollection.update(recordId, updateData);
          logger.info('Associated file record marked as deleted', { recordId });
        }
      } catch (error) {
        // Ignore errors if file record doesn't exist
        logger.debug('No associated file record found for deletion', {
          recordId,
        });
      }

      try {
        // Fetch the updated record to include all necessary data
        const updatedRecord = await this.recordCollection.document(recordId);
        const fileRecord = await this.fileRecordCollection.document(recordId);
        const deleteEventPayload = this.createDeletedRecordEventPayload(
          updatedRecord,
          fileRecord,
        );

        const event: Event = {
          eventType: EventType.DeletedRecordEvent,
          timestamp: Date.now(),
          payload: deleteEventPayload,
        };

        await this.eventProducer.publishEvent(event);
        logger.info(`Published delete event for record ${recordId}`);
      } catch (eventError) {
        logger.error('Failed to publish delete record event', {
          recordId,
          error: eventError,
        });
        // Don't throw the error to avoid affecting the main operation
      }

      logger.info('Record soft deleted successfully', { recordId, userId });
      return true;
    } catch (error) {
      logger.error('Error soft deleting record', { recordId, error });
      throw error;
    }
  }

  /**
   * Publishes a hard delete event for a record
   * @param recordResponse - The service response containing record and file record data
   * @throws {Error} When event publishing fails or record processing encounters an error
   */
  async publishHardDeleteRecord(
    recordResponse: IServiceRecordsResponse,
  ): Promise<void> {
    const recordId = recordResponse.record?._id;

    try {
      const record = recordResponse.record;

      if (!record || !recordId) {
        throw new Error('Invalid record data: missing record or record ID');
      }

      logger.debug('Publishing hard delete event for record', { recordId });

      // Get file record data - prioritize from response, fallback to database lookup
      let fileRecord = null;

      if (recordResponse.record.fileRecord) {
        // Use file record from the response if available
        fileRecord = recordResponse.record.fileRecord;
        logger.debug('Using file record from response', { recordId });
      } else if (record.recordType === 'FILE') {
        // For FILE type records, attempt to fetch file record from database
        try {
          const existingFileRecord =
            await this.fileRecordCollection.document(recordId);
          fileRecord = existingFileRecord;
          logger.debug('Retrieved file record from database', { recordId });
        } catch (fileRecordError) {
          logger.warn('File record not found for FILE type record', {
            recordId,
            error: fileRecordError,
          });
          // Continue without file record - it may have been already deleted
        }
      }

      // Create and publish the delete event
      try {
        const deleteEventPayload = this.createDeletedRecordEventPayload(
          record,
          fileRecord,
        );

        const event: Event = {
          eventType: EventType.DeletedRecordEvent,
          timestamp: Date.now(),
          payload: deleteEventPayload,
        };

        await this.eventProducer.publishEvent(event);

        logger.info('Successfully published hard delete event', {
          recordId,
          recordType: record.recordType,
          hasFileRecord: !!fileRecord,
        });
      } catch (eventError) {
        logger.error('Failed to publish hard delete event', {
          recordId,
          error: eventError,
        });

        // Re-throw the error as this is a critical operation
        throw new Error(
          `Failed to publish delete event for record ${recordId}: ${eventError}`,
        );
      }

      logger.info('Hard delete event processing completed successfully', {
        recordId,
        recordType: record.recordType,
      });
    } catch (error) {
      logger.error('Error processing hard delete record event', {
        recordId,
        error: error,
      });

      throw error;
    }
  }
}
