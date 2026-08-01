#!/usr/bin/env node
/** Independent JavaScript reference for poc-c14n-v1. */

import crypto from "node:crypto";
import fs from "node:fs";

export const PROFILE = "poc-c14n-v1";
export const HASH_ALGORITHM = "sha-256";

const TOP = new Set(["version","schema_version","canonicalization_profile","hash_algorithm","session_type","session_id","user_id","evse_id","ocpp_tx_id","start_ts","end_ts","energy_kwh","energy_summary","pricing","settlement","merkle_root","stream_hash_alg"]);
const ENERGY_SUMMARY = new Set(["import_kwh","export_kwh","net_kwh"]);
const PRICING = new Set(["currency","model","components","import_components","export_components"]);
const COMPONENT = new Set(["from","to","price_per_kwh"]);
const SETTLEMENT = new Set(["gross_import_cost","gross_export_credit","net_amount","currency"]);
const ENERGY_PATHS = new Set(["energy_kwh","energy_summary.import_kwh","energy_summary.export_kwh","energy_summary.net_kwh"]);
const MONEY_PATHS = new Set(["settlement.gross_import_cost","settlement.gross_export_credit","settlement.net_amount"]);

export function canonicalizeReceipt(receipt, profile) {
  if (profile !== PROFILE) throw new Error(`Unknown canonicalization profile: ${profile}`);
  if (!isObject(receipt)) throw new Error("Receipt must be an object");
  validateShape(receipt);
  if (receipt.canonicalization_profile !== profile) throw new Error("Receipt canonicalization_profile does not match requested profile");
  if (receipt.hash_algorithm !== HASH_ALGORITHM) throw new Error(`hash_algorithm must be ${HASH_ALGORITHM}`);
  const text = JSON.stringify(normalize(receipt, []));
  return Buffer.from(text, "utf8");
}

export function hashCanonicalReceipt(receipt, profile) {
  return `0x${crypto.createHash("sha256").update(canonicalizeReceipt(receipt, profile)).digest("hex")}`;
}

function validateShape(receipt) {
  exact(receipt, TOP, "receipt");
  exactObject(receipt.energy_summary, ENERGY_SUMMARY, "energy_summary");
  exactObject(receipt.pricing, PRICING, "pricing");
  for (const name of ["components", "import_components", "export_components"]) {
    const values = receipt.pricing[name];
    if (!Array.isArray(values)) throw new Error(`pricing.${name} must be an array`);
    values.forEach((item, index) => exactObject(item, COMPONENT, `pricing.${name}[${index}]`));
  }
  exactObject(receipt.settlement, SETTLEMENT, "settlement");
}

function exactObject(value, fields, path) {
  if (!isObject(value)) throw new Error(`${path} must be an object`);
  exact(value, fields, path);
}

function exact(value, fields, path) {
  const keys = Object.keys(value);
  const missing = [...fields].filter(key => !Object.hasOwn(value, key)).sort(codePointCompare);
  const extra = keys.filter(key => !fields.has(key)).sort(codePointCompare);
  if (missing.length) throw new Error(`${path} missing required fields: ${missing.join(", ")}`);
  if (extra.length) throw new Error(`${path} contains fields not defined by ${PROFILE}: ${extra.join(", ")}`);
  const nulls = keys.filter(key => value[key] === null).sort(codePointCompare);
  if (nulls.length) throw new Error(`${path} fields are not nullable: ${nulls.join(", ")}`);
}

function normalize(value, path) {
  if (Array.isArray(value)) return value.map(item => normalize(item, [...path, "[]"]));
  if (isObject(value)) {
    const result = {};
    const normalizedKeys = new Set();
    for (const key of Object.keys(value).sort(codePointCompare)) {
      const normalizedKey = normalizeString(key);
      if (normalizedKeys.has(normalizedKey)) throw new Error(`Unicode-normalized duplicate key: ${normalizedKey}`);
      normalizedKeys.add(normalizedKey);
      result[normalizedKey] = normalize(value[key], [...path, key]);
    }
    return result;
  }
  if (value === null) throw new Error(`Null is not allowed at ${showPath(path)}`);
  const joined = path.join(".");
  if (ENERGY_PATHS.has(joined) || MONEY_PATHS.has(joined) || (path.at(-1) === "price_per_kwh" && path.at(-2) === "[]")) return fixedDecimal(value, 3, path);
  if (["start_ts", "end_ts", "from", "to"].includes(path.at(-1))) return timestamp(value, path);
  if (typeof value === "string") return normalizeString(value);
  if (typeof value === "boolean") return value;
  if (typeof value === "number" || typeof value === "bigint") {
    if (typeof value === "number" && !Number.isFinite(value)) throw new Error(`Non-finite number at ${showPath(path)}`);
    throw new Error(`Unscaled numeric field is not allowed at ${showPath(path)}`);
  }
  throw new Error(`Unsupported value type at ${showPath(path)}: ${typeof value}`);
}

