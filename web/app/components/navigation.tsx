import Link from "next/link";

const links = [["Overview", "/"], ["LPs", "/lps"], ["Lending", "/lending"], ["Stablecoins", "/stablecoins"], ["Compare", "/compare/SOL-USDC"], ["Protocols", "/protocols"], ["Methodology", "/methodology"], ["Status", "/status"]];

export default function Navigation() {
  return <nav className="platform-nav" aria-label="Primary navigation">{links.map(([label, href]) => <Link href={href} key={href}>{label}</Link>)}</nav>;
}
