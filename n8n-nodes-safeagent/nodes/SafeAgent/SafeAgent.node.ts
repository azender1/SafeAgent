import {
  IDataObject,
  IExecuteFunctions,
  INodeExecutionData,
  INodeType,
  INodeTypeDescription,
  NodeOperationError,
} from 'n8n-workflow';

// -----------------------------------------------------------------------------
// This node talks HTTP-only to the SafeAgent API (POST /claim/test, POST
// /settle/{request_id}). It intentionally only wires up the FREE test endpoint
// (rate-limited to 10 calls per IP address, total) rather than the paid POST
// /claim endpoint.
//
// Why: paid /claim is gated by genuine on-chain x402 - each call requires a
// fresh EIP-3009-signed USDC payment authorization from an EVM wallet, not a
// reusable API key. Bundling a signing library (viem/ethers) to do that inside
// this package would add a real runtime dependency, which disqualifies a
// community node from n8n Cloud verification (verified nodes must not have
// any runtime dependencies) - the exact problem removing better-sqlite3 was
// meant to solve. So paid, production usage is intentionally left to direct
// HTTP/Python/MCP integration outside n8n; see https://github.com/azender1/SafeAgent.
// -----------------------------------------------------------------------------

const DEFAULT_BASE_URL = 'https://safeagent-production.up.railway.app';

interface ClaimTestResponse {
  status: string;
  request_id: string;
  test: boolean;
  calls_remaining: number;
  existing?: IDataObject;
}

interface SettleResponse {
  status: string;
  request_id: string;
}

interface HttpLikeError extends Error {
  response?: { statusCode?: number };
  statusCode?: number;
}

