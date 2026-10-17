#!/usr/bin/env node
// Baut die statische Website aus src/ nach public/.
//   node business/site/build.mjs            Vorschau-Build (fehlende Konfiguration wird sichtbar markiert)
//   node business/site/build.mjs --strict   Verkaufs-Build: bricht bei fehlender Konfiguration oder Prüffehlern ab
import {
  readFileSync, writeFileSync, mkdirSync, readdirSync, statSync, copyFileSync, rmSync, existsSync,
} from "node:fs";
import { dirname, join, relative, resolve, extname, sep, posix } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, "src");
const OUT = join(HERE, "public");
const STRICT = process.argv.includes("--strict");

const config = JSON.parse(readFileSync(join(HERE, "..", "config.json"), "utf8"));
const manifestPath = join(HERE, "..", "product", "dist", "manifest.json");
if (!existsSync(manifestPath)) {
  console.error("FEHLER: business/product/dist/manifest.json fehlt. Zuerst das Produkt bauen: python3 business/product/build.py");
  process.exit(1);
}
const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));

const OPTIONAL = new Set(["CONTACT_PHONE", "USTID"]);
const missing = new Set();
const problems = [];

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const cfgValue = (key) => String(config[key] ?? "").trim();
const siteUrl = cfgValue("SITE_URL").replace(/\/+$/, "");

function value(key) {
  if (key in manifest) return String(manifest[key]);
  if (key === "YEAR") return String(new Date().getFullYear());
  if (key === "SITE_URL") return siteUrl || (missing.add(key), "[[SITE_URL FEHLT]]");
  if (key === "CHECKOUT_URL") {
    const v = cfgValue(key);
    if (!v) { missing.add(key); return "#preis"; }
    return v;
  }
  if (key in config) {
    const v = cfgValue(key);
    if (!v) {
      if (OPTIONAL.has(key)) return "";
      missing.add(key);
      return `[[${key} FEHLT]]`;
    }
    return v;
  }
  throw new Error(`Unbekannte Variable {{${key}}}`);
}

function productJsonLd() {
  const data = {
    "@context": "https://schema.org",
    "@type": "Product",
    name: cfgValue("PRODUCT_NAME"),
    description: "Angebotsvorlage, Kalkulations-Tabelle, Onboarding-Fragebogen, Mail-Bausteine und KI-Prompts für Selbstständige in Deutschland.",
    offers: {
      "@type": "Offer",
      price: cfgValue("PRICE_EUR"),
      priceCurrency: "EUR",
      availability: "https://schema.org/InStock",
      url: siteUrl || undefined,
    },
  };
  return JSON.stringify(data).replace(/</g, "\\u003c");
}

function readPartial(name) {
  return readFileSync(join(SRC, "partials", name), "utf8");
}

function render(html, rootPrefix, pagePath) {
  // Includes (verschachtelt möglich)
  for (let i = 0; i < 5 && /\{\{include:[^}]+\}\}/.test(html); i++) {
    html = html.replace(/\{\{include:([^}]+)\}\}/g, (_, n) => readPartial(n.trim()));
  }
  // Bedingte Blöcke für optionale Felder
  html = html.replace(/\{\{#([A-Z_]+)\}\}([\s\S]*?)\{\{\/\1\}\}/g, (_, key, inner) => (cfgValue(key) ? inner : ""));
  html = html.replace(/\{\{ROOT\}\}/g, rootPrefix);
  html = html.replace(/\{\{PAGE_PATH\}\}/g, pagePath);
  html = html.replace(/\{\{RAW_JSONLD_PRODUCT\}\}/g, () => productJsonLd());
  html = html.replace(/\{\{([A-Z_]+)\}\}/g, (_, key) => esc(value(key)));
  const left = html.match(/\{\{[^}]*\}\}/);
  if (left) throw new Error(`Nicht aufgelöster Platzhalter: ${left[0]}`);
  return html;
}

function walk(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });

const htmlPages = [];
for (const file of walk(SRC)) {
  const rel = relative(SRC, file).split(sep).join("/");
  if (rel.startsWith("partials/")) continue;
  const dest = join(OUT, rel);
  mkdirSync(dirname(dest), { recursive: true });
  const ext = extname(rel);
  if (ext === ".html" || ext === ".txt") {
    const depth = rel.split("/").length - 1;
    const rootPrefix = depth === 0 ? "./" : "../".repeat(depth);
    const pagePath = rel === "index.html" ? "" : rel.replace(/index\.html$/, "");
    writeFileSync(dest, render(readFileSync(file, "utf8"), rootPrefix, pagePath));
    if (ext === ".html") htmlPages.push({ rel, pagePath });
  } else {
    copyFileSync(file, dest);
  }
}