function fixedDecimal(value, scale, path) {
  if (typeof value !== "string") throw new Error(`Canonical decimal must be a string at ${showPath(path)}`);
  let raw = value;
  const match = raw.match(/^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/);
  if (!match) throw new Error(`Invalid decimal at ${showPath(path)}: ${value}`);
  const negative = match[1] === "-";
  const integer = match[2];
  const fraction = match[3] || "";
  const exponent = Number(match[4] || 0);
  let digits = (integer + fraction).replace(/^0+(?=\d)/, "");
  let decimalPlaces = fraction.length - exponent;
  if (decimalPlaces < 0) { digits += "0".repeat(-decimalPlaces); decimalPlaces = 0; }
  let scaled;
  if (decimalPlaces <= scale) {
    scaled = BigInt(digits || "0") * 10n ** BigInt(scale - decimalPlaces);
  } else {
    const divisor = 10n ** BigInt(decimalPlaces - scale);
    const absolute = BigInt(digits || "0");
    scaled = absolute / divisor;
    const remainder = absolute % divisor;
    if (remainder * 2n >= divisor) scaled += 1n; // ROUND_HALF_UP
  }
  if (negative && scaled !== 0n) scaled = -scaled;
  const sign = scaled < 0n ? "-" : "";
  let output = (scaled < 0n ? -scaled : scaled).toString().padStart(scale + 1, "0");
  return `${sign}${output.slice(0, -scale)}.${output.slice(-scale)}`;
}

function timestamp(value, path) {
  if (typeof value !== "string") throw new Error(`Timestamp must be a string at ${showPath(path)}`);
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|z|[+-]\d{2}:\d{2})$/);
  if (!match) throw new Error(`Invalid RFC 3339 timestamp at ${showPath(path)}: ${value}`);
  const [, year, month, day, hour, minute, second, fraction = "", zone] = match;
  const micros = Number(fraction.padEnd(6, "0"));
  let offsetMinutes = 0;
  if (!["Z", "z"].includes(zone)) {
    const sign = zone[0] === "-" ? -1 : 1;
    offsetMinutes = sign * (Number(zone.slice(1, 3)) * 60 + Number(zone.slice(4, 6)));
  }
  const localMillis = Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), Number(second));
  const local = new Date(localMillis);
  if (local.getUTCFullYear() !== Number(year) || local.getUTCMonth() !== Number(month) - 1 || local.getUTCDate() !== Number(day) || local.getUTCHours() !== Number(hour) || local.getUTCMinutes() !== Number(minute) || local.getUTCSeconds() !== Number(second)) throw new Error(`Invalid RFC 3339 timestamp at ${showPath(path)}: ${value}`);
  const millis = localMillis - offsetMinutes * 60000;
  const date = new Date(millis);
  if (Number.isNaN(date.valueOf())) throw new Error(`Invalid RFC 3339 timestamp at ${showPath(path)}: ${value}`);
  const base = date.toISOString().slice(0, 19);
  return `${base}.${String(micros).padStart(6, "0")}Z`;
}

function normalizeString(value) {
  for (const char of value) {
    const code = char.codePointAt(0);
    if (code >= 0xD800 && code <= 0xDFFF) throw new Error("Strings must not contain lone Unicode surrogates");
  }
  return value.normalize("NFC");
}

function codePointCompare(left, right) {
  const a = Array.from(left, char => char.codePointAt(0));
  const b = Array.from(right, char => char.codePointAt(0));
  for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i] - b[i];
  return a.length - b.length;
}

function showPath(path) { return path.join(".").replaceAll(".[]", "[]") || "receipt"; }
function isObject(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }

if (import.meta.url === `file://${process.argv[1]}`) {
  const input = JSON.parse(fs.readFileSync(process.argv[2] || 0, "utf8"));
  const bytes = canonicalizeReceipt(input, input.canonicalization_profile);
  process.stdout.write(JSON.stringify({canonical_utf8_base64: bytes.toString("base64"), canonical_text: bytes.toString("utf8"), hash: hashCanonicalReceipt(input, input.canonicalization_profile)}));
}
