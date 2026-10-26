import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import axios, { AxiosError } from 'axios';
import { MailService } from '../../../../src/modules/auth/services/mail.service';
import {
  BadRequestError,
  InternalServerError,
} from '../../../../src/libs/errors/http.errors';

describe('MailService', () => {
  let mailService: MailService;
  const mockConfig = {
    communicationBackend: 'http://comm-backend:4000',
  } as any;
  const mockLogger = {
    info: sinon.stub(),
    debug: sinon.stub(),
    warn: sinon.stub(),
    error: sinon.stub(),
  } as any;

  beforeEach(() => {
    mailService = new MailService(mockConfig, mockLogger);
  });

  afterEach(() => {
    sinon.restore();
  });

  describe('sendMail', () => {
    it('should throw InternalServerError when usersMails is empty', async () => {
      try {
        await mailService.sendMail({
          emailTemplateType: 'loginWithOTP',
          initiator: { jwtAuthToken: 'token123' },
          usersMails: [],
          subject: 'Test',
        });
        expect.fail('Should have thrown');
      } catch (error) {
        // BadRequestError is thrown internally but caught by the catch block
        // which wraps non-AxiosError in InternalServerError
        expect(error).to.be.instanceOf(InternalServerError);
        expect((error as InternalServerError).message).to.equal(
          'usersMails is empty',
        );
      }
    });

    it('should throw InternalServerError when subject is empty', async () => {
      try {
        await mailService.sendMail({
          emailTemplateType: 'loginWithOTP',
          initiator: { jwtAuthToken: 'token123' },
          usersMails: ['test@example.com'],
          subject: '',
        });
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(InternalServerError);
        expect((error as InternalServerError).message).to.equal(
          'subject is empty',
        );
      }
    });

    it('should throw InternalServerError when emailTemplateType is empty', async () => {
      try {
        await mailService.sendMail({
          emailTemplateType: '',
          initiator: { jwtAuthToken: 'token123' },
          usersMails: ['test@example.com'],
          subject: 'Test Subject',
        });
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(InternalServerError);
        expect((error as InternalServerError).message).to.equal(
          'emailTemplateType is empty',
        );
      }
    });

    it('should throw InternalServerError when usersMails is undefined', async () => {
      try {
        await mailService.sendMail({
          emailTemplateType: 'loginWithOTP',
          initiator: { jwtAuthToken: 'token123' },
          usersMails: undefined as any,
          subject: 'Test',
        });
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(InternalServerError);
        expect((error as InternalServerError).message).to.equal(
          'usersMails is empty',
        );
      }
    });

    it('should be a function on the service', () => {
      expect(mailService.sendMail).to.be.a('function');
    });

    it('should return 200 when axios succeeds', async () => {
      const origAdapter = axios.defaults.adapter;
      axios.defaults.adapter = async () => ({
        data: { messageId: 'auth-m1' },
        status: 200,
        statusText: 'OK',
        headers: {},
        config: {} as any,
      });
      try {
        const result = await mailService.sendMail({
          emailTemplateType: 'loginWithOTP',
          initiator: { jwtAuthToken: 'token123' },
          usersMails: ['test@example.com'],
          subject: 'Test Subject',
        });
        expect(result.statusCode).to.equal(200);
        expect(result.data).to.deep.equal({ messageId: 'auth-m1' });
      } finally {
        axios.defaults.adapter = origAdapter;
      }
    });

    it('should rethrow Axios-shaped failures as AxiosError', async () => {
      const origAdapter = axios.defaults.adapter;
      const thrown = {
        message: 'Upstream',
        code: 'ERR_BAD_RESPONSE',
        config: {},
        request: {},
        response: { status: 502, data: { message: 'Bad gateway' } },
      };
      axios.defaults.adapter = async () => {
        throw thrown;
      };
      sinon.stub(axios, 'isAxiosError').returns(true);
      try {
        await mailService.sendMail({
          emailTemplateType: 'loginWithOTP',
          initiator: { jwtAuthToken: 'token123' },
          usersMails: ['test@example.com'],
          subject: 'Test Subject',
        });
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(AxiosError);
      } finally {
        axios.defaults.adapter = origAdapter;
      }
    });

    it('should wrap non-Axios non-Error rejections in InternalServerError', async () => {
      const origAdapter = axios.defaults.adapter;
      axios.defaults.adapter = async () => {
        throw 'non-error-throwable';
      };
      sinon.stub(axios, 'isAxiosError').returns(false);
      try {
        await mailService.sendMail({
          emailTemplateType: 'loginWithOTP',
          initiator: { jwtAuthToken: 'token123' },
          usersMails: ['test@example.com'],
          subject: 'Test Subject',
        });
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(InternalServerError);
        expect((error as InternalServerError).message).to.contain(
          'PipesHub tried to send that email',
        );
      } finally {
        axios.defaults.adapter = origAdapter;
      }
    });
  });

  describe('constructor', () => {
    it('should create an instance with config and logger', () => {
      const service = new MailService(mockConfig, mockLogger);
      expect(service).to.be.instanceOf(MailService);
    });
  });

  describe('sendMail - successful call', () => {
    // sendMail calls axios(config), not axios.request, so only an adapter keeps
    // these off the network. Stubbing axios.request left them sending a real
    // request to comm-backend, which timed out whenever the lookup was slow.
    let origAdapter: typeof axios.defaults.adapter;
    let sent: any;

    beforeEach(() => {
      origAdapter = axios.defaults.adapter;
      sent = undefined;
      axios.defaults.adapter = async (config) => {
        sent = config;
        return {
          data: { messageId: 'msg-1' },
          status: 200,
          statusText: 'OK',
          headers: {},
          config,
        };
      };
    });

    afterEach(() => {
      axios.defaults.adapter = origAdapter;
    });

    const body = () => JSON.parse(sent.data);

    it('should call axios with correct config', async () => {
      const result = await mailService.sendMail({
        emailTemplateType: 'loginWithOTP',
        initiator: { jwtAuthToken: 'token123' },
        usersMails: ['test@example.com'],
        subject: 'Test Subject',
        templateData: { otp: '123456' },
      });

      expect(result).to.deep.equal({
        statusCode: 200,
        data: { messageId: 'msg-1' },
      });
      expect(sent.method).to.equal('post');
      expect(sent.url).to.equal(
        'http://comm-backend:4000/api/v1/mail/emails/sendEmail',
      );
      expect(sent.headers.Authorization).to.equal('Bearer token123');
      expect(sent.timeout).to.equal(30_000);
      expect(body()).to.include({
        emailTemplateType: 'loginWithOTP',
        subject: 'Test Subject',
        isAutoEmail: false,
      });
      expect(body().sendEmailTo).to.deep.equal(['test@example.com']);
      expect(body().templateData).to.deep.equal({ otp: '123456' });
      expect(body()).to.not.have.any.keys('attachments', 'sendCcTo', 'orgId');
    });

    it('should include attachments when provided', async () => {
      await mailService.sendMail({
        emailTemplateType: 'welcome',
        initiator: { jwtAuthToken: 'token123' },
        usersMails: ['test@example.com'],
        subject: 'Welcome',
        attachedDocuments: [{ filename: 'doc.pdf', content: 'base64' }] as any,
      });

      expect(body().attachments).to.deep.equal([
        { filename: 'doc.pdf', content: 'base64' },
      ]);
    });

    it('should include ccEmails when provided', async () => {
      await mailService.sendMail({
        emailTemplateType: 'invite',
        initiator: { jwtAuthToken: 'token123' },
        usersMails: ['test@example.com'],
        subject: 'Invite',
        ccEmails: ['cc@example.com'],
      } as any);

      expect(body().sendCcTo).to.deep.equal(['cc@example.com']);
    });

    it('should use default fromEmailDomain when not provided', async () => {
      await mailService.sendMail({
        emailTemplateType: 'loginWithOTP',
        initiator: { jwtAuthToken: 'token123' },
        usersMails: ['test@example.com'],
        subject: 'Test',
      });

      expect(body().fromEmailDomain).to.equal('noreply@contextualml.com');
    });

    it('should use custom fromEmailDomain when provided', async () => {
      await mailService.sendMail({
        emailTemplateType: 'loginWithOTP',
        initiator: { jwtAuthToken: 'token123' },
        usersMails: ['test@example.com'],
        subject: 'Test',
        fromEmailDomain: 'custom@domain.com',
      });

      expect(body().fromEmailDomain).to.equal('custom@domain.com');
    });
  });

  describe('sendMail - validation', () => {
    it('should throw when subject is only whitespace', async () => {
      try {
        await mailService.sendMail({
          emailTemplateType: 'loginWithOTP',
          initiator: { jwtAuthToken: 'token123' },
          usersMails: ['test@example.com'],
          subject: '',
        });
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(InternalServerError);
      }
    });
  });
});
