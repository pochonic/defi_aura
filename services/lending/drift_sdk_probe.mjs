import { Connection, Keypair, PublicKey } from '@solana/web3.js';
import {
  BulkAccountLoader,
  DriftClient,
  SpotBalanceType,
  Wallet,
  calculateBorrowRate,
  calculateDepositRate,
  calculateUtilization,
  getTokenAmount,
  initialize,
} from '@drift-labs/sdk';

const [rpcUrl, env = 'mainnet-beta'] = process.argv.slice(2);
if (!rpcUrl) throw new Error('rpcUrl is required');

const connection = new Connection(rpcUrl, 'confirmed');
const sdkConfig = initialize({ env });
const wallet = new Wallet(Keypair.generate());
const loader = new BulkAccountLoader(connection, 'confirmed', 1000);
const client = new DriftClient({
  connection,
  wallet,
  programID: new PublicKey(sdkConfig.DRIFT_PROGRAM_ID),
  env,
  accountSubscription: { type: 'polling', accountLoader: loader },
});

const bnNumber = (value, precision) => value == null ? null : Number(value.toString()) / precision;
const nameOf = (value) => {
  if (value == null) return null;
  if (Buffer.isBuffer(value)) return value.toString('utf8').replace(/\0/g, '').trim();
  if (Array.isArray(value) || ArrayBuffer.isView(value)) {
    return Buffer.from(value).toString('utf8').replace(/\0/g, '').trim();
  }
  return String(value).replace(/\0/g, '').trim();
};

await client.subscribe();
const rows = [];
for (const market of client.getSpotMarketAccounts()) {
  const index = Number(market.marketIndex);
  const decimals = Number(market.decimals);
  const precision = 10 ** decimals;
  const deposits = getTokenAmount(market.depositBalance, market, SpotBalanceType.DEPOSIT);
  const borrows = getTokenAmount(market.borrowBalance, market, SpotBalanceType.BORROW);
  const supplied = bnNumber(deposits, precision);
  const borrowed = bnNumber(borrows, precision);
  const utilization = bnNumber(calculateUtilization(market), 1e6);
  const supplyRate = bnNumber(calculateDepositRate(market), 1e6);
  const borrowRate = bnNumber(calculateBorrowRate(market), 1e6);
  const oracle = client.getOracleDataForSpotMarket(index);
  const oraclePrice = oracle?.price == null ? null : bnNumber(oracle.price, 1e6);
  const available = supplied == null || borrowed == null ? null : Math.max(0, supplied - borrowed);
  rows.push({
    market_id: String(index), reserve_id: market.mint?.toString?.() || String(index),
    asset_symbol: nameOf(market.name), asset_mint: market.mint?.toString?.() || null,
    market_name: nameOf(market.name), decimals, total_supplied_native: supplied,
    total_borrowed_native: borrowed, available_amount_native: available,
    utilization, supply_apy: supplyRate, borrow_apy: borrowRate,
    oracle_price_usd: oraclePrice,
    source_metadata: { market_index: index, oracle: market.oracle?.toString?.() || null },
  });
}
await client.unsubscribe();
console.log(JSON.stringify(rows));
