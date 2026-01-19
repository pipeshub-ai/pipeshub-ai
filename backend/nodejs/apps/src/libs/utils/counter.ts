import mongoose, { Schema, Model } from 'mongoose'
import slug from 'slug'

interface CounterDocument {
  _id: string
  name?: string
  seq: number
}

const counterSchema = new Schema<CounterDocument>({
  _id: { type: String, required: true },
  name: { type: String },
  seq: { type: Number, default: 1000 },
})

export const Counter: Model<CounterDocument> =
  mongoose.models.Counter ||
  mongoose.model<CounterDocument>('Counter', counterSchema)

const getNextSequence = async (name: string): Promise<number> => {
  const counter = await Counter.findOneAndUpdate(
    { name },
    { $inc: { seq: 1 } },
    { new: true, upsert: true },
  )
  return counter.seq
}

export const generateUniqueSlug = async (name: string): Promise<string> => {
  const counter = await getNextSequence(name)
  return slug(`${name}-${counter}`)
}
