import type { Schemas } from "@/api/client";

export const COLLECTION_METRICS = {
  collected_creators: "采集人数",
  creators_per_hour: "采集时速",
  creator_share_pct: "采集占比",
} as const;
export const RISK_METRICS = { risk_count: "风控次数", risk_rate_pct: "风控率" } as const;
export const RISK_LABELS = {
  empty_body: "空正文",
  challenge: "挑战 / 验证码",
  throttled: "限流",
  blocked: "封禁 / 拦截",
  other: "其他风控",
} as const satisfies Record<keyof Schemas["CrawlIpRisk"], string>;
export const MIN_RISK_ATTEMPTS = 5;
export const HIGH_RISK_PCT = 20;

export type CrawlIp = Schemas["CrawlIpRow"];
export type MetricKey = keyof typeof COLLECTION_METRICS | keyof typeof RISK_METRICS;

const number = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });

export function pct(value: number) {
  return `${number.format(value)}%`;
}

export function formatIpMetric(key: MetricKey, value: number | null) {
  if (value === null) return "—";
  if (key === "creators_per_hour") return `${number.format(value)} 人/时`;
  return key.endsWith("_pct") ? pct(value) : number.format(value);
}

export function chartBars(lines: CrawlIp[], key: MetricKey) {
  const rows = lines
    .map((line) => ({ line, value: line[key] }))
    .filter((row): row is { line: CrawlIp; value: number } => row.value !== null)
    .sort((a, b) => b.value - a.value);
  const max = key.endsWith("_pct") ? 100 : (rows[0]?.value ?? 0);
  return rows.map(({ line, value }) => ({
    line,
    label: formatIpMetric(key, value),
    width: max > 0 ? (value / max) * 100 : 0,
  }));
}

export function isHighRisk(line: CrawlIp) {
  return line.crawl_leases >= MIN_RISK_ATTEMPTS && line.risk_rate_pct >= HIGH_RISK_PCT;
}

export function accountNotes(row: Pick<CrawlIp, "private" | "not_found" | "login_required">) {
  return [
    ["私密", row.private],
    ["不存在", row.not_found],
    ["需登录", row.login_required],
  ]
    .filter(([, count]) => count)
    .map(([label, count]) => `${label} ${count}`)
    .join(" · ");
}
