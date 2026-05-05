import {
  ICredentialType,
  INodeProperties,
} from 'n8n-workflow';

export class PostgresApiCredentials implements ICredentialType {
  name = 'postgresApiCredentials';
  displayName = 'SafeAgent Postgres Credentials';
  documentationUrl = 'https://github.com/your-org/n8n-nodes-safeagent#postgres-setup';

  properties: INodeProperties[] = [
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
