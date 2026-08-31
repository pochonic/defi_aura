export type OpportunityEntity = {
  id: string;
  entity_type: "LP" | "LENDING" | "STABLECOIN" | "VAULT" | "STAKING";
  chain: string;
  protocol: string | null;
  display_name: string;
  assets: string[];
  opportunity_score: number | null;
  risk: number | null;
  yield: number | null;
  status: string | null;
  trend: string | null;
  data_quality: string | null;
};

export type ChainMetadata = { id: string; name: string; native_asset: string; explorer_url: string; enabled: boolean };

export const CHAINS: ChainMetadata[] = [
  { id: "solana", name: "Solana", native_asset: "SOL", explorer_url: "https://solscan.io", enabled: true },
  { id: "ethereum", name: "Ethereum", native_asset: "ETH", explorer_url: "https://etherscan.io", enabled: false },
  { id: "base", name: "Base", native_asset: "ETH", explorer_url: "https://basescan.org", enabled: false },
  { id: "arbitrum", name: "Arbitrum", native_asset: "ETH", explorer_url: "https://arbiscan.io", enabled: false },
];
