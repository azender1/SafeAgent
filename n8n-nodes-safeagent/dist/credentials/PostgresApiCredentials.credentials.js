"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.PostgresApiCredentials = void 0;
class PostgresApiCredentials {
    constructor() {
        this.name = 'postgresApiCredentials';
        this.displayName = 'SafeAgent Postgres Credentials';
        this.documentationUrl = 'https://github.com/your-org/n8n-nodes-safeagent#postgres-setup';
        this.properties = [
            {
                displayName: 'Host',
                name: 'host',
                type: 'string',
                default: 'localhost',
                placeholder: 'localhost',
                description: 'PostgreSQL server hostname',
            },
            {
                displayName: 'Port',
                name: 'port',
                type: 'number',
                default: 5432,
                description: 'PostgreSQL server port',
            },
            {
                displayName: 'Database',
                name: 'database',
                type: 'string',
                default: 'safeagent',
                description: 'Name of the database that holds the execution-guard table',
            },
            {
                displayName: 'User',
                name: 'user',
                type: 'string',
                default: '',
                description: 'Database user',
            },
            {
                displayName: 'Password',
                name: 'password',
                type: 'string',
                typeOptions: { password: true },
                default: '',
                description: 'Database password',
            },
            {
                displayName: 'SSL',
                name: 'ssl',
                type: 'boolean',
                default: false,
                description: 'Whether to connect using SSL/TLS',
            },
        ];
    }
}
exports.PostgresApiCredentials = PostgresApiCredentials;
//# sourceMappingURL=PostgresApiCredentials.credentials.js.map