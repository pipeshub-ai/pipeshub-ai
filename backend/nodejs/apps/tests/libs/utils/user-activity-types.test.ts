import { expect } from 'chai';
import fs from 'fs';
import Module from 'module';
import path from 'path';
import mongoose from 'mongoose';
import {
  USER_ACTIVITY_TYPES,
  USER_LOGIN_MODES,
  userActivitiesType,
} from '../../../src/libs/utils/userActivities.utils';
import { UserActivities } from '../../../src/modules/auth/schema/userActivities.schema';
import { checkActivity } from '../../../src/libs/repositories/activity/pg';
import { EDITION_ACTIVITY_TYPES, EDITION_LOGIN_MODES } from '../../../src/edition';

// R46FIX-1: WP-73 added ACCOUNT DISABLED / ENABLED to the utils but not to the
// document schema, so every deactivation answered 503 on Mongo. Each store must
// accept every type and login mode the code can write.

const MIGRATIONS = path.resolve(__dirname, '../../../../../db/versions');

function sqlFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return sqlFiles(p);
    return e.name.endsWith('.sql') ? [p] : [];
  });
}

const SRC = path.resolve(__dirname, '../../../src');
const EDITION = require.resolve(path.join(SRC, 'edition'));
const UTILS = require.resolve(path.join(SRC, 'libs/utils/userActivities.utils'));
const PG = require.resolve(path.join(SRC, 'libs/repositories/activity/pg'));
const SCHEMA = require.resolve(path.join(SRC, 'modules/auth/schema/userActivities.schema'));
const MODEL = 'userActivities';

/**
 * The activity lists, the PostgreSQL check and the document schema, evaluated against an edition that declares these
 * values. The modules (and the registered model) every other test uses are put back before it returns.
 */
function loadWithEdition(types: string[], modes: string[]) {
  const saved = new Map([EDITION, UTILS, PG, SCHEMA].map((k) => [k, require.cache[k]]));
  const model = mongoose.models[MODEL];
  try {
    const real = require(EDITION) as Record<string, unknown>;
    const fake = new Module(EDITION);
    fake.filename = EDITION;
    fake.loaded = true;
    fake.exports = { ...real, EDITION_ACTIVITY_TYPES: types, EDITION_LOGIN_MODES: modes };
    require.cache[EDITION] = fake;
    for (const k of [UTILS, PG, SCHEMA]) delete require.cache[k];
    mongoose.deleteModel(MODEL);
    return {
      utils: require(UTILS) as typeof import('../../../src/libs/utils/userActivities.utils'),
      check: (require(PG) as typeof import('../../../src/libs/repositories/activity/pg')).checkActivity,
      Model: (require(SCHEMA) as typeof import('../../../src/modules/auth/schema/userActivities.schema')).UserActivities,
    };
  } finally {
    for (const [k, m] of saved) {
      if (m) require.cache[k] = m;
      else delete require.cache[k];
    }
    if (mongoose.models[MODEL]) mongoose.deleteModel(MODEL);
    // the same model object, so code that imported it before this call still finds it registered
    if (model) {
      Object.assign(mongoose.models, { [MODEL]: model });
      Object.assign(mongoose.connection.models, { [MODEL]: model });
      Object.assign(mongoose.connection.collections, { [model.collection.name]: model.collection });
    }
  }
}

function row(activityType: string, loginMode?: string) {
  return {
    email: 'a@b.test',
    userId: new mongoose.Types.ObjectId(),
    orgId: new mongoose.Types.ObjectId(),
    activityType,
    ipAddress: '127.0.0.1',
    ...(loginMode ? { loginMode } : {}),
  };
}

