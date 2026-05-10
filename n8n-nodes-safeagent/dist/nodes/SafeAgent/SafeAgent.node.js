"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.SafeAgent = void 0;
const n8n_workflow_1 = require("n8n-workflow");
const better_sqlite3_1 = __importDefault(require("better-sqlite3"));
const path = __importStar(require("path"));
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
function openDb(dbPath) {
    const resolved = path.isAbsolute(dbPath)
        ? dbPath
        : path.resolve(process.cwd(), dbPath);
    const db = new better_sqlite3_1.default(resolved);
    db.pragma('journal_mode = WAL');
    db.exec(TABLE_DDL);
    return db;
}
// ---------------------------------------------------------------------------
// claim()  — INSERT OR IGNORE; returns whether the row was newly inserted
// ---------------------------------------------------------------------------
function claim(db, requestId, action) {
    var _a;
    const insert = db.prepare(`INSERT OR IGNORE INTO execution_claims (request_id, action, status)
     VALUES (?, ?, 'OPEN')`);
    const result = insert.run(requestId, action);
    const inserted = result.changes === 1;
    if (inserted) {
        return { inserted: true, status: 'OPEN' };
    }
    // Row already existed — fetch current status for the caller
    const row = db
        .prepare(`SELECT status FROM execution_claims WHERE request_id = ? AND action = ?`)
        .get(requestId, action);
    return { inserted: false, status: (_a = row === null || row === void 0 ? void 0 : row.status) !== null && _a !== void 0 ? _a : 'UNKNOWN' };
}
// ---------------------------------------------------------------------------
// settle()  — mark SETTLED after the action completed successfully
// ---------------------------------------------------------------------------
function settle(db, requestId, action) {
    const update = db.prepare(`UPDATE execution_claims
     SET status = 'SETTLED', updated_at = unixepoch()
     WHERE request_id = ? AND action = ?`);
    const result = update.run(requestId, action);
    return { settled: result.changes > 0 };
}
// ---------------------------------------------------------------------------
// Node definition
// ---------------------------------------------------------------------------
class SafeAgent {
    constructor() {
        this.description = {
            displayName: 'SafeAgent Execution Guard',
            name: 'safeAgent',
            icon: 'fa:shield-alt',
            group: ['transform'],
            version: 1,
            subtitle: '={{$parameter["operation"] + ": " + $parameter["action"]}}',
            description: 'Exactly-once execution guard. Claims a (request_id, action) slot before running ' +
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
                            description: 'Atomically claim the (Request ID, Action) pair. ' +
                                'Routes to Proceed if new, Skip if already seen.',
                        },
                        {
                            name: 'Settle',
                            value: 'settle',
                            description: 'Mark a previously claimed pair as SETTLED once the action has completed successfully.',
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
                    description: 'Unique identifier for this logical request ' +
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
                    description: 'Short label for the side-effectful action being guarded ' +
                        '(e.g. "send_email", "charge_card", "place_trade").',
                },
                // ── Database path ────────────────────────────────────────────────────
                {
                    displayName: 'Database Path',
                    name: 'dbPath',
                    type: 'string',
                    default: 'safeagent.db',
                    description: 'Path to the SQLite database file. ' +
                        'Relative paths are resolved from the n8n working directory.',
                },
            ],
        };
    }
    // ── Execute ──────────────────────────────────────────────────────────────
    async execute() {
        const items = this.getInputData();
        const proceedItems = [];
        const skipItems = [];
        for (let i = 0; i < items.length; i++) {
            const operation = this.getNodeParameter('operation', i);
            const requestId = this.getNodeParameter('requestId', i).trim();
            const action = this.getNodeParameter('action', i).trim();
            const dbPath = this.getNodeParameter('dbPath', i) || 'safeagent.db';
            if (!requestId) {
                throw new n8n_workflow_1.NodeOperationError(this.getNode(), 'Request ID must not be empty.', {
                    itemIndex: i,
                });
            }
            if (!action) {
                throw new n8n_workflow_1.NodeOperationError(this.getNode(), 'Action must not be empty.', {
                    itemIndex: i,
                });
            }
            const db = openDb(dbPath);
            try {
                if (operation === 'claim') {
                    const { inserted, status } = claim(db, requestId, action);
                    const outItem = {
                        json: { requestId, action, dbPath, inserted, status },
                        pairedItem: { item: i },
                    };
                    if (inserted) {
                        proceedItems.push(outItem);
                    }
                    else {
                        skipItems.push(outItem);
                    }
                }
                else {
                    // settle
                    const { settled } = settle(db, requestId, action);
                    proceedItems.push({
                        json: { requestId, action, dbPath, settled, status: settled ? 'SETTLED' : 'NOT_FOUND' },
                        pairedItem: { item: i },
                    });
                }
            }
            finally {
                db.close();
            }
        }
        return [proceedItems, skipItems];
    }
}
exports.SafeAgent = SafeAgent;
//# sourceMappingURL=SafeAgent.node.js.map