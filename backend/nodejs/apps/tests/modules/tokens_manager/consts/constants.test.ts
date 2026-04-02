import 'reflect-metadata'
import { expect } from 'chai'
import {
  GOOGLE_WORKSPACE_CONFIG_PATH,
  GOOGLE_WORKSPACE_CREDENTIALS_PATH,
  GOOGLE_WORKSPACE_INDIVIDUAL_CREDENTIALS_PATH,
  GOOGLE_WORKSPACE_BUSINESS_CREDENTIALS_PATH,
  GOOGLE_WORKSPACE_TOKEN_EXCHANGE_PATH,
  REFRESH_TOKEN_PATH,
  ATLASIAN_CONFIG_PATH,
  ONE_DRIVE_CONFIG_PATH,
  SHAREPOINT_CONFIG_PATH,
} from '../../../../src/modules/tokens_manager/consts/constants'

describe('tokens_manager/consts/constants', () => {
  it('should export GOOGLE_WORKSPACE_CONFIG_PATH', () => {
    expect(GOOGLE_WORKSPACE_CONFIG_PATH).to.be.a('string')
    expect(GOOGLE_WORKSPACE_CONFIG_PATH).to.include('googleWorkspaceOauthConfig')
  })

  it('should export GOOGLE_WORKSPACE_CREDENTIALS_PATH', () => {
    expect(GOOGLE_WORKSPACE_CREDENTIALS_PATH).to.be.a('string')
    expect(GOOGLE_WORKSPACE_CREDENTIALS_PATH).to.include('googleWorkspaceCredentials')
  })

  it('should export GOOGLE_WORKSPACE_INDIVIDUAL_CREDENTIALS_PATH', () => {
    expect(GOOGLE_WORKSPACE_INDIVIDUAL_CREDENTIALS_PATH).to.be.a('string')
    expect(GOOGLE_WORKSPACE_INDIVIDUAL_CREDENTIALS_PATH).to.include('individual')
  })

  it('should export GOOGLE_WORKSPACE_BUSINESS_CREDENTIALS_PATH', () => {
    expect(GOOGLE_WORKSPACE_BUSINESS_CREDENTIALS_PATH).to.be.a('string')
    expect(GOOGLE_WORKSPACE_BUSINESS_CREDENTIALS_PATH).to.include('business')
  })

  it('should export GOOGLE_WORKSPACE_TOKEN_EXCHANGE_PATH as googleapis URL', () => {
    expect(GOOGLE_WORKSPACE_TOKEN_EXCHANGE_PATH).to.equal('https://oauth2.googleapis.com/token')
  })

  it('should export REFRESH_TOKEN_PATH', () => {
    expect(REFRESH_TOKEN_PATH).to.be.a('string')
    expect(REFRESH_TOKEN_PATH).to.include('refreshIndividualConnectorToken')
  })

  it('should export ATLASIAN_CONFIG_PATH', () => {
    expect(ATLASIAN_CONFIG_PATH).to.be.a('string')
    expect(ATLASIAN_CONFIG_PATH).to.include('atlassian')
  })

  it('should export ONE_DRIVE_CONFIG_PATH', () => {
    expect(ONE_DRIVE_CONFIG_PATH).to.be.a('string')
    expect(ONE_DRIVE_CONFIG_PATH).to.include('onedrive')
  })

  it('should export SHAREPOINT_CONFIG_PATH', () => {
    expect(SHAREPOINT_CONFIG_PATH).to.be.a('string')
    expect(SHAREPOINT_CONFIG_PATH).to.include('sharepoint')
  })
})
