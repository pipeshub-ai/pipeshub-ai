import axios from "src/utils/axios";
import { Connector, ConnectorConfig } from "../types/types";

const BASE_URL = 'http://localhost:8088/api/v1/connectors';

export class ConnectorApiService {

    static async getConnectors(): Promise<Connector[]> {
        const response = await axios.get(`${BASE_URL}`);
        if (!response.data) throw new Error('Failed to fetch connectors');
        return response.data.connectors;
    }

    static async getConnectorConfig(connectorName: string): Promise<ConnectorConfig> {
        const response = await axios.get(`${BASE_URL}/config/${connectorName}`);
        if (!response.data) throw new Error('Failed to fetch connector config');
        return response.data.config;
    }

    static async updateConnectorConfig(connectorName: string, config: any): Promise<any> {
        const response = await axios.put(`${BASE_URL}/config/${connectorName}`, config);
        if (!response.data) throw new Error('Failed to update connector config');
        return response.data.config;
    }

    static async toggleConnector(connectorName: string): Promise<boolean> {
        const response = await axios.post(`${BASE_URL}/toggle/${connectorName}`);
        if (!response.data) throw new Error('Failed to toggle connector');
        return response.data.success;
    }

}