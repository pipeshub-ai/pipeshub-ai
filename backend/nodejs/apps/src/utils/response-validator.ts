import { Response } from "express";
import { z } from "zod";
import { Logger } from "../libs/services/logger.service";
import { ValidationUtils } from "../libs/utils/validation.utils";

const logger = Logger.getInstance();

export const sendValidatedJson = (
    res: Response,
    schema: z.ZodTypeAny,
    payload: any,
    statusCode: number,
  ): Response => {
    const result = schema.safeParse(payload);
    if (!result.success) {
      logger.warn('Response schema mismatch: extra/missing field or incorrect value type', {
        errors: ValidationUtils.formatZodError(result.error),
      });
      return res.status(statusCode).json(payload);
    }
    return res.status(statusCode).json(result.data);
  };