describe('R46FIX-1: user activity types are accepted by every store', () => {
  it('lists every type the utils define, and the ones the edition adds', () => {
    expect([...USER_ACTIVITY_TYPES].sort()).to.deep.equal(
      [...new Set([...Object.values(userActivitiesType), ...EDITION_ACTIVITY_TYPES])].sort(),
    );
    expect(USER_ACTIVITY_TYPES).to.include.members(['ACCOUNT DISABLED', 'ACCOUNT ENABLED']);
  });

  for (const type of Object.values(userActivitiesType)) {
    it(`the document schema accepts ${type}`, () => {
      const err = new UserActivities(row(type)).validateSync();
      expect(err, String(err)).to.equal(undefined);
    });

    it(`the PostgreSQL store accepts ${type}`, () => {
      expect(() => checkActivity(row(type) as never)).not.to.throw();
    });
  }

  for (const mode of USER_LOGIN_MODES) {
    it(`both stores accept login mode ${mode}`, () => {
      expect(new UserActivities(row('LOGIN', mode)).validateSync()).to.equal(undefined);
      expect(() => checkActivity(row('LOGIN', mode) as never)).not.to.throw();
    });
  }

  it('both stores still refuse a type nobody defined', () => {
    expect(new UserActivities(row('NOT A TYPE')).validateSync()).to.not.equal(undefined);
    expect(() => checkActivity(row('NOT A TYPE') as never)).to.throw(/unknown activity type/);
  });

  it('lists every built-in login mode, and the ones the edition adds', () => {
    expect(USER_LOGIN_MODES).to.include.members(['OTP', 'PASSWORD', 'AUTH SERVICE', 'GOOGLE OAUTH', 'MICROSOFT OAUTH', 'SSO', 'AZUREAD OAUTH', 'OAUTH', 'GITHUB OAUTH']);
    expect(USER_LOGIN_MODES).to.include.members([...EDITION_LOGIN_MODES]);
    expect(new Set(USER_LOGIN_MODES).size).to.equal(USER_LOGIN_MODES.length);
  });

  it('both stores still refuse a login mode nobody defined', () => {
    expect(new UserActivities(row('LOGIN', 'NOT A MODE')).validateSync()).to.not.equal(undefined);
    expect(() => checkActivity(row('LOGIN', 'NOT A MODE') as never)).to.throw(/unknown login mode/);
  });

  // This build's edition lists may be empty, so the merge is proven with stubbed ones: the lists and both validators are
  // loaded afresh against an edition that declares a new type, a new mode and a value that is already built in.
  describe('with an edition that declares its own values', () => {
    const EDITION_TYPE = 'TEST EDITION ACTIVITY';
    const EDITION_MODE = 'TEST EDITION MODE';
    type Loaded = {
      utils: typeof import('../../../src/libs/utils/userActivities.utils');
      check: typeof checkActivity;
      Model: typeof UserActivities;
    };
    const STUB_TYPES = [EDITION_TYPE, 'LOGIN'];
    const STUB_MODES = [EDITION_MODE, 'OTP'];
    let loaded: Loaded;
    let builtIn: Loaded;

    before(() => {
      loaded = loadWithEdition(STUB_TYPES, STUB_MODES);
      // the built-in values alone, whatever this build's own edition declares
      builtIn = loadWithEdition([], []);
    });

    it('merges them into both lists once each', () => {
      const { USER_ACTIVITY_TYPES: types, USER_LOGIN_MODES: modes } = loaded.utils;
      expect(types).to.include(EDITION_TYPE);
      expect(modes).to.include(EDITION_MODE);
      // each list is exactly the built-in values plus the stub's, and no value appears twice
      const expectedTypes = [...new Set([...builtIn.utils.USER_ACTIVITY_TYPES, ...STUB_TYPES])].sort();
      const expectedModes = [...new Set([...builtIn.utils.USER_LOGIN_MODES, ...STUB_MODES])].sort();
      expect([...types].sort()).to.deep.equal(expectedTypes);
      expect([...modes].sort()).to.deep.equal(expectedModes);
      expect([...builtIn.utils.USER_ACTIVITY_TYPES].sort()).to.deep.equal([...Object.values(userActivitiesType)].sort());
    });

    it('both stores accept the declared type and mode', () => {
      expect(new loaded.Model(row(EDITION_TYPE, EDITION_MODE)).validateSync()).to.equal(undefined);
      expect(() => loaded.check(row(EDITION_TYPE, EDITION_MODE) as never)).not.to.throw();
    });

    it('both stores still refuse a value the edition did not declare', () => {
      expect(new loaded.Model(row('UNDECLARED ACTIVITY')).validateSync()).to.not.equal(undefined);
      expect(() => loaded.check(row('UNDECLARED ACTIVITY') as never)).to.throw(/unknown activity type/);
      expect(new loaded.Model(row('LOGIN', 'UNDECLARED MODE')).validateSync()).to.not.equal(undefined);
      expect(() => loaded.check(row('LOGIN', 'UNDECLARED MODE') as never)).to.throw(/unknown login mode/);
    });

    it('leaves the modules every other test uses as they were', () => {
      expect(mongoose.models[MODEL]).to.equal(UserActivities);
      expect(require(UTILS)).to.have.property('USER_ACTIVITY_TYPES', USER_ACTIVITY_TYPES);
      expect(USER_ACTIVITY_TYPES).to.not.include(EDITION_TYPE);
      expect(() => checkActivity(row(EDITION_TYPE) as never)).to.throw(/unknown activity type/);
      expect(new UserActivities(row(EDITION_TYPE)).validateSync()).to.not.equal(undefined);
    });
  });

  it('a database constraint on activity_type, if a migration adds one, names every type', () => {
    const files = sqlFiles(MIGRATIONS);
    expect(files.length).to.be.greaterThan(0);
    for (const file of files) {
      const sql = fs.readFileSync(file, 'utf8');
      const checks = sql.match(/CHECK\s*\([^;]*activity_type[^;]*\)/gi) ?? [];
      for (const check of checks) {
        for (const type of USER_ACTIVITY_TYPES) {
          expect(check, `${path.basename(file)} omits ${type}`).to.include(`'${type}'`);
        }
      }
    }
  });
});
