import type { Schemas } from "../../api/client";

export const runningBatch = {
  id: "c4af010c-e2ed-4ce5-a53b-6d22570eec3f",
  name: "运行中批次",
  status: "running",
  has_leases: true,
} satisfies Schemas["CrawlIpBatch"];
export const historyBatch = {
  id: "ef4212c2-4e28-4d95-93b2-a659e69c5940",
  name: "上周的任务",
  status: "completed",
  has_leases: false,
} satisfies Schemas["CrawlIpBatch"];

const noRisk = { empty_body: 0, challenge: 0, throttled: 0, blocked: 0, other: 0 };
const emptyMetrics = {
  crawl_leases: 0,
  collected_creators: 0,
  private: 0,
  not_found: 0,
  login_required: 0,
  risk_count: 0,
  risk_rate_pct: 0,
  risk_breakdown: noRisk,
  active_hours: 0,
  creators_per_hour: null,
  leases_per_hour: null,
};

const emptySummary: Schemas["CrawlIpSummary"] = {
  ...emptyMetrics,
  active_ips: 0,
  first_lease_at: null,
  last_lease_at: null,
};
const normal = { backoff_state: "normal", backoff_step: 0, backoff_until: null } as const;
const cooldown = {
  backoff_state: "cooling",
  backoff_step: 2,
  backoff_until: "2026-11-26T04:02:00Z",
} as const;

export const crawlIpStats = {
  batch: runningBatch,
  data_status: "available",
  summary: {
    ...emptySummary,
    active_ips: 3,
    first_lease_at: "2026-11-26T03:00:00Z",
    last_lease_at: "2026-11-26T04:00:00Z",
    crawl_leases: 21,
    collected_creators: 10,
    private: 1,
    login_required: 1,
    risk_count: 4,
    risk_rate_pct: 19.05,
    risk_breakdown: { ...noRisk, empty_body: 2, challenge: 1, throttled: 1 },
    active_hours: 1,
    creators_per_hour: 10,
    leases_per_hour: 21,
  },
  lines: [
    {
      ...emptyMetrics,
      ...cooldown,
      line: "192.0.2.11",
      proxy_name: "直连美国家宽 01",
      crawl_leases: 10,
      collected_creators: 6,
      creator_share_pct: 60,
      private: 1,
      risk_count: 3,
      risk_rate_pct: 30,
      risk_share_pct: 75,
      risk_breakdown: { ...noRisk, empty_body: 2, challenge: 1 },
      active_hours: 0.9,
      creators_per_hour: 6,
      leases_per_hour: 10,
    },
    {
      ...emptyMetrics,
      ...normal,
      line: "192.0.2.12",
      proxy_name: "直连美国家宽 02",
      crawl_leases: 8,
      collected_creators: 3,
      creator_share_pct: 30,
      login_required: 1,
      risk_count: 1,
      risk_rate_pct: 12.5,
      risk_share_pct: 25,
      risk_breakdown: { ...noRisk, throttled: 1 },
      active_hours: 0.6,
      creators_per_hour: 3,
      leases_per_hour: 8,
    },
    {
      ...emptyMetrics,
      ...normal,
      line: "2001:db8:17::/64",
      proxy_name: "2001:db8:17::/64",
      crawl_leases: 3,
      collected_creators: 1,
      creator_share_pct: 10,
      risk_share_pct: 0,
      active_hours: 0.25,
      creators_per_hour: 1,
      leases_per_hour: 3,
    },
  ],
} satisfies Schemas["CrawlIpStats"];

export const crawlIpHistory = {
  batch: historyBatch,
  data_status: "history_unavailable",
  summary: emptySummary,
  lines: [],
} satisfies Schemas["CrawlIpStats"];

export const noCrawlIpBatches = {
  batch: null,
  data_status: "no_batch",
  summary: emptySummary,
  lines: [],
} satisfies Schemas["CrawlIpStats"];
