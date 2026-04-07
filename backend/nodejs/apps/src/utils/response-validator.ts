import { Response } from "express";
import { z } from "zod";
import { Logger } from "../libs/services/logger.service";
import { ValidationUtils } from "../libs/utils/validation.utils";

const logger = Logger.getInstance();

export const sendValidatedJson = (
    res: Response,
    schema: z.ZodTypeAny,
    payload: unknown,
    statusCode: number,
  ): Response => {
    const result = schema.safeParse(payload);
    if (!result.success) {
      logger.warn('Response validation failed, sending unvalidated payload', {
        errors: ValidationUtils.formatZodError(result.error),
      });
      return res.status(statusCode).json(payload);
    }
    return res.status(statusCode).json(result.data);
  };
