import { Model, Schema } from 'mongoose';
import {
  ControlPlaneLiveness,
  LivenessObservation,
} from '../../../../modules/agent-harness/ports/control-plane-liveness';
import { MAJORITY_WRITE } from '../durable-transaction/mongo';
import { MongoTime, mongoServerTime } from '../time/mongo';
import { harnessModel } from '../schema/harness-model.schema';

interface LivenessDocument {
  _id: string;
  seenAt: number;
  previousSeenAt: number | null;
  holdoffUntil: number;
}

const livenessSchema = new Schema<LivenessDocument>(
  {
    _id: { type: String, required: true },
    seenAt: { type: Number, required: true },
    previousSeenAt: { type: Number, default: null },
    holdoffUntil: { type: Number, required: true },
  },
  { collection: 'agentHarnessLiveness', versionKey: false, autoIndex: false },
);

export const AgentHarnessLivenessModel: Model<LivenessDocument> =
  harnessModel<LivenessDocument>('AgentHarnessLiveness', livenessSchema);

const CONTROL_PLANE = 'control-plane';

/** On DB time, so a Node replica's jumped clock cannot fake or hide a gap. */
export class MongoControlPlaneLiveness implements ControlPlaneLiveness {
  constructor(private readonly time: MongoTime = mongoServerTime) {}

  async observe(): Promise<LivenessObservation> {
    const document = await AgentHarnessLivenessModel.findOneAndUpdate(
      { _id: CONTROL_PLANE },
      [
        {
          $set: {
            previousSeenAt: { $ifNull: ['$seenAt', null] },
            seenAt: this.time.expr(),
            holdoffUntil: { $ifNull: ['$holdoffUntil', 0] },
          },
        },
      ],
      {
        updatePipeline: true,
        upsert: true,
        returnDocument: 'after',
        ...MAJORITY_WRITE,
      },
    ).lean();
    return {
      now: document.seenAt,
      previousSeenAt: document.previousSeenAt ?? null,
      holdoffUntil: document.holdoffUntil,
    };
  }

  async holdOff(until: number): Promise<void> {
    await AgentHarnessLivenessModel.updateOne(
      { _id: CONTROL_PLANE },
      { $max: { holdoffUntil: until } },
      // A holdoff ordered before the first sweeper tick must still hold.
      { upsert: true, ...MAJORITY_WRITE },
    );
  }
}
