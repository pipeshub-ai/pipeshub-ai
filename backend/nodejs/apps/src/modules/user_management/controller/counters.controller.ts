import {Counter} from "../schema/counter.schema";


const getNextSequence = async (name:string):Promise<number> => {
  const seq = 1;

  const counter = await Counter.findOneAndUpdate(
    { name: name },
    { $inc: { seq: seq } },
    { new: true, upsert: true }
  );
  return counter.seq;
};

export const generateUniqueSlug = async (name:string) => {
  const slug = require("slug");
  const counter = await getNextSequence(name);
  return slug(`${name}-${counter}`);
};
