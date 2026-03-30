import { Response } from "express";
import { z } from "zod";
import { ValidationError } from "../libs/errors/validation.error";
import { ValidationUtils } from "../libs/utils/validation.utils";

export const sendValidatedJson = (
    res: Response,
    schema: z.ZodTypeAny,
    payload: unknown,
    statusCode: number,
  ): Response => {
    if (statusCode < 200 || statusCode >= 300) {
      return res.status(statusCode).json(payload);
    }

    const result = schema.safeParse(payload);
    if (!result.success) {
      throw new ValidationError('Validation failed', ValidationUtils.formatZodError(result.error));
    }
    return res.status(statusCode).json(result.data);
  };