import { Readable } from 'stream';
import FormData from 'form-data';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import { FileBufferInfo } from '../../../libs/middlewares/file_processor/fp.interface';
import axios from 'axios';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { endpoint } from '../../storage/constants/constants';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { DefaultStorageConfig } from '../../tokens_manager/services/cm.service';
import { RecordRelationService } from '../services/kb.relation.service';
import { IRecordDocument } from '../types/record';
import { IFileRecordDocument } from '../types/file_record';
import { Event, EventType } from '../services/records_events.service';
import { InternalServerError } from '../../../libs/errors/http.errors';

const logger = Logger.getInstance({
  service: 'knowledge_base.utils',
});

const axiosInstance = axios.create({
  maxRedirects: 0,
});

export interface StorageResponseMetadata {
  documentId: string;
  documentName: string;
}

export const saveFileToStorageAndGetDocumentId = async (
  req: AuthenticatedUserRequest,
  file: FileBufferInfo,
  documentName: string,
  isVersionedFile: boolean,
  record: IRecordDocument,
  fileRecord: IFileRecordDocument,
  keyValueStoreService: KeyValueStoreService,
  defaultConfig: DefaultStorageConfig,
  recordRelationService: RecordRelationService,
): Promise<StorageResponseMetadata> => {
  const formData = new FormData();

  // Add the file with proper metadata
  formData.append('file', file.buffer, {
    filename: file.originalname,
    contentType: file.mimetype,
  });
  const url = (await keyValueStoreService.get<string>(endpoint)) || '{}';

  const storageUrl = JSON.parse(url).storage.endpoint || defaultConfig.endpoint;

  // Add other required fields
  formData.append(
    'documentPath',
    `PipesHub/KnowledgeBase/private/${req.user?.userId}`,
  );
  formData.append('isVersionedFile', isVersionedFile.toString());
  formData.append('documentName', getFilenameWithoutExtension(documentName));

  try {
    const response = await axiosInstance.post(
      `${storageUrl}/api/v1/document/upload`,
      formData,
      {
        headers: {
          ...formData.getHeaders(),
          Authorization: req.headers.authorization,
        },
      },
    );

    // async call the placeholder call here
    // we get the doc id and all other meta
    // call the direct upload here by passing the doc id
    // we get the signed url here for upload
    // now pass the buffer to the signed url and run in background

    return {
      documentId: response.data?._id,
      documentName: response.data?.documentName,
    };
  } catch (error: any) {
    if (error.response.status === HTTP_STATUS.PERMANENT_REDIRECT) {
      const redirectUrl = error.response.headers.location;
      if (process.env.NODE_ENV == 'development') {
        logger.info('Redirecting to storage url', { redirectUrl });
      }

      // Extract document information from headers
      const documentId = error.response.headers['x-document-id'];
      const documentName = error.response.headers['x-document-name'];

      runInBackGround(
        file.buffer,
        file.mimetype,
        redirectUrl,
        documentId,
        documentName,
        record,
        fileRecord,
        recordRelationService,
        keyValueStoreService,
      );
      return { documentId, documentName };
    } else {
      logger.error('Error uploading file to storage', {
        error: error.response.data,
      });
      throw error;
    }
  }
};

function runInBackGround(
  buffer: Buffer,
  mimetype: string,
  redirectUrl: string,
  documentId: string,
  documentName: string,
  record: IRecordDocument,
  fileRecord: IFileRecordDocument,
  recordRelationService: RecordRelationService,
  keyValueStoreService: KeyValueStoreService,
) {
  // Start the upload in the background
  (async () => {
    try {
      // Create a readable stream from the buffer
      const bufferStream = new Readable();
      bufferStream.push(buffer);
      bufferStream.push(null); // Signal end of stream

      // Start the upload but don't await it
      axios({
        method: 'put',
        url: redirectUrl,
        data: bufferStream,
        headers: {
          'Content-Type': mimetype,
          'Content-Length': buffer.length,
        },
        maxContentLength: Infinity,
        maxBodyLength: Infinity,
      })
        .then(async (response) => {
          // TODO: Notify the user about the upload completion
          logger.info('Background upload completed successfully', {
            documentId,
            documentName,
            status: response.status,
            record: record._key,
          });

          record.externalRecordId = documentId;
          record.recordName = documentName;
          fileRecord.name = documentName;

          if (response.status === 200) {
            try {
              const recordExists =
                await recordRelationService.checkRecordExists(record._key);
              logger.info('Record exists, key', {
                recordExists,
                recordKey: record._key,
              });
              if (!recordExists) {
                throw new InternalServerError(
                  `Record ${record._key} not found in database`,
                );
              }
              // Create the payload using the separate function
              const newRecordPayload =
                await recordRelationService.createNewRecordEventPayload(
                  record,
                  keyValueStoreService,
                  fileRecord,
                );

              const event: Event = {
                eventType: EventType.NewRecordEvent,
                timestamp: Date.now(),
                payload: newRecordPayload,
              };

              // Return the promise for event publishing
              return recordRelationService.eventProducer.publishEvent(event);
            } catch (error) {
              logger.error(
                `Failed to create event payload for record ${record._key}`,
                { error },
              );
              // Return a resolved promise to avoid failing the Promise.all
              return Promise.resolve();
            }
          }
        })
        .catch((uploadError) => {
          // TODO: Notify the user about the upload failure
          logger.error('Background upload failed', {
            documentId,
            documentName,
            error: uploadError.message,
          });
        });
    } catch (error: any) {
      logger.error('Error setting up background upload', {
        documentId,
        documentName,
        error: error.message,
      });
    }
  })();
}

export const uploadNextVersionToStorage = async (
  req: AuthenticatedUserRequest,
  file: FileBufferInfo,
  documentId: string,
  keyValueStoreService: KeyValueStoreService,
  defaultConfig: DefaultStorageConfig,
): Promise<StorageResponseMetadata> => {
  const formData = new FormData();

  // Add the file with proper metadata
  formData.append('file', file.buffer, {
    filename: file.originalname,
    contentType: file.mimetype,
  });

  const url = (await keyValueStoreService.get<string>(endpoint)) || '{}';

  const storageUrl = JSON.parse(url).storage.endpoint || defaultConfig.endpoint;

  try {
    const response = await axiosInstance.post(
      `${storageUrl}/api/v1/document/${documentId}/uploadNextVersion`,
      formData,
      {
        headers: {
          ...formData.getHeaders(),
          Authorization: req.headers.authorization,
        },
      },
    );

    return {
      documentId: response.data?._id,
      documentName: response.data?.documentName,
    };
  } catch (error: any) {
    logger.error('Error uploading file to storage', error.response.message);
    throw error;
  }
};

function getFilenameWithoutExtension(originalname: string) {
  const fileExtension = originalname.slice(originalname.lastIndexOf('.') + 1);
  return originalname.slice(0, -fileExtension.length - 1);
}
