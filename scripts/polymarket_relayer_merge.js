#!/usr/bin/env node

const { privateKeyToAccount } = require('viem/accounts');
const { createWalletClient, encodeFunctionData, http } = require('viem');
const { polygon } = require('viem/chains');
const { RelayClient } = require('@polymarket/builder-relayer-client');
const { BuilderConfig } = require('@polymarket/builder-signing-sdk');

const CTF_ADDRESS = '0x4D97DCd97eC945f40cF65F87097ACe5EA0476045';
const USDC_ADDRESS = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174';
const HASH_ZERO = `0x${'00'.repeat(32)}`;
const MERGE_ABI = [
  {
    name: 'mergePositions',
    type: 'function',
    stateMutability: 'nonpayable',
    inputs: [
      { name: 'collateralToken', type: 'address' },
      { name: 'parentCollectionId', type: 'bytes32' },
      { name: 'conditionId', type: 'bytes32' },
      { name: 'partition', type: 'uint256[]' },
      { name: 'amount', type: 'uint256' },
    ],
    outputs: [],
  },
];

function fail(message, extra) {
  const payload = { ok: false, error: message };
  if (extra !== undefined) {
    payload.details = extra;
  }
  process.stdout.write(JSON.stringify(payload));
  process.exit(1);
}

async function main() {
  const rawInput = process.argv[2];
  if (!rawInput) {
    fail('missing JSON payload argument');
  }

  let parsedPayload;
  try {
    parsedPayload = JSON.parse(rawInput);
  } catch (error) {
    fail('invalid JSON payload', String(error));
  }

  const privateKey = (parsedPayload.privateKey || process.env.PRIVATE_KEY || '').trim();
  const rpcUrl = (parsedPayload.rpcUrl || process.env.POLYGON_RPC_URL || 'https://polygon-rpc.com').trim();
  const relayerUrl = (parsedPayload.relayerUrl || process.env.POLY_RELAYER_URL || 'https://relayer-v2.polymarket.com/').trim();
  const builderApiKey = (parsedPayload.builderApiKey || process.env.POLY_BUILDER_API_KEY || '').trim();
  const builderSecret = (parsedPayload.builderSecret || process.env.POLY_BUILDER_SECRET || '').trim();
  const builderPassphrase = (parsedPayload.builderPassphrase || process.env.POLY_BUILDER_PASSPHRASE || '').trim();
  const description = (parsedPayload.description || 'Merge tokens to USDCe').trim();
  const rawConditionId = (parsedPayload.conditionId || '').trim();
  const rawAmount = parsedPayload.amountRaw;

  if (!privateKey) {
    fail('missing private key');
  }
  if (!builderApiKey || !builderSecret || !builderPassphrase) {
    fail('missing builder credentials');
  }
  if (!/^0x[0-9a-fA-F]{64}$/.test(rawConditionId)) {
    fail('conditionId must be a 0x-prefixed 32-byte hex string');
  }
  if (rawAmount === undefined || rawAmount === null) {
    fail('missing amountRaw');
  }

  let amountRawAsBigInt;
  try {
    amountRawAsBigInt = BigInt(String(rawAmount));
  } catch (error) {
    fail('amountRaw must be an integer-compatible value', String(error));
  }

  if (amountRawAsBigInt <= 0n) {
    fail('amountRaw must be greater than zero');
  }

  try {
    const normalizedPrivateKey = privateKey.startsWith('0x') ? privateKey : `0x${privateKey}`;
    const account = privateKeyToAccount(normalizedPrivateKey);
    const wallet = createWalletClient({
      account,
      chain: polygon,
      transport: http(rpcUrl),
    });

    const builderConfig = new BuilderConfig({
      localBuilderCreds: {
        key: builderApiKey,
        secret: builderSecret,
        passphrase: builderPassphrase,
      },
    });

    const client = new RelayClient(relayerUrl, 137, wallet, builderConfig);
    const mergeTx = {
      to: CTF_ADDRESS,
      data: encodeFunctionData({
        abi: MERGE_ABI,
        functionName: 'mergePositions',
        args: [USDC_ADDRESS, HASH_ZERO, rawConditionId, [1, 2], amountRawAsBigInt],
      }),
      value: '0',
    };

    const response = await client.execute([mergeTx], description);
    const waitedResult = response && typeof response.wait === 'function' ? await response.wait() : null;
    const output = {
      ok: true,
      response: response || null,
      result: waitedResult || null,
      transactionHash:
        (waitedResult && (waitedResult.transactionHash || waitedResult.hash)) ||
        (response && (response.transactionHash || response.hash)) ||
        null,
      proxyAddress: waitedResult && waitedResult.proxyAddress ? waitedResult.proxyAddress : null,
      signerAddress: account.address,
    };
    process.stdout.write(JSON.stringify(output));
  } catch (error) {
    fail('relayer execution failed', error && error.stack ? error.stack : String(error));
  }
}

main();
