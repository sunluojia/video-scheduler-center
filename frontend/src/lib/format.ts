import { differenceInSeconds, format, formatDistanceToNowStrict } from "date-fns";
import { zhCN } from "date-fns/locale";

/** 时间按浏览器所在时区显示。 */
export function dateTime(value: string | null | undefined): string {
  return value ? format(new Date(value), "yyyy-MM-dd HH:mm:ss") : "—";
}

export function shortTime(value: string | null | undefined): string {
  return value ? format(new Date(value), "MM-dd HH:mm") : "—";
}

export function ago(value: string | null | undefined): string {
  return value
    ? formatDistanceToNowStrict(new Date(value), { locale: zhCN, addSuffix: true })
    : "—";
}

/** 两个时间之间的时长；未结束时算到现在。 */
export function duration(start: string | null | undefined, end?: string | null): string {
  if (!start) return "—";
  const seconds = differenceInSeconds(end ? new Date(end) : new Date(), new Date(start));
  if (seconds < 60) return `${Math.max(seconds, 0)} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  const hours = Math.floor(seconds / 3600);
  return hours < 48
    ? `${hours} 小时 ${Math.floor((seconds % 3600) / 60)} 分`
    : `${Math.floor(hours / 24)} 天`;
}

export function bytes(value: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

/** 比例显示为百分比；没有分母时显示破折号。 */
export function percent(rate: number | null | undefined, digits = 1): string {
  return rate == null ? "—" : `${(rate * 100).toFixed(digits)}%`;
}

/** 大数字缩写：1,284 / 12.9万 / 3.4亿。 */
export function compact(value: number): string {
  if (value >= 1e8) return `${(value / 1e8).toFixed(1)}亿`;
  if (value >= 1e4) return `${(value / 1e4).toFixed(1)}万`;
  return value.toLocaleString("zh-CN");
}

/** 秒数显示为易读的时长。 */
export function seconds(value: number): string {
  if (value < 60) return `${value.toFixed(value < 10 ? 1 : 0)} 秒`;
  if (value < 3600) return `${Math.floor(value / 60)} 分 ${Math.floor(value % 60)} 秒`;
  return `${Math.floor(value / 3600)} 小时 ${Math.floor((value % 3600) / 60)} 分`;
}
