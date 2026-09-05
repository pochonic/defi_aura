"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname } from "next/navigation";
import Navigation from "./components/navigation";

type Pool = { pair: string; protocol: string | null; pool_address: string | null; type: string | null; opportunity_score: number | null; risk_score: number | null; risk_coverage: number | null; asset_risk: number | null; asset_risk_coverage: number | null; volatility_risk: number | null; volatility_coverage: number | null; structure_risk: number | null; structure_coverage: number | null; apr: number | null; tvl_usd: number | null; volume_tvl_ratio: number | null; status: string | null; trend: string | null; snapshot_time: string | null; currently_eligible: boolean; hard_filter_failures: string[]; risk_modules_available?: number; evaluation_reason?: string | null; };
type PoolResponse = { items: Pool[]; total: number; limit: number; offset: number };
type ProviderHealth = { protocol: string; status: string; checked_at: string; error: string };
type Tab = "top" | "persistent" | "rising" | "dropped" | "all";
type Evaluation = "evaluated" | "incomplete" | "all";
type SortKey = "opportunity_score" | "apr" | "tvl_usd" | "volume_tvl_ratio" | "asset_risk" | "volatility_risk" | "structure_risk" | "status" | "trend";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const display = (value: unknown) => value === null || value === undefined || value === "" ? "N/A" : String(value);
const money = (value: number | null | undefined) => typeof value !== "number" ? "N/A" : `$${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const compactMoney = (value: number | null | undefined) => {
  if (typeof value !== "number") return "N/A";
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
};
const score = (value: number | null | undefined) => typeof value !== "number" ? "N/A" : value.toFixed(1);
const visualPair = (pair: string) => pair.replace(/\bWSOL\b/gi, "SOL");
const shortPoolType = (type: string | null) => type === "AMM/OpenBookMarket" ? "AMM" : type;
const protocolLogo = (protocol: string | null) => protocol === "Raydium" ? "https://raydium.io/favicon.ico" : protocol === "Orca" ? "https://www.orca.so/favicon.ico" : protocol === "Meteora" ? "https://app.meteora.ag/favicon.ico" : null;
const statusClass = (status: string | null) => (status ?? "na").toLowerCase().replaceAll("_", "-");
const dataAge = (value: string | undefined) => {
  if (!value) return "N/A";
  const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(value)) / 1000));
  if (!Number.isFinite(seconds)) return "N/A";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h`;
};

