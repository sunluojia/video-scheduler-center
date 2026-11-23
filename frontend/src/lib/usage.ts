import { useState } from "react";
import { $ai, type AnalysisSchemas } from "@/api/client";

export type UsageRow = AnalysisSchemas["UsageRow"];

/** 输入 token 的缓存命中率：只算报告了命中数的调用；没有这样的调用时为 null。 */
export function cacheRate(rows: UsageRow[] | undefined): number | null {
  const basis = sum(rows, "cache_input_tokens");
  return basis ? sum(rows, "cached_tokens") / basis : null;
}

export function sum(rows: UsageRow[] | undefined, key: keyof UsageRow & string): number {
  return (rows ?? []).reduce((total, row) => total + Number(row[key]), 0);
}

/** 最近 24 小时的模型调用；打开页面时定下时间段，每分钟刷新一次。 */
export function useRecentAiUsage() {
  const [since] = useState(() => new Date(Date.now() - 24 * 3600_000).toISOString());
  const [until] = useState(() => new Date(Date.now() + 3600_000).toISOString());
  return $ai.useQuery(
    "get",
    "/api/ai/usage",
    { params: { query: { since, until, bucket: "day" } } },
    { refetchInterval: 60_000 },
  );
}
