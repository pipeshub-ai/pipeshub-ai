import { NextFunction, Response } from 'express';

import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { AIModelsConfig } from '../types/ai-models.types';
import {
  findOpenAIModel,
  openAIModelNotFound,
  toOpenAIModelList,
} from '../services/openaiModelList';

type ReadModels = () => Promise<AIModelsConfig | null>;

export const listOpenAIModels =
  (read: ReadModels) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      res.status(200).json(toOpenAIModelList(await read()));
    } catch (error) {
      next(error);
    }
  };

export const retrieveOpenAIModel =
  (read: ReadModels) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const params = req.params as Record<string, string | undefined>;
      // Express already decoded this capture. Decoding again throws when the
      // id itself contains a percent sign (stored "my%model", request "my%25model").
      const modelId = params['0'] ?? params.modelId ?? '';
      const found = findOpenAIModel(await read(), modelId);
      if (!found) {
        res.status(404).json(openAIModelNotFound(modelId));
        return;
      }
      res.status(200).json(found);
    } catch (error) {
      next(error);
    }
  };
