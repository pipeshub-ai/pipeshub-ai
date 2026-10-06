import { Types } from 'mongoose'
import { ACTORS, ActorName } from '../../helpers/conversation-world'

export const idOf = (who: ActorName): string => String(ACTORS[who].userId)
export const user = (who: ActorName) => ({ type: 'user' as const, id: idOf(who) })
export const team = (id: string) => ({ type: 'team' as const, id })
export const assistant = { type: 'assistant' as const, id: 'self' }
export const agent = (id = 'agent-1') => ({ type: 'agent' as const, id })
export const newId = (): string => String(new Types.ObjectId())