export class SafeAgent implements INodeType {
  description: INodeTypeDescription = {
    usableAsTool: true,
    displayName: 'SafeAgent Execution Guard',
    name: 'safeAgent',
    icon: 'file:safeagent.svg',
    group: ['transform'],
    version: 2,
    subtitle: '={{$parameter["operation"]}}',
    description:
      'Exactly-once execution guard backed by the free SafeAgent test API ' +
      '(POST /claim/test - limited to 10 calls per IP address, total). Claims ' +
      'an (Agent ID, Action Type, Scope) slot before running a side-effectful ' +
      'action, then routes to Proceed (new) or Skip (duplicate). Call Settle ' +
      'after the action completes to record the result. For unlimited, paid ' +
      "production usage, call SafeAgent's POST /claim endpoint directly outside " +
      'n8n (see github.com/azender1/SafeAgent) - this node only wires up the ' +
      'free tier so the package has no payment/wallet runtime dependency.',
    defaults: { name: 'SafeAgent Guard' },
    inputs: ['main'],
    outputs: ['main', 'main'],
    outputNames: ['Proceed', 'Skip'],
    properties: [
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
              'Atomically claim an (Agent ID, Action Type, Scope) slot via the free ' +
              'test API. Routes to Proceed if new, Skip if already seen.',
            action: 'Claim an execution slot',
          },
          {
            name: 'Settle',
            value: 'settle',
            description:
              'Mark a previously claimed request as committed, with its result',
            action: 'Settle a claimed execution',
          },
        ],
        default: 'claim',
      },
      {
        displayName: 'Base URL',
        name: 'baseUrl',
        type: 'string',
        default: DEFAULT_BASE_URL,
        description:
          'SafeAgent API base URL. Override only if you are running a self-hosted instance.',
      },
      {
        displayName: 'Agent ID',
        name: 'agentId',
        type: 'string',
        default: '',
        required: true,
        placeholder: 'my-agent',
        description: 'Identifier for the agent or workflow performing the action',
        displayOptions: { show: { operation: ['claim'] } },
      },
      {
        displayName: 'Action Type',
        name: 'actionType',
        type: 'string',
        default: '',
        required: true,
        placeholder: 'send_payment',
        description: 'Short label for the side-effectful action being guarded',
        displayOptions: { show: { operation: ['claim'] } },
      },
      {
        displayName: 'Scope',
        name: 'scope',
        type: 'string',
        default: '',
        required: true,
        placeholder: 'customer:123',
        description:
          'Everything that makes this execution unique (e.g. customer ID, order ID, ' +
          'timestamp/bar). Combined with Agent ID and Action Type server-side into a ' +
          'content-addressed request ID.',
        displayOptions: { show: { operation: ['claim'] } },
      },
      {
        displayName: 'Request ID',
        name: 'requestId',
        type: 'string',
        default: '',
        required: true,
        placeholder: '={{ $json["request_id"] }}',
        description: 'The request_id returned by a previous Claim call',
        displayOptions: { show: { operation: ['settle'] } },
      },
      {
        displayName: 'Result',
        name: 'result',
        type: 'json',
        default: '{}',
        description: 'Arbitrary JSON result to store against this claim once settled',
        displayOptions: { show: { operation: ['settle'] } },
      },
    ],
  };

  async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
    const items = this.getInputData();
    const proceedItems: INodeExecutionData[] = [];
    const skipItems: INodeExecutionData[] = [];

    for (let i = 0; i < items.length; i++) {
      const operation = this.getNodeParameter('operation', i) as string;
      const rawBaseUrl = (this.getNodeParameter('baseUrl', i) as string) || DEFAULT_BASE_URL;
      const baseUrl = rawBaseUrl.replace(/\/+$/, '');

      try {
        if (operation === 'claim') {
          const agentId = (this.getNodeParameter('agentId', i) as string).trim();
          const actionType = (this.getNodeParameter('actionType', i) as string).trim();
          const scope = (this.getNodeParameter('scope', i) as string).trim();

          if (!agentId) {
            throw new NodeOperationError(this.getNode(), 'Agent ID must not be empty.', {
              itemIndex: i,
            });
          }
          if (!actionType) {
            throw new NodeOperationError(this.getNode(), 'Action Type must not be empty.', {
              itemIndex: i,
            });
          }
          if (!scope) {
            throw new NodeOperationError(this.getNode(), 'Scope must not be empty.', {
              itemIndex: i,
            });
          }

          const response = (await this.helpers.httpRequest({
            method: 'POST',
            url: baseUrl + '/claim/test',
            body: { agent_id: agentId, action_type: actionType, scope },
            json: true,
          })) as ClaimTestResponse;

          const outItem: INodeExecutionData = {
            json: { ...response },
            pairedItem: { item: i },
          };

          if (response.status === 'PROCEED') {
            proceedItems.push(outItem);
          } else {
            skipItems.push(outItem);
          }
        } else {
          const requestId = (this.getNodeParameter('requestId', i) as string).trim();
          const resultRaw = this.getNodeParameter('result', i) as string | object;

          if (!requestId) {
            throw new NodeOperationError(this.getNode(), 'Request ID must not be empty.', {
              itemIndex: i,
            });
          }

          let result: unknown = resultRaw;
          if (typeof resultRaw === 'string') {
            try {
              result = resultRaw.trim() ? JSON.parse(resultRaw) : {};
            } catch (parseError) {
              throw new NodeOperationError(
                this.getNode(),
                'Result must be valid JSON: ' + (parseError as Error).message,
                { itemIndex: i },
              );
            }
          }

          const response = (await this.helpers.httpRequest({
            method: 'POST',
            url: baseUrl + '/settle/' + encodeURIComponent(requestId),
            body: { result },
            json: true,
          })) as SettleResponse;

          proceedItems.push({
            json: { ...response },
            pairedItem: { item: i },
          });
        }
      } catch (error) {
        if (error instanceof NodeOperationError) {
          if (this.continueOnFail()) {
            proceedItems.push({ json: { error: error.message }, pairedItem: { item: i } });
            continue;
          }
          throw error;
        }

        const statusCode =
          (error as HttpLikeError).response?.statusCode ?? (error as HttpLikeError).statusCode;

        if (statusCode === 429) {
          const friendly = new NodeOperationError(
            this.getNode(),
            'Free test quota exhausted: POST /claim/test is limited to 10 calls per IP ' +
              'address, total. This node only supports the free test endpoint - for ' +
              'unlimited production usage, call the paid POST /claim endpoint directly ' +
              'outside n8n.',
            { itemIndex: i },
          );
          if (this.continueOnFail()) {
            proceedItems.push({ json: { error: friendly.message }, pairedItem: { item: i } });
            continue;
          }
          throw friendly;
        }

        if (this.continueOnFail()) {
          proceedItems.push({ json: { error: (error as Error).message }, pairedItem: { item: i } });
          continue;
        }
        throw error;
      }
    }

    return [proceedItems, skipItems];
  }
}
