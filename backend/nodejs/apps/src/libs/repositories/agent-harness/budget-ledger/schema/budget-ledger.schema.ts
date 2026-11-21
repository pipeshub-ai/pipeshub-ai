import { Model, Schema } from 'mongoose';
import type {
  BudgetAccount,
  BudgetReservation,
} from '../../../../../modules/agent-harness/domain/budget-ledger';
import { harnessModel } from '../../schema/harness-model.schema';

/** `_id` is `budgetAccountId(orgId, runId)`: the run index is the account's identity. */
export type BudgetAccountDocument = BudgetAccount & { _id: string };

/** `_id` is `budgetReservationId(orgId, runId, key)`: the idempotency key. */
export type BudgetReservationDocument = BudgetReservation & { _id: string };

const amountSchema = new Schema(
  {
    usdMicros: { type: Number, required: true },
    tokens: { type: Number, required: true },
  },
  { _id: false },
);

/** A call's usage as the provider reported it; the cost ledger stores the same subdocument. */
export const usageSchema = new Schema(
  {
    inputTokens: { type: Number, required: true },
    outputTokens: { type: Number, required: true },
    cacheReadTokens: { type: Number, required: true },
    cacheWriteTokens: { type: Number, required: true },
  },
  { _id: false },
);

const capSchema = new Schema(
  {
    usdMicros: { type: Number, default: null },
    tokens: { type: Number, default: null },
  },
  { _id: false },
);

const accountSchema = new Schema<BudgetAccountDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    runId: { type: String, required: true },
    rootRunId: { type: String, required: true },
    parentRunId: { type: String, default: null },
    ancestors: { type: [String], default: [] },
    cap: { type: capSchema, required: true },
    committed: { type: amountSchema, required: true },
    reserved: { type: amountSchema, required: true },
    used: { type: usageSchema, default: undefined },
    estimated: { type: Number, default: undefined },
    closed: { type: Boolean, required: true, default: false },
    openedAt: { type: Number, required: true },
  },
  {
    collection: 'agentHarnessBudgetAccounts',
    versionKey: false,
    autoIndex: false,
  },
);

export const BUDGET_RESERVATIONS_EXPIRY_INDEX = 'org_root_state_expiry';
export const BUDGET_RESERVATIONS_HOLDS_INDEX = 'org_holds_open_expiry';

const reservationSchema = new Schema<BudgetReservationDocument>(
  {
    _id: { type: String, required: true },
    orgId: { type: String, required: true },
    runId: { type: String, required: true },
    rootRunId: { type: String, required: true },
    key: { type: String, required: true },
    accounts: { type: [String], required: true },
    requested: { type: amountSchema, required: true },
    granted: { type: amountSchema, required: true },
    expiresAt: { type: Number, required: true },
    state: { type: String, required: true },
    createdAt: { type: Number, required: true },
    charged: { type: amountSchema, default: undefined },
    crossed: { type: Boolean, default: undefined },
    usage: { type: usageSchema, default: undefined },
    estimated: { type: Boolean, default: undefined },
    hold: { type: Boolean, default: undefined },
    holder: {
      type: new Schema(
        {
          userId: { type: String, required: true },
          agentKey: { type: String, default: null },
        },
        { _id: false },
      ),
      default: undefined,
    },
    // SEC-C-20: the reserving attempt's fence; absent on a reservation made before the binding.
    fence: { type: Number, default: undefined },
  },
  {
    collection: 'agentHarnessBudgetReservations',
    versionKey: false,
    autoIndex: false,
  },
);
// What expires on a tree: its open reservations past their time.
reservationSchema.index(
  { orgId: 1, rootRunId: 1, state: 1, expiresAt: 1 },
  { name: BUDGET_RESERVATIONS_EXPIRY_INDEX },
);
// WP-66: an org's live holds, which every reservation under a shared budget sums.
reservationSchema.index(
  { orgId: 1, state: 1, expiresAt: 1 },
  {
    name: BUDGET_RESERVATIONS_HOLDS_INDEX,
    partialFilterExpression: { hold: true },
  },
);

const model = <T>(name: string, schema: Schema<T>): Model<T> =>
  harnessModel(name, schema);

export const AgentBudgetAccountModel = model(
  'AgentHarnessBudgetAccount',
  accountSchema,
);
export const AgentBudgetReservationModel = model(
  'AgentHarnessBudgetReservation',
  reservationSchema,
);
