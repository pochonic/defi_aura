"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";

type PoolDetail = {
  pair: string; protocol: string | null; pool_address: string; type: string | null;
  fee_tier: number | null; opportunity_score: number | null; risk_score: number | null;
  risk_coverage: number | null; asset_risk: number | null; asset_risk_coverage: number | null;
  volatility_risk: number | null; volatility_coverage: number | null; structure_risk: number | null; structure_coverage: number | null;
  apr: number | null; tvl_usd: number | null; volume_tvl_ratio: number | null;
  status: string | null; trend: string | null; snapshot_time: string | null; currently_eligible: boolean; current_eligibility: "ELIGIBLE" | "EXCLUDED" | "UNKNOWN"; eligibility_reason: string | null;
  hard_filter_failures: string[]; risk_modules: { asset: boolean; volatility: boolean; structure: boolean }; risk_modules_available: number; snapshot: Record<string, unknown>; score_breakdown: Record<string, unknown> | null;
  history: Array<Record<string, unknown>>;
};

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const value = (item: unknown) => item === null || item === undefined || item === "" ? "N/A" : String(item);
const visualPair = (pair: string) => pair.replace(/\bWSOL\b/gi, "SOL");
const numberValue = (item: unknown, digits = 2) => typeof item === "number" ? item.toFixed(digits) : "N/A";
const money = (item: unknown) => typeof item === "number" ? `$${item.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "N/A";
const pct = (item: unknown) => typeof item === "number" ? `${item.toFixed(1)}%` : "N/A";
const dappUrl = (protocol: string | null) => protocol === "Raydium" ? "https://raydium.io" : protocol === "Orca" ? "https://www.orca.so" : protocol === "Meteora" ? "https://app.meteora.ag" : null;
const age = (timestamp: string | null) => { if (!timestamp) return "N/A"; const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(timestamp)) / 1000)); if (!Number.isFinite(seconds)) return "N/A"; if (seconds < 60) return `${seconds}s`; if (seconds < 3600) return `${Math.floor(seconds / 60)}m`; return `${Math.floor(seconds / 3600)}h`; };
type HistoryTab = "fee" | "tvl" | "ratio";
const historyMetric = (item: Record<string, unknown>, tab: HistoryTab) => tab === "fee" ? item.calculated_fee_apr : tab === "tvl" ? item.tvl_usd : item.volume_tvl_ratio;
const chartPath = (history: Array<Record<string, unknown>>, tab: HistoryTab) => { const values = history.map((item) => historyMetric(item, tab)).filter((item): item is number => typeof item === "number"); if (values.length < 2) return null; const min = Math.min(...values); const max = Math.max(...values); const span = max - min || 1; return history.map((item, index) => { const itemValue = historyMetric(item, tab); if (typeof itemValue !== "number") return null; const x = index / (history.length - 1) * 780 + 10; const y = 200 - ((itemValue - min) / span * 180 + 10); return `${x.toFixed(1)},${y.toFixed(1)}`; }).filter(Boolean).join(" "); };

export default function PoolPage({ params }: { params: Promise<{ address: string }> }) {
  const { address } = use(params);
  const [pool, setPool] = useState<PoolDetail | null>(null);
  const [historyTab, setHistoryTab] = useState<HistoryTab>("fee");
  const [showRaw, setShowRaw] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetch(`${API}/api/pools/${encodeURIComponent(address)}`).then((response) => {
      if (!response.ok) throw new Error(`API ${response.status}`);
      return response.json() as Promise<PoolDetail>;
    }).then(setPool).catch((reason: Error) => setError(reason.message));
  }, [address]);

  return <main className="shell">
    <Link className="back" href="/">&lt;- Back to radar</Link>
    {error && <p className="error">Pool unavailable: {error}</p>}
    {!pool && !error && <p className="empty">Loading persisted pool data...</p>}
    {pool && <>
      <header><p className="eyebrow">POOL DETAIL</p><h1>{visualPair(pool.pair)}</h1><p className="subtitle">{value(pool.protocol)} · {value(pool.type)}</p><div className="external-actions"><a href={`https://solscan.io/account/${encodeURIComponent(pool.pool_address)}`} target="_blank" rel="noreferrer">View on Explorer</a>{dappUrl(pool.protocol) && <a href={dappUrl(pool.protocol)!} target="_blank" rel="noreferrer">Open {pool.protocol} DApp</a>}</div></header>
      <section className="detail-section"><h2>Overview</h2><div className="card detail-grid">
        <div><span>Opportunity</span><strong>{numberValue(pool.opportunity_score, 1)}</strong></div><div><span>Fee APR</span><strong>{pool.apr === null ? "N/A" : `${pool.apr.toFixed(2)}%`}</strong></div><div><span>TVL</span><strong>{money(pool.tvl_usd)}</strong></div><div><span>Volume/TVL</span><strong>{pool.volume_tvl_ratio === null ? "N/A" : `${pool.volume_tvl_ratio.toFixed(2)}x`}</strong></div><div><span>Status</span><strong>{value(pool.status)}</strong></div><div><span>Trend</span><strong>{value(pool.trend)}</strong></div>
      </div></section>
      <section className="detail-section"><h2>Current eligibility</h2><div className="card eligibility"><strong className={`eligibility-${(pool.current_eligibility ?? "UNKNOWN").toLowerCase()}`}>{pool.current_eligibility ?? "UNKNOWN"}</strong>{pool.current_eligibility !== "ELIGIBLE" && <span>Reason: {value(pool.eligibility_reason ?? (pool.hard_filter_failures?.length ? pool.hard_filter_failures.join(", ") : null))}</span>}<span>Snapshot age: {age(pool.snapshot_time)}</span></div></section>
      <section className="detail-section"><h2>Opportunity breakdown</h2><div className="card detail-grid">
        {(["fee_apr", "volume_tvl", "tvl", "persistence", "organic_yield"] as const).map((key) => { const labels = { fee_apr: "Fee APR", volume_tvl: "Volume efficiency", tvl: "TVL", persistence: "Persistence", organic_yield: "Organic yield" }; const components = (pool.score_breakdown?.components ?? {}) as Record<string, unknown>; const weights = (pool.score_breakdown?.effective_weights ?? {}) as Record<string, unknown>; const raw = components[key]; return <div key={key}><span>{labels[key]}</span><strong>{typeof raw === "number" ? `Score: ${raw.toFixed(2)} / 100 · Weight: ${typeof weights[key] === "number" ? `${(weights[key] as number * 100).toFixed(1)}%` : "N/A"}` : key === "persistence" ? "Not enough history" : "N/A"}</strong></div>; })}
      </div></section>
      <section className="detail-section"><h2>Risk</h2><div className="card detail-grid">
        <div><span>Risk Data</span><strong>{pool.risk_modules_available ?? 0} / 3 available</strong></div><div><span>Asset Risk</span><strong>{pool.risk_modules?.asset ? `${numberValue(pool.asset_risk, 1)} · coverage ${pct(pool.asset_risk_coverage)}` : "-"}</strong></div><div><span>Volatility Risk</span><strong>{pool.risk_modules?.volatility ? `${numberValue(pool.volatility_risk, 1)} · coverage ${pct(pool.volatility_coverage)}` : "-"}</strong></div><div><span>Structure Risk</span><strong>{pool.risk_modules?.structure ? `${numberValue(pool.structure_risk, 1)} · coverage ${pct(pool.structure_coverage)}` : "-"}</strong></div>
      </div></section>
      <section className="detail-section"><h2>History</h2><div className="card history-empty">{pool.history.length > 1 ? <><div className="history-tabs">{([['fee', 'Fee APR'], ['tvl', 'TVL'], ['ratio', 'Volume/TVL']] as [HistoryTab, string][]).map(([key, label]) => <button className={historyTab === key ? "tab active" : "tab"} onClick={() => setHistoryTab(key)} key={key}>{label}</button>)}</div><svg className="history-chart" viewBox="0 0 800 220" role="img" aria-label="Persisted pool history chart"><polyline fill="none" stroke="var(--accent)" strokeWidth="3" points={chartPath(pool.history, historyTab) ?? ""} /></svg><button className="raw-toggle" onClick={() => setShowRaw(!showRaw)}>{showRaw ? "Hide raw snapshots" : "View raw snapshots"}</button>{showRaw && <div className="history-table-wrap"><table><thead><tr><th>Snapshot</th><th>Fee APR</th><th>TVL</th><th>Volume/TVL</th></tr></thead><tbody>{pool.history.map((item, index) => <tr key={`${String(item.snapshot_time)}-${index}`}><td>{value(item.snapshot_time)}</td><td className="numeric">{typeof item.calculated_fee_apr === "number" ? `${item.calculated_fee_apr.toFixed(2)}%` : "N/A"}</td><td className="numeric">{money(item.tvl_usd)}</td><td className="numeric">{typeof item.volume_tvl_ratio === "number" ? `${item.volume_tvl_ratio.toFixed(2)}x` : "N/A"}</td></tr>)}</tbody></table></div>}</> : "Historical series not available yet"}</div></section>
      <section className="detail-section"><h2>Pool metadata</h2><div className="card detail-grid"><div><span>Full address</span><strong>{pool.pool_address}</strong></div><div><span>Protocol</span><strong>{value(pool.protocol)}</strong></div><div><span>Pool type</span><strong>{value(pool.type)}</strong></div><div><span>Fee tier</span><strong>{pool.fee_tier === null ? "N/A" : `${(pool.fee_tier * 100).toFixed(2)}%`}</strong></div><div><span>Snapshot timestamp</span><strong>{value(pool.snapshot_time)}</strong></div></div></section>
    </>}
  </main>;
}