// Sitemap + robots (nur mit bekannter Domain)
if (siteUrl) {
  const urls = htmlPages
    .filter((p) => !/^(impressum|datenschutz)\//.test(p.rel))
    .map((p) => `  <url><loc>${esc(siteUrl + "/" + p.pagePath)}</loc></url>`)
    .join("\n");
  writeFileSync(join(OUT, "sitemap.xml"), `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`);
  writeFileSync(join(OUT, "robots.txt"), `User-agent: *\nAllow: /\n\nSitemap: ${siteUrl}/sitemap.xml\n`);
} else {
  writeFileSync(join(OUT, "robots.txt"), "User-agent: *\nDisallow: /\n"); // Vorschau soll nicht indexiert werden
}

// ---- Prüfungen -------------------------------------------------------------
const outFiles = new Set(walk(OUT).map((f) => relative(OUT, f).split(sep).join("/")));
const ids = new Map();
for (const p of htmlPages) {
  const html = readFileSync(join(OUT, p.rel), "utf8");
  ids.set(p.rel, new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1])));
}

for (const p of htmlPages) {
  const html = readFileSync(join(OUT, p.rel), "utf8");
  const baseDir = posix.dirname(p.rel);

  // Externe Ressourcen (Datenschutz-Zusage der Seite: keine Drittanbieter-Requests)
  for (const m of html.matchAll(/<(script|img|iframe|source|video|audio)\b[^>]*\ssrc="(https?:)?\/\/[^"]+"/gi)) {
    problems.push(`${p.rel}: externe Ressource geladen: ${m[0].slice(0, 90)}`);
  }
  for (const m of html.matchAll(/<link\b[^>]*\bhref="(https?:)?\/\/[^"]+"[^>]*>/gi)) {
    if (/rel="(stylesheet|preload|preconnect|dns-prefetch|modulepreload|icon)"/i.test(m[0])) problems.push(`${p.rel}: externe Ressource geladen: ${m[0].slice(0, 90)}`);
  }

  // Interne Links und Anker
  for (const m of html.matchAll(/\s(?:href|src)="([^"]*)"/g)) {
    const url = m[1];
    if (!url || /^(mailto:|tel:|https?:|data:|javascript:)/i.test(url) || url.startsWith("[[")) continue;
    const [pathPart, frag] = url.split("#");
    let target = p.rel;
    if (pathPart) {
      target = posix.normalize(posix.join(baseDir, pathPart.split("?")[0]));
      if (target.endsWith("/") || (!extname(target) && !outFiles.has(target))) target = posix.join(target, "index.html");
      if (!outFiles.has(target)) { problems.push(`${p.rel}: toter Link → ${url}`); continue; }
    }
    if (frag && target.endsWith(".html") && !ids.get(target)?.has(frag)) problems.push(`${p.rel}: Anker fehlt → ${url}`);
  }
}
for (const f of outFiles) {
  if (!f.endsWith(".css")) continue;
  if (/@import|url\(\s*["']?(https?:)?\/\//i.test(readFileSync(join(OUT, f), "utf8"))) problems.push(`${f}: externe Ressource im CSS`);
}
for (const f of outFiles) {
  if (!f.endsWith(".js")) continue;
  if (/\b(fetch|XMLHttpRequest|sendBeacon|WebSocket)\b|https?:\/\//.test(readFileSync(join(OUT, f), "utf8"))) problems.push(`${f}: Netzwerkzugriff oder URL im Skript (Datenschutz-Zusage)`);
}

const unresolved = [];
for (const f of outFiles) if (/\.(html|txt|xml)$/.test(f) && readFileSync(join(OUT, f), "utf8").includes("[[")) unresolved.push(f);

if (STRICT) {
  const checkout = cfgValue("CHECKOUT_URL");
  if (checkout && !/^https:\/\//.test(checkout)) problems.push("CHECKOUT_URL muss mit https:// beginnen");
  if (siteUrl && !/^https:\/\//.test(siteUrl)) problems.push("SITE_URL muss mit https:// beginnen");
  const mail = cfgValue("CONTACT_EMAIL");
  if (mail && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(mail)) problems.push("CONTACT_EMAIL sieht ungültig aus");
}

const count = htmlPages.length;
console.log(`Seiten gebaut: ${count} → ${relative(process.cwd(), OUT) || OUT}`);
if (missing.size) console.log(`Fehlende Konfiguration (business/config.json): ${[...missing].sort().join(", ")}`);
if (unresolved.length) console.log(`Seiten mit sichtbaren Platzhaltern: ${unresolved.join(", ")}`);
if (problems.length) console.log("Prüffehler:\n  - " + problems.join("\n  - "));

if (problems.length || (STRICT && (missing.size || unresolved.length))) {
  if (STRICT) {
    console.error("\nSTRICT-Build abgebrochen. Nicht veröffentlichen.");
    rmSync(OUT, { recursive: true, force: true });
    process.exit(1);
  }
  process.exitCode = problems.length ? 1 : 0;
}