function LpRadar() {
  const [data, setData] = useState<PoolResponse>({ items: [], total: 0, limit: 500, offset: 0 });
  const [health, setHealth] = useState<ProviderHealth[]>([]);
  const [protocol, setProtocol] = useState(""); const [pair, setPair] = useState(""); const [minOpportunity, setMinOpportunity] = useState("");
  const [tab, setTab] = useState<Tab>("top"); const [includeIncomplete, setIncludeIncomplete] = useState(false); const [evaluation, setEvaluation] = useState<Evaluation>("evaluated");
  const [sortKey, setSortKey] = useState<SortKey>("opportunity_score"); const [sortDescending, setSortDescending] = useState(true);
  const [error, setError] = useState<string | null>(null); const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const load = useCallback(async () => { try { const params = new URLSearchParams({ limit: "500" }); if (protocol) params.set("protocol", protocol); if (pair) params.set("pair", pair); if (minOpportunity) params.set("min_opportunity", minOpportunity); const [poolResponse, healthResponse] = await Promise.all([fetch(`${API}/api/pools?${params}`), fetch(`${API}/api/protocols/health`)]); if (!poolResponse.ok) throw new Error(`Pools API ${poolResponse.status}`); if (!healthResponse.ok) throw new Error(`Health API ${healthResponse.status}`); setData(await poolResponse.json() as PoolResponse); setHealth(await healthResponse.json() as ProviderHealth[]); setError(null); setLastRefresh(new Date()); } catch (reason) { setError(reason instanceof Error ? reason.message : "Unknown API error"); } }, [minOpportunity, pair, protocol]);
  useEffect(() => { void load(); const timer = setInterval(() => void load(), 30000); return () => clearInterval(timer); }, [load]);
  const filtered = useMemo(() => { let items = data.items; if (tab === "top") items = items.filter((pool) => pool.currently_eligible && pool.opportunity_score !== null); else if (tab === "persistent") items = items.filter((pool) => pool.currently_eligible && pool.status?.startsWith("PERSISTENT")); else if (tab === "rising") items = items.filter((pool) => pool.currently_eligible && pool.trend === "RISING"); else if (tab === "dropped") items = []; if (tab === "all") { if (evaluation === "evaluated") items = items.filter((pool) => pool.opportunity_score !== null); if (evaluation === "incomplete") items = items.filter((pool) => pool.opportunity_score === null); } else if (!includeIncomplete) items = items.filter((pool) => pool.opportunity_score !== null); items = [...items].sort((a, b) => { const left = a[sortKey]; const right = b[sortKey]; if (left === null || left === undefined) return 1; if (right === null || right === undefined) return -1; const result = typeof left === "number" && typeof right === "number" ? left - right : String(left).localeCompare(String(right)); return sortDescending ? -result : result; }); return tab === "top" ? items.slice(0, 15) : items; }, [data.items, evaluation, includeIncomplete, sortDescending, sortKey, tab]);
  const sortable: Record<string, SortKey> = { Opportunity: "opportunity_score", "Fee APR": "apr", TVL: "tvl_usd", "Vol/TVL": "volume_tvl_ratio", "Asset Risk": "asset_risk", Volatility: "volatility_risk", Structure: "structure_risk", Status: "status", Trend: "trend" };
  // Future API responses may provide evaluation_reason; keep the generic tooltip until then.
  const evaluationReason = (pool: Pool) => pool.evaluation_reason || "Insufficient data for evaluation";
  const setSort = (key: SortKey) => { if (sortKey === key) setSortDescending((value) => !value); else { setSortKey(key); setSortDescending(true); } };
  const opportunities = data.items.filter((pool) => pool.currently_eligible && pool.opportunity_score !== null).length;
  const persistent = data.items.filter((pool) => pool.status?.startsWith("PERSISTENT")).length;
  const liveProviders = health.filter((item) => item.status === "OK").length;
  return <main className="shell"><Navigation /><header className="hero"><div><p className="eyebrow">CRYPTO RADAR</p><h1>Crypto Radar</h1><p className="subtitle">Find DeFi opportunities. Understand the risk.</p><p className="scope">Solana · Raydium · Orca · Meteora</p></div><div className="refresh-meta"><span className="live-dot" /> Auto refresh 30s<br />{lastRefresh ? `Updated ${lastRefresh.toLocaleTimeString()}` : "Connecting..."}</div></header>
    <section className="snapshot"><div><span>Pools tracked</span><strong>{data.total}</strong></div><div><span>Opportunities</span><strong>{opportunities}</strong></div><div><span>Persistent &gt;24h</span><strong>{persistent}</strong></div><div><span>Protocols live</span><strong>{liveProviders} / 3</strong></div></section>
    <section className="health-strip"><div className="section-heading"><h2>Provider health</h2><span>Persisted status</span></div><div className="health-grid">{["Raydium", "Orca", "Meteora"].map((name) => { const item = health.find((entry) => entry.protocol === name); const state = item?.status === "OK" ? "LIVE" : item ? "UNAVAILABLE" : "N/A"; return <div className="health-item" key={name}><span>{name}</span><strong className={state === "LIVE" ? "ok" : "muted"}>{state}</strong><small>age: {dataAge(item?.checked_at)} · last update: {item?.checked_at ?? "N/A"}</small></div>; })}</div></section>
    <section className="card"><div className="section-heading"><div><h2>Opportunity radar</h2><p className="section-note">Persisted snapshots only. Scores are calculated by the engine.</p></div><div className="radar-actions"><span>{lastRefresh ? `Updated ${lastRefresh.toLocaleTimeString()}` : "Connecting..."}</span><button onClick={() => void load()}>Refresh</button></div></div>
      <nav className="tabs" aria-label="Pool views">{([["top", "Top Opportunities"], ["persistent", "Persistent"], ["rising", "Rising"], ["dropped", "Recently Dropped"], ["all", "All Pools"]] as [Tab, string][]).map(([key, label]) => <button className={tab === key ? "tab active" : "tab"} onClick={() => setTab(key)} key={key}>{label}</button>)}</nav>
      <div className="filters"><input aria-label="Pair" placeholder="Search pair" value={pair} onChange={(event) => setPair(event.target.value)} /><select aria-label="Protocol" value={protocol} onChange={(event) => setProtocol(event.target.value)}><option value="">All protocols</option><option>Raydium</option><option>Orca</option><option>Meteora</option></select><input aria-label="Minimum opportunity" type="number" min="0" max="100" placeholder="Min OPP" value={minOpportunity} onChange={(event) => setMinOpportunity(event.target.value)} />{tab === "all" ? <div className="evaluation-filter" aria-label="Evaluation"><span>Evaluation</span>{([['evaluated', 'Evaluated'], ['incomplete', 'Incomplete'], ['all', 'All']] as [Evaluation, string][]).map(([key, label]) => <button type="button" className={evaluation === key ? "evaluation-option active" : "evaluation-option"} onClick={() => setEvaluation(key)} key={key}>{label}</button>)}</div> : <label className="toggle"><input type="checkbox" checked={includeIncomplete} onChange={(event) => setIncludeIncomplete(event.target.checked)} /> Include incomplete</label>}</div>
      {error && <p className="error">API unavailable: {error}</p>}
      <div className="count-filter-row"><p className="section-note">Showing {filtered.length} of {data.items.length} pools</p>{tab === "all" && <span className="evaluation-summary">{data.items.filter((pool) => pool.opportunity_score !== null).length} evaluated · {data.items.filter((pool) => pool.opportunity_score === null).length} incomplete</span>}</div>
      <div className="table-wrap"><table><thead><tr>{["Pair", "Protocol", "Type", "Global Score", "Risk Data", "Asset Risk", "Volatility", "Structure", "Fee APR", "TVL", "Vol/TVL", "Status", "Trend"].map((heading) => <th key={heading}>{heading === "Global Score" ? <button className="sort-button" onClick={() => setSort("opportunity_score")}>{heading} {sortKey === "opportunity_score" ? (sortDescending ? "↓" : "↑") : "↕"}</button> : sortable[heading] ? <button className="sort-button" onClick={() => setSort(sortable[heading])}>{heading} {sortKey === sortable[heading] ? (sortDescending ? "↓" : "↑") : "↕"}</button> : heading}</th>)}</tr></thead><tbody>{filtered.map((pool, index) => <tr key={`${pool.protocol}-${pool.pool_address}-${index}`}><td><Link href={`/compare/${encodeURIComponent(visualPair(pool.pair).replaceAll(" / ", "-"))}`}>{visualPair(display(pool.pair))}</Link></td><td><Link href={`/pools/${encodeURIComponent(pool.pool_address ?? "")}`}>{display(pool.protocol)}</Link></td><td>{display(pool.type)}</td><td className="numeric">{pool.opportunity_score === null ? <span className="na-value" title={evaluationReason(pool)}>N/A</span> : score(pool.opportunity_score)}</td><td className="numeric">{typeof pool.risk_modules_available === "number" ? `${pool.risk_modules_available} / 3` : "N/A"}</td><td className="numeric">{score(pool.asset_risk)}</td><td className="numeric">{score(pool.volatility_risk)}</td><td className="numeric">{score(pool.structure_risk)}</td><td className="numeric">{typeof pool.apr !== "number" ? "N/A" : `${pool.apr.toFixed(2)}%`}</td><td className="numeric">{money(pool.tvl_usd)}</td><td className="numeric">{typeof pool.volume_tvl_ratio !== "number" ? "N/A" : `${pool.volume_tvl_ratio.toFixed(2)}x`}</td><td><span className={`status-badge ${statusClass(pool.status)}`}>{display(pool.status)}</span></td><td>{display(pool.trend)}</td></tr>)}</tbody></table></div>
      {!error && filtered.length === 0 && <p className="empty">{tab === "dropped" ? "No recently dropped candidates are exposed by the current API." : "No persisted pools match this view."}</p>}
    </section></main>;
}

