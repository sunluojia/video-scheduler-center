import type { Schemas } from "@/api/client";

/** 统一的状态色调：待处理、进行中、完成、需要注意、失败、已结束。 */
export type Tone = "idle" | "active" | "done" | "attention" | "failed" | "muted";
type Labels<T extends string> = Record<T, [label: string, tone: Tone]>;

export const batchStatus: Labels<Schemas["BatchStatus"]> = {
  draft: ["草稿", "idle"],
  running: ["进行中", "active"],
  paused: ["已暂停", "attention"],
  completed: ["已完成", "done"],
  cancelled: ["已取消", "muted"],
};

export const creatorStatus: Labels<Schemas["CreatorStatus"]> = {
  pending: ["待爬取", "idle"],
  crawling: ["爬取中", "active"],
  analyzing: ["分析中", "active"],
  summarizing: ["汇总中", "active"],
  completed: ["已完成", "done"],
  failed: ["失败账号", "failed"],
  recrawl: ["需重新抓取", "attention"],
  invalid: ["无法识别", "muted"],
};

export const creatorProgress: Labels<Schemas["CreatorProgress"]> = {
  pending: creatorStatus.pending,
  crawling: creatorStatus.crawling,
  downloading: ["下载中", "active"],
  audio_analysis: ["音频分析中", "active"],
  video_analysis: ["视频分析中", "active"],
  summarizing: creatorStatus.summarizing,
  completed: creatorStatus.completed,
  failed: creatorStatus.failed,
  recrawl: ["需要重新抓取", "attention"],
  invalid: creatorStatus.invalid,
};

/** 达人还在处理中的环节，按流程先后排列。 */
export const creatorFlow: Schemas["CreatorProgress"][] = [
  "pending",
  "crawling",
  "downloading",
  "audio_analysis",
  "video_analysis",
  "summarizing",
];
/** 达人已得出结论的状态。 */
export const creatorFinal = ["completed", "failed", "recrawl", "invalid"] as const;

export const workStatus: Labels<Schemas["WorkStatus"]> = {
  skipped: ["图文", "muted"],
  downloading: ["下载中", "active"],
  analyzing: ["分析中", "active"],
  needs_review: ["待核查", "attention"],
  completed: ["已完成", "done"],
  failed: ["失败", "failed"],
  abandoned: ["已放弃", "muted"],
};

export const identityState: Labels<Schemas["IdentityState"]> = {
  ready: ["可用", "done"],
  in_use: ["使用中", "active"],
  warming: ["预热中", "active"],
  cooling: ["冷却中", "attention"],
  stale: ["待预热", "idle"],
  retired: ["已退休", "muted"],
};

export const jobStatus: Labels<Schemas["JobStatus"]> = {
  submitted: ["已提交", "idle"],
  accepted: ["已受理", "active"],
  processing: ["处理中", "active"],
  completed: ["已完成", "done"],
  failed: ["失败", "failed"],
  needs_review: ["待核查", "attention"],
  abandoned: ["已放弃", "muted"],
  cancelled: ["已取消", "muted"],
};

export const stageStatus: Labels<Schemas["StageStatus"]> = {
  started: ["进行中", "active"],
  succeeded: ["完成", "done"],
  retrying: ["等待重试", "attention"],
  failed: ["失败", "failed"],
  needs_review: ["待核查", "attention"],
  skipped: ["跳过", "muted"],
};

export const taskStatus: Labels<Schemas["CrawlerTaskStatus"]> = {
  published: ["已发布", "idle"],
  accepted: ["已受理", "active"],
  succeeded: ["已完成", "done"],
  failed: ["失败", "failed"],
  cancelled: ["已取消", "muted"],
};

export const conclusions: Record<Schemas["CrawlConclusion"], string> = {
  ok: "锁定作品",
  private: "私密账号",
  not_found: "找不到此达人",
  failed: "爬取失败",
};

export const stageNames: Record<string, string> = {
  fetch: "获取视频",
  extract_frames: "抽帧",
  extract_audio: "提取音频",
  listen: "听音",
  recognize: "识别",
  summarize: "汇总",
};

export const executorKinds: Record<Schemas["ExecutorKind"], string> = {
  crawler: "爬取程序",
  analysis: "分析服务",
};

export const proxyStatus: Labels<Schemas["ProxyStatus"]> = {
  untested: ["待测试", "idle"],
  available: ["可用", "done"],
  unreachable: ["连不上", "failed"],
  auth_failed: ["认证失败", "failed"],
  refreshing: ["刷新出口", "active"],
};

export const lineState: Labels<Schemas["LineState"]> = {
  open: ["正常", "done"],
  probing: ["试探中", "active"],
  throttled: ["限流", "attention"],
  cooling: ["冷却中", "failed"],
};

export const proxyProtocols: Record<Schemas["ProxyProtocol"], string> = {
  auto: "自动识别",
  http: "HTTP",
  https: "HTTPS",
  socks5: "SOCKS5",
  ssh: "SSH",
};

export const ruleSources: Record<Schemas["RuleSource"], string> = {
  direct: "直连",
  pool: "代理池",
  local_port: "本机端口",
};

export const pickModes: Record<Schemas["PickMode"], string> = {
  priority: "优先级",
  weighted: "权重",
  rotate: "轮询（旧设置）",
};

/** 色调对应的颜色类，状态标记和进度条共用。 */
export const toneClass: Record<Tone, { dot: string; text: string; bar: string }> = {
  idle: { dot: "bg-status-idle", text: "text-status-idle", bar: "bg-status-idle/60" },
  active: { dot: "bg-status-active", text: "text-status-active", bar: "bg-status-active" },
  done: { dot: "bg-status-done", text: "text-status-done", bar: "bg-status-done" },
  attention: {
    dot: "bg-status-attention",
    text: "text-[oklch(0.5_0.13_65)]",
    bar: "bg-status-attention",
  },
  failed: { dot: "bg-status-failed", text: "text-status-failed", bar: "bg-status-failed" },
  muted: { dot: "bg-status-muted", text: "text-muted-foreground", bar: "bg-status-muted" },
};

/** 按状态表的顺序排列计数，进度条和图例都按这个顺序。 */
export function ordered<T extends string>(labels: Labels<T>, counts: Partial<Record<T, number>>) {
  return (Object.keys(labels) as T[])
    .filter((status) => counts[status])
    .map((status) => ({
      status,
      label: labels[status][0],
      tone: labels[status][1],
      count: counts[status] ?? 0,
    }));
}

export function total(counts: Partial<Record<string, number>>): number {
  return Object.values(counts).reduce<number>((sum, count) => sum + (count ?? 0), 0);
}
