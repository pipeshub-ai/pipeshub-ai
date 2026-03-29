import sinon from 'sinon';

export interface MockQuery {
  select: sinon.SinonStub;
  sort: sinon.SinonStub;
  lean: sinon.SinonStub;
  exec: sinon.SinonStub;
  limit: sinon.SinonStub;
  skip: sinon.SinonStub;
  populate: sinon.SinonStub;
  countDocuments: sinon.SinonStub;
  where: sinon.SinonStub;
  equals: sinon.SinonStub;
  then: undefined;
}

/**
 * Creates a chainable Mongoose query mock.
 * Model.findOne({}).select('field').lean().exec() all chain correctly.
 */
export function createMockQuery(resolvedValue: any = null): MockQuery {
  const query: MockQuery = {
    select: sinon.stub(),
    sort: sinon.stub(),
    lean: sinon.stub(),
    exec: sinon.stub().resolves(resolvedValue),
    limit: sinon.stub(),
    skip: sinon.stub(),
    populate: sinon.stub(),
    countDocuments: sinon.stub().resolves(0),
    where: sinon.stub(),
    equals: sinon.stub(),
    then: undefined, // Prevent Mocha from treating as thenable
  };
  // Make each method return the query itself for chaining
  query.select.returns(query);
  query.sort.returns(query);
  query.lean.returns(query);
  query.limit.returns(query);
  query.skip.returns(query);
  query.populate.returns(query);
  query.where.returns(query);
  query.equals.returns(query);
  return query;
}

/**
 * Creates stubs for common Mongoose model static methods.
 * Usage: const stubs = stubMongooseModel(Model);
 * Then: stubs.findOne.returns(createMockQuery(userData));
 */
export function stubMongooseModel(Model: any): Record<string, sinon.SinonStub> {
  const methods = [
    'findOne',
    'find',
    'findById',
    'create',
    'findByIdAndUpdate',
    'findOneAndUpdate',
    'findOneAndDelete',
    'updateOne',
    'updateMany',
    'deleteOne',
    'deleteMany',
    'countDocuments',
    'aggregate',
    'bulkWrite',
    'insertMany',
  ];

  const stubs: Record<string, sinon.SinonStub> = {};
  for (const method of methods) {
    if (typeof Model[method] === 'function') {
      stubs[method] = sinon.stub(Model, method);
    }
  }
  return stubs;
}

/**
 * Creates a mock Mongoose document instance with save, toObject, etc.
 */
export function createMockDocument(data: Record<string, any> = {}): any {
  return {
    ...data,
    _id: data._id || 'mock-id',
    save: sinon.stub().resolves(data),
    toObject: sinon.stub().returns(data),
    toJSON: sinon.stub().returns(data),
    set: sinon.stub(),
    get: sinon.stub(),
    deleteOne: sinon.stub().resolves(),
    populate: sinon.stub().resolves(data),
  };
}
