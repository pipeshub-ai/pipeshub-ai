import { inject, injectable } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { IRecordDocument } from '../types/record';
import { IFileRecordDocument } from '../types/file_record';
import {
  InternalServerError,
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
import {
  ConnectorSyncEvent,
  SyncEventProducer,
  Event as SyncEvent,
  BaseSyncEvent,
} from './sync_events.service';
import {
  IServiceFileRecord,
  IServiceRecord
} from '../types/service.records.response';


const logger = Logger.getInstance({
  service: 'Knowledge Base Service',
});

@injectable()
export class RecordRelationService {

  constructor(
    @inject(RecordsEventProducer) readonly eventProducer: RecordsEventProducer,
    @inject(SyncEventProducer) readonly syncEventProducer: SyncEventProducer,
    private readonly defaultConfig: DefaultStorageConfig,
  ) {

    this.initializeEventProducer();
    this.initializeSyncEventProducer();
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

  async reindexFailedRecords(reindexPayload: any): Promise<any> {
    try {
      const connectorNormalized = reindexPayload.app
        .replace(/\s+/g, '')
        .toLowerCase();
      
      const eventType = `${connectorNormalized}.reindex`;
      
      const payload = {
        orgId: reindexPayload.orgId,
        connector: connectorNormalized,
        connectorId: reindexPayload.connectorId,
        statusFilters: reindexPayload.statusFilters,
      };

      const event: SyncEvent = {
        eventType: eventType,
        timestamp: Date.now(),
        payload: payload,
      };

      await this.syncEventProducer.publishEvent(event);
      logger.info(`Published ${eventType} event for app ${reindexPayload.app}`);

      return { success: true };
    } catch (eventError: any) {
      logger.error('Failed to publish reindex failed record event', {
        error: eventError,
      });
      return { success: false, error: eventError.message };
    }
  }

  async createReindexFailedRecordEventPayload(
    reindexPayload: any,
  ): Promise<ConnectorSyncEvent> {
    return {
      orgId: reindexPayload.orgId,
      origin: reindexPayload.origin,
      connector: reindexPayload.app,
      connectorId: reindexPayload.connectorId,
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
      connectorId: resyncConnectorEventPayload.connectorId,
      createdAtTimestamp: Date.now().toString(),
      updatedAtTimestamp: Date.now().toString(),
      sourceCreatedAtTimestamp: Date.now().toString(),
    };
  }

}
