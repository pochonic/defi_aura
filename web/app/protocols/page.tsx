"use client";
import { useEffect, useState } from "react";
import Navigation from "../components/navigation";

type Health = { protocol: string; status: string; checked_at: string; error: string };
const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const protocolLogo = (protocol: string) => protocol === "Raydium" ? "https://raydium.io/favicon.ico" : protocol === "Orca" ? "https://www.orca.so/favicon.ico" : "https://app.meteora.ag/favicon.ico";
export default function ProtocolsPage() {
  const [health, setHealth] = useState<Health[]>([]);
  useEffect(() => { fetch(`${API}/api/protocols/health`).then((response) => response.json()).then(setHealth).catch(() => setHealth([])); }, []);
  return <main className="shell"><Navigation /><p className="eyebrow">PROTOCOLS</p><h1>Protocols</h1><p className="subtitle">Connected protocol coverage across the platform.</p><section className="protocol-grid">{["Raydium", "Orca", "Meteora"].map((name) => { const item = health.find((entry) => entry.protocol === name); return <article className="card protocol-card" key={name}><h2><img className="protocol-logo large" src={protocolLogo(name)} alt="" />{name}</h2><div><span>Chain</span><strong>Solana</strong></div><div><span>Category</span><strong>LP</strong></div><div><span>TVL</span><strong>N/A</strong></div><div><span>Products</span><strong>LP</strong></div><div><span>Risk</span><strong>N/A</strong></div><div><span>Data status</span><strong className={item?.status === "OK" ? "ok" : "muted"}>{item?.status === "OK" ? "LIVE" : item ? "UNAVAILABLE" : "N/A"}</strong></div></article>; })}</section></main>;
}
