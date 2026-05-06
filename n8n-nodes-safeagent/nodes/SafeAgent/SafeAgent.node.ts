import {
  IExecuteFunctions,
  INodeExecutionData,
  INodeType,
  INodeTypeDescription,
  NodeOperationError,
} from 'n8n-workflow';
import Database from 'better-sqlite3';
import * as path from 'path';

// ---------------------------------------------------------------------------
// Database helpers
// ---------------------------------------------------------------------------

const TABLE_DDL = `
  CREATE TABLE IF NOT EXISTS execution_claims (
    request_id TEXT NOT NULL,
    action     TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'OPEN',
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (request_id, action)
  )
`;

function openDb(dbPath: string): Database.Database {
  const resolved = path.isAbsolute(dbPath)
    ? dbPath
    : path.resolve(process.cwd(), dbPath);
  const db = new Database(resolved);
  db.pragma('journal_mode = WAL');
  db.exec(TABLE_DDL);
  return db;
}

interface ClaimRow {
  status: string;
}

// ---------------------------------------------------------------------------
// claim()  — INSERT OR IGNORE; returns whether the row was newly inserted
// ---------------------------------------------------------------------------

function claim(
  db: Database.Database,
  requestId: string,
  action: string,
): { inserted: boolean; status: string } {
  const insert = db.prepare<[string, string]>(
    `INSERT OR IGNORE INTO execution_claims (request_id, action, status)
     VALUES (?, ?, 'OPEN')`,
  );
  const result = insert.run(requestId, action);
  const inserted = result.changes === 1;

  if (inserted) {
    return { inserted: true, status: 'OPEN' };
  }

  // Row already existed — fetch current status for the caller
  const row = db
    .prepare<[string, string], ClaimRow>(
      `SELECT status FROM execution_claims WHERE request_id = ? AND action = ?`,
    )
    .get(requestId, action);

  return { inserted: false, status: row?.status ?? 'UNKNOWN' };
}

// ---------------------------------------------------------------------------
// settle()  — mark SETTLED after the action completed successfully
// ---------------------------------------------------------------------------

function settle(
  db: Database.Database,
  requestId: string,
  action: string,
): { settled: boolean } {
  const update = db.prepare<[string, string]>(
    `UPDATE execution_claims
     SET status = 'SETTLED', updated_at = unixepoch()
     WHERE request_id = ? AND action = ?`,
  );
  const result = update.run(requestId, action);
  return { settled: result.changes > 0 };
}

// ---------------------------------------------------------------------------
// Node definition
// ---------------------------------------------------------------------------

export class SafeAgent implements INodeType {
  description: INodeTypeDescription = {
    displayName: 'SafeAgent Execution Guard',
    name: 'safeAgent',
    icon: 'fa:shield-alt',
    group: ['transform'],
    version: 1,
    subtitle: '={{$parameter["operation"] + ": " + $parameter["action"]}}',
    description:
      'Exactly-once execution guard. Claims a (request_id, action) slot before running ' +
      'a side-effectful action, then routes to Proceed (new) or Skip (duplicate). ' +
      'Call Settle after the action completes to mark the slot as done.',
    defaults: { name: 'SafeAgent Guard' },
    inputs: ['main'],
    // Two named outputs: index 0 = Proceed, index 1 = Skip.
    // Settle always emits on index 0.
    outputs: ['main', 'main'],
    outputNames: ['Proceed', 'Skip'],
    properties: [
      // ── Operation ────────────────────────────────────────────────────────
      {
        displayName: 'Operation',
        name: 'operation',
        type: 'options',
        noDataExpression: true,
        options: [
          {
            name: 'Claim',
            value: 'claim',
            description:
              'Atomically claim the (Request ID, Action) pair. ' +
              'Routes to Proceed if new, Skip if already seen.',
          },
          {
            name: 'Settle',
            value: 'settle',
            description:
              'Mark a previously claimed pair as SETTLED once the action has completed successfully.',
          },
        ],
        default: 'claim',
      },
      // ── Request ID ───────────────────────────────────────────────────────
      {
        displayName: 'Request ID',
        name: 'requestId',
        type: 'string',
        default: '',
        required: true,
        placeholder: '={{ $json["requestId"] }}',
        description:
          'Unique identifier for this logical request ' +
          '(e.g. webhook event ID, message UUID, idempotency key).',
      },
      // ── Action ───────────────────────────────────────────────────────────
      {
        displayName: 'Action',
        name: 'action',
        type: 'string',
        default: '',
        required: true,
        placeholder: 'send_email',
        description:
          'Short label for the side-effectful action being guarded ' +
          '(e.g. "send_email", "charge_card", "place_trade").',
      },
      // ── Database path ────────────────────────────────────────────────────
      {
        displayName: 'Database Path',
        name: 'dbPath',
        type: 'string',
        default: 'safeagent.db',
        description:
          'Path to the SQLite database file. ' +
          'Relative paths are resolved from the n8n working directory.',
      },
    ],
  };

  // ── Execute ──────────────────────────────────────────────────────────────

  async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
    const items = this.getInputData();
    const proceedItems: INodeExecutionData[] = [];
    const skipItems: INodeExecutionData[] = [];

    for (let i = 0; i < items.length; i++) {
      const operation = this.getNodeParameter('operation', i) as string;
      const requestId = (this.getNodeParameter('requestId', i) as string).trim();
      const action = (this.getNodeParameter('action', i) as string).trim();
      const dbPath = (this.getNodeParameter('dbPath', i) as string) || 'safeagent.db';

      if (!requestId) {
        throw new NodeOperationError(this.getNode(), 'Request ID must not be empty.', {
          itemIndex: i,
        });
      }
      if (!action) {
        throw new NodeOperationError(this.getNode(), 'Action must not be empty.', {
          itemIndex: i,
        });
      }

      const db = openDb(dbPath);
      try {
        if (operation === 'claim') {
          const { inserted, status } = claim(db, requestId, action);

          const outItem: INodeExecutionData = {
            json: { requestId, action, dbPath, inserted, status },
            pairedItem: { item: i },
          };

          if (inserted) {
            proceedItems.push(outItem);
          } else {
            skipItems.push(outItem);
          }
        } else {
          // settle
          const { settled } = settle(db, requestId, action);

          proceedItems.push({
            json: { requestId, action, dbPath, settled, status: settled ? 'SETTLED' : 'NOT_FOUND' },
            pairedItem: { item: i },
          });
        }
      } finally {
        db.close();
      }
    }

    return [proceedItems, skipItems];
  }
}