function Overview() {
  const [data, setData] = useState<PoolResponse>({ items: [], total: 0, limit: 500, offset: 0 });
  const [health, setHealth] = useState<ProviderHealth[]>([]);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const [poolResponse, healthResponse] = await Promise.all([fetch(`${API}/api/pools?limit=500`), fetch(`${API}/api/protocols/health`)]);
      if (!poolResponse.ok) throw new Error(`Pools API ${poolResponse.status}`);
      if (!healthResponse.ok) throw new Error(`Health API ${healthResponse.status}`);
      setData(await poolResponse.json() as PoolResponse);
      setHealth(await healthResponse.json() as ProviderHealth[]);
      setError(null);
      setLastRefresh(new Date());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unknown API error");
    }
  }, []);
  useEffect(() => { void load(); const timer = setInterval(() => void load(), 30000); return () => clearInterval(timer); }, [load]);

  const currentPools = useMemo(() => data.items.filter((pool) => pool.currently_eligible && pool.opportunity_score !== null).sort((a, b) => (b.opportunity_score ?? -1) - (a.opportunity_score ?? -1)), [data.items]);
  const topPools = currentPools.slice(0, 5);
  const mainPair = topPools[0] ? visualPair(topPools[0].pair) : null;
  const comparisonPools = mainPair ? currentPools.filter((pool) => visualPair(pool.pair) === mainPair) : [];
  const best = currentPools[0];
  const liveProviders = health.filter((item) => item.status === "OK").length;
  const providerState = (item: ProviderHealth | undefined) => item?.status === "OK" ? "LIVE" : item?.status === "STALE" ? "STALE" : item ? "UNAVAILABLE" : "N/A";

  return <main className="shell platform-shell"><Navigation /><header className="terminal-header"><div><p className="eyebrow brand-mark">D.E.F.I.</p><p className="brand-expansion">DeFi Ecosystem Financial Intelligence</p></div><span className="header-updated">{lastRefresh ? `Updated ${lastRefresh.toLocaleTimeString()}` : "Connecting..."}</span></header>
    <section className="market-intro"><p className="eyebrow">DEFI OPPORTUNITY INTELLIGENCE</p><h1>Market overview</h1><p className="subtitle">Find yield. Understand the risk.</p><p className="scope"><span className="live-dot" /> Solana · Raydium · Orca · Meteora</p></section>
    {error && <p className="error">API unavailable: {error}</p>}
    <section className="kpi-row"><div className="terminal-kpi"><span>Opportunities</span><strong>{currentPools.length}</strong><small>current eligible</small></div><div className="terminal-kpi"><span>Persistent &gt;24h</span><strong>{currentPools.filter((pool) => pool.status?.startsWith("PERSISTENT")).length}</strong><small>historically qualified</small></div><div className="terminal-kpi"><span>Protocols live</span><strong>{health.length ? `${liveProviders} / 3` : "N/A"}</strong><small>provider health</small></div><div className="terminal-kpi"><span>Best opportunity</span><strong className="accent-value">{score(best?.opportunity_score)}</strong><small>{best ? `${visualPair(best.pair)} · ${display(best.protocol)} · ${display(best.type)}` : "N/A"}</small></div></section>
    <div className="platform-status-line"><span><b>LIVE:</b> LP Intelligence</span><span><b>LIVE:</b> Lending</span><span><b>PLANNED:</b> Stablecoins</span></div>
    <section className="terminal-grid"><div className="card ranking-card"><div className="terminal-section-heading"><div><p className="eyebrow">LIVE RANKING</p><h2>Top Opportunities</h2></div><span>LP Intelligence</span></div><div className="ranking-table-wrap"><table className="ranking-table"><thead><tr><th>#</th><th>Pool</th><th>Protocol</th><th>APR</th><th>TVL</th><th className="numeric">Opportunity</th></tr></thead><tbody>{topPools.map((pool, index) => <tr key={pool.pool_address}><td>{String(index + 1).padStart(2, "0")}</td><td><Link href={`/pools/${encodeURIComponent(pool.pool_address ?? "")}`}>{visualPair(pool.pair)}</Link></td><td><Link href={`/pools/${encodeURIComponent(pool.pool_address ?? "")}`} className="protocol-cell">{protocolLogo(pool.protocol) && <img className="protocol-logo" src={protocolLogo(pool.protocol)!} alt="" />}{display(pool.protocol)} · {display(pool.type)}</Link></td><td className="numeric">{typeof pool.apr === "number" ? `${pool.apr.toFixed(1)}%` : "N/A"}</td><td className="numeric">{compactMoney(pool.tvl_usd)}</td><td className="numeric"><b className="score-pill">{score(pool.opportunity_score)}</b></td></tr>)}</tbody></table></div>{!error && topPools.length === 0 && <p className="empty">No current eligible opportunities available.</p>}<Link className="module-cta" href="/lps">View all LP opportunities <span aria-hidden="true">→</span></Link></div>
      <aside className="terminal-side"><div className="card compare-card"><div className="terminal-section-heading"><div><p className="eyebrow">CROSS-PROTOCOL</p><h2>{mainPair ?? "N/A"}</h2></div><Link href={mainPair ? `/compare/${encodeURIComponent(mainPair.replaceAll(" / ", "-"))}` : "/compare/SOL-USDC"}>Compare</Link></div>{comparisonPools.length ? <div className="compare-list">{comparisonPools.map((pool) => <Link className="compare-row" href={`/pools/${encodeURIComponent(pool.pool_address ?? "")}`} key={pool.pool_address}><span><strong>{display(pool.protocol)}</strong><small>{display(shortPoolType(pool.type))}</small></span><i><u style={{ width: `${Math.max(4, Math.min(100, ((pool.opportunity_score ?? 0) / (comparisonPools[0].opportunity_score ?? 1)) * 100))}%` }} /></i><b>{score(pool.opportunity_score)}</b></Link>)}</div> : <p className="empty">No comparable current pools available.</p>}<Link className="module-cta" href={mainPair ? `/compare/${encodeURIComponent(mainPair.replaceAll(" / ", "-"))}` : "/compare/SOL-USDC"}>Open comparison <span aria-hidden="true">→</span></Link></div>
        <div className="card health-card"><div className="terminal-section-heading"><div><p className="eyebrow">DATA HEALTH</p><h2>Providers</h2></div><span className="health-summary">{health.length ? `${liveProviders} / 3 LIVE` : "N/A"}</span></div><div className="health-list">{["Raydium", "Orca", "Meteora"].map((name) => { const item = health.find((entry) => entry.protocol === name); const state = providerState(item); return <div className="health-row" key={name}><span>{name}</span><strong className={state === "LIVE" ? "ok" : "muted"}>{state}</strong></div>; })}</div></div></aside></section>
    <section className="future-row"><div className="card future-module"><div><p className="eyebrow">LIVE MODULE</p><h2>Lending Intelligence</h2></div><span className="state-badge">CONNECTED</span><p>Supply APY · utilization · available liquidity · borrow cost · historical evidence</p><small>Data source <Link href="/lending">Open lending radar →</Link></small></div><div className="card future-module"><div><p className="eyebrow">PLANNED MODULE</p><h2>Stablecoin Intelligence</h2></div><span className="state-badge">PLANNED</span><p>Best yield · venue · depeg risk · issuer/custody risk · persistence</p><small>Data source <span>Not connected yet</span></small></div></section>
    <footer className="platform-footer"><span>Analytics only. Not financial advice.</span><span>D.E.F.I. · Opportunity + Risk + Persistence + Data Quality</span></footer></main>;
}

export default function Home() { const pathname = usePathname(); return pathname === "/" ? <Overview /> : <LpRadar />; }
