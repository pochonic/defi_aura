import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const DLMMModule = require('@meteora-ag/dlmm');
const DLMM = DLMMModule.default || DLMMModule;
const { Connection, PublicKey } = require('@solana/web3.js');

const [poolAddress, rpcUrl, windowText] = process.argv.slice(2);
const window = Number(windowText || 100);
const connection = new Connection(rpcUrl, 'confirmed');
const pool = await DLMM.create(connection, new PublicKey(poolAddress));
const active = await pool.getActiveBin();
const around = await pool.getBinsAroundActiveBin(window, window);

const asString = (value) => value === undefined || value === null ? null : String(value);
const bins = (around.bins || []).map((bin) => ({
  binId: bin.binId,
  price: asString(bin.price),
  pricePerToken: asString(bin.pricePerToken),
  xAmount: asString(bin.xAmount),
  yAmount: asString(bin.yAmount),
  supply: asString(bin.supply),
}));
console.log(JSON.stringify({
  activeBin: { binId: active.binId, price: asString(active.price), pricePerToken: asString(active.pricePerToken) },
  bins,
  binStep: pool.lbPair.binStep,
}));
