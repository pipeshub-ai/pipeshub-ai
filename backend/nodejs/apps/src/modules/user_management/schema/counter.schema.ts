import mongoose from "mongoose";

import { Schema, Model } from 'mongoose';

interface CounterUser {
  _id: string;
  name?: string;
  seq: number;
}

const counterSchema = new Schema<CounterUser>({
  _id: { type: String, required: true },
  name: { type: String },
  seq: { type: Number, default: 1000 },
});

export const Counter: Model<CounterUser> = mongoose.model<CounterUser>("Counter", counterSchema);