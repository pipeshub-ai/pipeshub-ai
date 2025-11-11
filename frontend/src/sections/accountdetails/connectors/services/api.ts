import axios from "src/utils/axios";
import { Connector, ConnectorConfig } from "../types/types";

const BASE_URL = '/api/v1/connectors';

export class ConnectorApiService {

    static async getConnectors(): Promise<Connector[]> {
        const response = await axios.get(`${BASE_URL}`);
        if (!response.data) throw new Error('Failed to fetch connectors');
        return response.data.connectors || [];
    }

    static async getConnectorByName(connectorName: string): Promise<Connector> {
        const response = await axios.get(`${BASE_URL}/${connectorName}`);
        if (!response.data) throw new Error('Failed to fetch connector');
        return response.data.connector;
    }

    static async getActiveConnectors(): Promise<Connector[]> {
        const response = await axios.get(`${BASE_URL}/active`);
        if (!response.data) throw new Error('Failed to fetch active connectors');
        return response.data.connectors || [];
    }

    static async getInactiveConnectors(): Promise<Connector[]> {
        const response = await axios.get(`${BASE_URL}/inactive`);
        if (!response.data) throw new Error('Failed to fetch innactive connectors');
        return response.data.connectors || [];
    }

    static async getConnectorConfig(connectorName: string): Promise<ConnectorConfig> {
        const response = await axios.get(`${BASE_URL}/config/${connectorName}`);
        if (!response.data) throw new Error('Failed to fetch connector config');
        return response.data.config;
    }

    static async getConnectorSchema(connectorName: string): Promise<any> {
        const response = await axios.get(`${BASE_URL}/schema/${connectorName}`);
        if (!response.data) throw new Error('Failed to fetch connector schema');
        return response.data.schema;
    }

    static async getConnectorConfigAndSchema(connectorName: string): Promise<{ config: ConnectorConfig; schema: any }> {
        const response = await axios.get(`${BASE_URL}/config-schema/${connectorName}`);
        if (!response.data) throw new Error('Failed to fetch connector config and schema');
        return {
            config: response.data.config,
            schema: response.data.schema
        };
    }

    static async updateConnectorConfig(connectorName: string, config: any): Promise<any> {
        const response = await axios.put(`${BASE_URL}/config/${connectorName}`, {
            ...config,
            baseUrl: window.location.origin
        });
        if (!response.data) throw new Error('Failed to update connector config');
        return response.data.config;
    }

    static async toggleConnector(connectorName: string): Promise<boolean> {
        const response = await axios.post(`${BASE_URL}/toggle/${connectorName}`);
        if (!response.data) throw new Error('Failed to toggle connector');
        return response.data.success;
    }

    static async getOAuthAuthorizationUrl(connectorName: string): Promise<{ authorizationUrl: string; state: string }> {
        const baseUrl = window.location.origin;
        const response = await axios.get(`${BASE_URL}/${connectorName}/oauth/authorize`, {
            params: {
                baseUrl
            }
        });
        if (!response.data) throw new Error('Failed to get OAuth authorization URL');
        return {
            authorizationUrl: response.data.authorizationUrl,
            state: response.data.state
        };
    }

    static async handleOAuthCallback(connectorName: string, code: string, state: string): Promise<{ filterOptions: any }> {
        const response = await axios.post(`${BASE_URL}/${connectorName}/oauth/callback`, {
            code,
            state,
            baseUrl: window.location.origin
        });
        if (!response.data) throw new Error('Failed to handle OAuth callback');
        return {
            filterOptions: response.data.filterOptions
        };
    }

    static async getConnectorFilterOptions(connectorName: string): Promise<{ filterOptions: any }> {
        const response = await axios.get(`${BASE_URL}/${connectorName}/filters`);
        if (!response.data) throw new Error('Failed to get connector filter options');
        return response.data;
    }

    static async saveConnectorFilters(connectorName: string, filters: any): Promise<any> {
        const response = await axios.post(`${BASE_URL}/${connectorName}/filters`, {
            filters,
            baseUrl: window.location.origin
        });
        if (!response.data) throw new Error('Failed to save connector filters');
        return response.data;
    }

}