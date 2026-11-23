import {
  addDays,
  addHours,
  differenceInHours,
  format,
  startOfDay,
  startOfHour,
  subDays,
  subHours,
} from "date-fns";
import type { Schemas } from "@/api/client";

export const PRESETS = {
  day: "近 24 小时",
  today: "今天",
  yesterday: "昨天",
  week: "近 7 天",
  month: "近 30 天",
  batch: "批次全程",
  custom: "自定义",
} as const;
export type Preset = keyof typeof PRESETS;
export type Bucket = Schemas["Bucket"];
export type Period = { since: Date; until: Date };

/** 统计接口一次最多返回的时段数。 */
const MAX_BUCKETS = 800;

/**
 * 预设时间段对齐到整点或零点，同一小时内查询条件不变；结束时间可以在未来，图上留出还没到的时段。
 * 自定义时间段不完整、批次还没开始时返回 null。
 */
export function periodOf(
  preset: Preset,
  now: Date,
  { from, to, batch }: { from?: string; to?: string; batch?: Schemas["BatchRow"] } = {},
): Period | null {
  const today = startOfDay(now);
  const nextHour = addHours(startOfHour(now), 1);
  switch (preset) {
    case "day":
      return { since: subHours(nextHour, 24), until: nextHour };
    case "today":
      return { since: today, until: addDays(today, 1) };
    case "yesterday":
      return { since: subDays(today, 1), until: today };
    case "week":
      return { since: subDays(today, 6), until: addDays(today, 1) };
    case "month":
      return { since: subDays(today, 29), until: addDays(today, 1) };
    case "batch": {
      if (!batch?.started_at) return null;
      const end = batch.finished_at ? new Date(batch.finished_at) : now;
      return {
        since: startOfHour(new Date(batch.started_at)),
        until: addHours(startOfHour(end), 1),
      };
    }
    case "custom": {
      if (!from || !to) return null;
      const period = { since: new Date(from), until: new Date(to) };
      return period.since < period.until ? period : null;
    }
  }
}

/** 三天以内按小时，更长按天。 */
export function defaultBucket({ since, until }: Period): Bucket {
  return differenceInHours(until, since) <= 72 ? "hour" : "day";
}

/** 按小时会超过接口上限时只能按天。 */
export function hourly({ since, until }: Period): boolean {
  return differenceInHours(until, since) <= MAX_BUCKETS;
}

/** 填进 datetime-local 输入框的本地时间。 */
export function inputValue(date: Date): string {
  return format(date, "yyyy-MM-dd'T'HH:mm");
}

/** 时段的显示：按小时显示到整点，按天显示日期。 */
export function bucketLabel(at: string, bucket: Bucket, long = false): string {
  if (bucket === "day") return format(new Date(at), "MM-dd");
  return format(new Date(at), long ? "MM-dd HH:00" : "HH:00");
}
