import { FileProcessorService } from "./fp.service";
import { FileProcessorConfiguration } from "./fp.interface";
import { FileProcessingType } from "./fp.constant";
import { Logger } from "../../services/logger.service";
import { NextFunction, RequestHandler, Response } from "express";
import { AuthenticatedUserRequest } from "../types";

const logger = Logger.getInstance({
    service: 'FileProcessorFactory',
});

export class FileProcessorFactory {
    static createJSONUploadProcessor(configuration: FileProcessorConfiguration): {
        getMiddleware: RequestHandler[]
    } {
        logger.debug('Creating JSON upload processor');
        const config = {
            ...configuration,
            processingType: FileProcessingType.JSON
        };
        let serviceInstance: FileProcessorService | null = null;
        const getService = () => {
            if (!serviceInstance) {
                serviceInstance = new FileProcessorService(config);
            }
            return serviceInstance;
        };
        return {
            getMiddleware: [
                // First middleware - uses shared service instance
                (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
                    const service = getService();
                    const uploadMiddleware = service.upload();
                    
                    // Call upload middleware with custom next that continues to process
                    uploadMiddleware(req, res, (err) => {
                        if (err) {
                            logger.error("err in createJSONUploadProcessor upload middleware", err);
                            return next(err);
                        }
                        
                        // If upload succeeds, proceed to processing
                        const processMiddleware = service.processFiles();
                        processMiddleware(req, res, (err) => {
                            if (err) {
                                logger.error("err in createJSONUploadProcessor process middleware", err);
                                return next(err);
                            }
                            next();
                        });
                    });
                },
            ]
        };
    }

    static createBufferUploadProcessor(configuration: FileProcessorConfiguration): {
        getMiddleware: RequestHandler[]
    } {
        logger.debug('Creating buffer upload processor');
        const config = {
            ...configuration,
            processingType: FileProcessingType.BUFFER
        };
        let serviceInstance: FileProcessorService | null = null;
        const getService = () => {
            if (!serviceInstance) {
                serviceInstance = new FileProcessorService(config);
            }
            return serviceInstance;
        };
        return {
            getMiddleware: [
                // First middleware - uses shared service instance
                (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
                    const service = getService();
                    const uploadMiddleware = service.upload();
                    
                    // Call upload middleware with custom next that continues to process
                    uploadMiddleware(req, res, (err) => {
                        if (err) {
                            logger.error("err in createBufferUploadProcessor upload middleware", err);
                            return next(err);
                        }
                        
                        // If upload succeeds, proceed to processing
                        const processMiddleware = service.processFiles();
                        processMiddleware(req, res, (err) => {
                            if (err) {
                                logger.error("err in createBufferUploadProcessor process middleware", err);
                                return next(err);
                            }
                            next();
                        });
                    });
                },
            ]
        };
    }
}