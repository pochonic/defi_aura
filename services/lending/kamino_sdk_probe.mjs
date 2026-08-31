import { createSolanaRpc, address } from '@solana/kit';
import { KaminoMarket, DEFAULT_RECENT_SLOT_DURATION_MS } from '@kamino-finance/klend-sdk';

const [marketIdsJson, rpcUrl, debugFlag] = process.argv.slice(2);
const marketIds = JSON.parse(marketIdsJson || '[]');
if (!marketIds.length || !rpcUrl) throw new Error('marketIds and rpcUrl are required');

const rpc = createSolanaRpc(rpcUrl);
const maxAttempts = Number.parseInt(process.env.KAMINO_SDK_MAX_ATTEMPTS || '5', 10);
const baseBackoffMs = Number.parseInt(process.env.KAMINO_SDK_BACKOFF_MS || '1000', 10);

const isRateLimited = (error) => {
  const statusCode = error?.context?.statusCode ?? error?.statusCode;
  return statusCode === 429 || /429|too many requests/i.test(String(error?.message || ''));
};

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

let markets;
for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
  try {
    markets = await KaminoMarket.loadMultiple(
      rpc,
      marketIds.map(address),
      DEFAULT_RECENT_SLOT_DURATION_MS,
    );
    break;
  } catch (error) {
    if (!isRateLimited(error) || attempt === maxAttempts) throw error;

    const backoffMs = baseBackoffMs * (2 ** (attempt - 1));
    console.error(
      `Kamino SDK RPC rate limited; retrying in ${backoffMs}ms ` +
      `(attempt ${attempt + 1}/${maxAttempts})`,
    );
    await sleep(backoffMs);
  }
}

const rows = [...markets.entries()].flatMap(([loadedMarketId, market]) => market.getReserves().map((reserve) => {
  const liquidity = reserve.state?.liquidity;
  const row = {
    market_id: String(loadedMarketId),
    reserve_id: String(reserve.address),
    utilization: reserve.calculateUtilizationRatio(),
    available_amount_native: liquidity?.totalAvailableAmount == null ? null : String(liquidity.totalAvailableAmount),
    mint_decimals: liquidity?.mintDecimals == null ? null : Number(liquidity.mintDecimals),
    borrowed_amount_native: reserve.getBorrowedAmount().toString(),
    total_supply_native: reserve.getTotalSupply().toString(),
    source_type: 'derived', source: 'kamino_sdk',
    calculation_version: 'kamino_sdk.calculateUtilizationRatio.v1',
  };
  if (debugFlag === '--debug') row.debug = { account_fetch: 'success', reserve_decode: 'success', function: 'reserve.calculateUtilizationRatio()', state_fields: ['liquidity.totalAvailableAmount', 'liquidity.borrowedAmountSf', 'liquidity.accumulatedProtocolFeesSf', 'liquidity.accumulatedReferrerFeesSf', 'liquidity.pendingReferrerFeesSf'], state_values: { available_amount_native: row.available_amount_native, borrowed_amount_native: row.borrowed_amount_native, total_supply_native: row.total_supply_native } };
  return row;
}));
console.log(JSON.stringify(rows));
