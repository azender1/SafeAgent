import {
  IExecuteFunctions,
  INodeExecutionData,
  INodeType,
  INodeTypeDescription,
  NodeOperationError,
} from 'n8n-workflow';
import { execSync } from 'child_process';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Escape single-quotes so the value is safe to embed in a Python string literal. */
function pyStr(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

/**
 * Build the inline Python snippet for the SQLite backend.
 *
 * Uses safeagent_exec_guard.sqlite_store.SQLiteExecutionStore which exposes:
 *   insert_if_not_exists(request_id, action_name) -> dict
 *
 * The dict has at minimum:
 *   { "inserted": bool, "receipt": { ... } }
 */
function buildSQLiteScript(requestId: string, actionName: string, dbPath: string): string {
  const rid = pyStr(requestId);
  const act = pyStr(actionName);
  const db = pyStr(dbPath);

  return (
    'import json; ' +
    'from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore; ' +
    `store = SQLiteExecutionStore('${db}'); ` +
    'store.init_db(); ' +
    `result = store.insert_if_not_exists('${rid}', '${act}'); ` +
    'print(json.dumps(result))'
  );
}

/**
 * Build the inline Python snippet for the Postgres backend.
 *
 * Uses safeagent_exec_guard.pg_store.PostgresExecutionStore which accepts a
 * libpq DSN string.
 */
function buildPostgresScript(
  requestId: string,
  actionName: string,
  dsn: string,
): string {
  const rid = pyStr(requestId);
  const act = pyStr(actionName);
  const d = pyStr(dsn);

  return (
    'import json; ' +
    'from safeagent_exec_guard.pg_store import PostgresExecutionStore; ' +
    `store = PostgresExecutionStore('${d}'); ` +
    'store.init_db(); ' +
    `result = store.insert_if_not_exists('${rid}', '${act}'); ` +
    'print(json.dumps(result))'
  );
}

// ---------------------------------------------------------------------------
// Node definition
// ---------------------------------------------------------------------------

export class SafeAgent implements INodeType {
  description: INodeTypeDescription = {
    displayName: 'SafeAgent Execution Guard',
    name: 'safeAgent',
    // Built-in placeholder icon; replace with a custom SVG in nodes/SafeAgent/safeagent.svg
    icon: 'fa:shield-alt',
    group: ['transform'],
    version: 1,
    subtitle: '={{$parameter["actionName"]}}',
    description:
      'Claim-before-execute idempotency guard powered by the safeagent-exec-guard Python library. ' +
      'Returns a cached receipt when the (requestId, actionName) pair was already processed, ' +
      'or a "proceed" signal when it is new.',
    defaults: {
      name: 'SafeAgent Guard',
    },
    inputs: ['main'],
    outputs: ['main'],
    credentials: [
      {
        name: 'postgresApiCredentials',
        required: false,
        displayOptions: {
          show: {
            backend: ['postgres'],
          },
        },
      },
    ],
    properties: [
      // ── Request ID ────────────────────────────────────────────────────────
      {
        displayName: 'Request ID',
        name: 'requestId',
        type: 'string',
        default: '',
        required: true,
        placeholder: '={{ $json["requestId"] }}',
        description:
          'Unique identifier for this logical request (e.g. webhook event ID, message UUID). ' +
          'Used together with Action Name to form the idempotency key.',
      },
      // ── Action Name ───────────────────────────────────────────────────────
      {
        displayName: 'Action Name',
        name: 'actionName',
        type: 'string',
        default: '',
        required: true,
        placeholder: 'send_invoice',
        description:
          'Name of the side-effectful action being guarded (e.g. "send_invoice", "charge_card"). ' +
          'Combined with Request ID to create a unique execution claim.',
      },
      // ── Backend ───────────────────────────────────────────────────────────
      {
        displayName: 'Backend',
        name: 'backend',
        type: 'options',
        options: [
          {
            name: 'SQLite (local file)',
            value: 'sqlite',
            description: 'Lightweight; good for single-instance or development use',
          },
          {
            name: 'PostgreSQL',
            value: 'postgres',
            description: 'Distributed-safe; recommended for multi-worker / production deployments',
          },
        ],
        default: 'sqlite',
        description: 'Storage backend used by the execution-guard library',
      },
      // ── SQLite path (only shown when backend = sqlite) ────────────────────
      {
        displayName: 'SQLite Database Path',
        name: 'sqlitePath',
        type: 'string',
        default: 'safeagent.db',
        displayOptions: {
          show: {
            backend: ['sqlite'],
          },
        },
        description:
          'Filesystem path to the SQLite database file. ' +
          'Relative paths are resolved from the n8n working directory.',
      },
    ],
  };

  // ── Execute ──────────────────────────────────────────────────────────────

  async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
    const items = this.getInputData();
    const returnData: INodeExecutionData[] = [];

    for (let i = 0; i < items.length; i++) {
      const requestId = this.getNodeParameter('requestId', i) as string;
      const actionName = this.getNodeParameter('actionName', i) as string;
      const backend = this.getNodeParameter('backend', i) as 'sqlite' | 'postgres';

      if (!requestId.trim()) {
        throw new NodeOperationError(this.getNode(), 'Request ID must not be empty.', {
          itemIndex: i,
        });
      }
      if (!actionName.trim()) {
        throw new NodeOperationError(this.getNode(), 'Action Name must not be empty.', {
          itemIndex: i,
        });
      }

      let script: string;

      if (backend === 'sqlite') {
        const dbPath = this.getNodeParameter('sqlitePath', i) as string;
        script = buildSQLiteScript(requestId, actionName, dbPath || 'safeagent.db');
      } else {
        // Build a libpq DSN from the stored credentials
        const creds = await this.getCredentials('postgresApiCredentials');
        const host = creds.host as string;
        const port = creds.port as number;
        const database = creds.database as string;
        const user = creds.user as string;
        const password = creds.password as string;
        const ssl = creds.ssl as boolean;

        const sslMode = ssl ? 'require' : 'disable';
        const dsn = `postgresql://${encodeURIComponent(user)}:${encodeURIComponent(
          password,
        )}@${host}:${port}/${database}?sslmode=${sslMode}`;

        script = buildPostgresScript(requestId, actionName, dsn);
      }

      let rawOutput: string;
      try {
        rawOutput = execSync(`python3 -c "${script.replace(/"/g, '\\"')}"`, {
          encoding: 'utf8',
          timeout: 15_000,
        }).trim();
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : String(err);
        throw new NodeOperationError(
          this.getNode(),
          `safeagent-exec-guard subprocess failed: ${message}`,
          { itemIndex: i },
        );
      }

      let guardResult: Record<string, unknown>;
      try {
        guardResult = JSON.parse(rawOutput);
      } catch {
        throw new NodeOperationError(
          this.getNode(),
          `Unexpected output from safeagent-exec-guard (expected JSON): ${rawOutput}`,
          { itemIndex: i },
        );
      }

      // `inserted: true`  → new claim; caller should proceed with the action.
      // `inserted: false` → already executed; caller should skip and use receipt.
      const isDuplicate = guardResult.inserted === false;

      returnData.push({
        json: {
          requestId,
          actionName,
          backend,
          isDuplicate,
          shouldProceed: !isDuplicate,
          ...guardResult,
        },
        pairedItem: { item: i },
      });
    }

    return [returnData];
  }
}
