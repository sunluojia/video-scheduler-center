import { ExternalLink, Image as ImageIcon, RefreshCw, Sigma } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { $api, type Schemas } from "@/api/client";
import { AttemptHistory, ExecutorName } from "@/components/attempt-history";
import { Facts, Notice, PageHeader, Panel, QueryState, Section, Tags } from "@/components/page";
import { Status, StatusOf } from "@/components/status";
import { Button } from "@/components/ui/button";
import { ago, bytes, dateTime } from "@/lib/format";
import { useDone } from "@/lib/refresh";
import {
  conclusions,
  creatorStatus,
  jobStatus,
  stageNames,
  stageStatus,
  taskStatus,
  workStatus,
} from "@/lib/status";
import { cn } from "@/lib/utils";

const SLOTS = [1, 2, 3, 4, 5];

export function CreatorDetailPage() {
  const creatorId = useParams().creatorId!;
  const done = useDone();
  const path = { params: { path: { creator_id: creatorId } } };
  const detail = $api.useQuery("get", "/api/creators/{creator_id}", path);
  const recrawl = $api.useMutation("post", "/api/creators/{creator_id}/recrawl", {
    onSuccess: () => done("已重新发布爬取任务"),
  });
  const resummarize = $api.useMutation("post", "/api/creators/{creator_id}/resummarize", {
    onSuccess: () => done("已重新提交汇总"),
  });

  return (
    <QueryState query={detail} rows={6}>
      {({ creator, batch, crawl, works, summary, crawl_attempts, summary_attempts, history }) => (
        <>
          <PageHeader
            eyebrow={
              <Link to={`/batches/${batch.id}`} className="hover:text-foreground">
                {batch.name} / 第 {creator.position} 位
              </Link>
            }
            title={creator.name}
            meta={
              <>
                <StatusOf labels={creatorStatus} value={creator.status} />
                {creator.handle && (
                  <a
                    href={`https://www.tiktok.com/@${creator.handle}`}
                    target="_blank"
                    rel="noreferrer"
                    className="hover:text-foreground inline-flex items-center gap-1 font-mono"
                  >
                    @{creator.handle} <ExternalLink className="size-3" />
                  </a>
                )}
                <span>最近活动 {ago(creator.updated_at)}</span>
              </>
            }
            actions={
              <>
                {["completed", "failed", "recrawl"].includes(creator.status) && (
                  <Button
                    variant="outline"
                    onClick={() => recrawl.mutate(path)}
                    disabled={recrawl.isPending}
                  >
                    <RefreshCw /> 重新爬取
                  </Button>
                )}
                {summary && ["summarizing", "completed"].includes(creator.status) && (
                  <Button
                    variant="outline"
                    onClick={() => resummarize.mutate(path)}
                    disabled={resummarize.isPending}
                  >
                    <Sigma /> 重新汇总
                  </Button>
                )}
              </>
            }
          />
          {creator.note && (
            <div className="rise border-status-attention/40 bg-status-attention/10 rounded-md border px-4 py-2.5 text-sm">
              {creator.note}
            </div>
          )}
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_320px]">
            <div className="space-y-8">
              <Section title="锁定的作品">
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                  {SLOTS.map((position) => (
                    <WorkSlot
                      key={position}
                      work={works.find((work) => work.position === position)}
                      position={position}
                      crawled={crawl?.conclusion === "ok"}
                      count={works.length}
                    />
                  ))}
                </div>
              </Section>
              <Section title="汇总">
                <Panel className="space-y-3 p-5">
                  {summary ? (
                    <>
                      <div className="flex items-center gap-3 text-sm">
                        <StatusOf labels={jobStatus} value={summary.status} />
                        <span className="text-muted-foreground">
                          第 {summary.run} 轮 · 基于{" "}
                          {works.filter((work) => work.status === "completed").length} 条成功的视频
                        </span>
                      </div>
                      <Tags tags={[...creator.labels, ...(creator.tags ?? [])]} />
                      {summary.error && (
                        <p className="text-status-failed text-sm">{summary.error}</p>
                      )}
                      <Facts
                        items={[
                          ["最近执行器", <ExecutorName id={summary.last_executor_id} />],
                          ["结果执行器", <ExecutorName id={summary.result_executor_id} />],
                        ]}
                      />
                    </>
                  ) : (
                    <p className="text-muted-foreground text-sm">
                      {creator.labels.length ? (
                        <Tags tags={creator.labels} />
                      ) : (
                        "视频都有结果后自动汇总。"
                      )}
                    </p>
                  )}
                </Panel>
              </Section>
              <Section title="执行尝试">
                <AttemptHistory
                  title="爬取认领"
                  attempts={crawl_attempts}
                  defaultStage="爬取达人"
                />
                <AttemptHistory
                  title="汇总尝试"
                  attempts={summary_attempts}
                  defaultStage="汇总任务"
                />
              </Section>
              <Section title="爬取记录">
                <Panel>
                  <History events={history} />
                </Panel>
              </Section>
            </div>
            <aside className="space-y-8">
              <Section title="名单信息">
                <Panel className="space-y-4 p-5">
                  <QrCode creatorId={creator.id} />
                  <Facts
                    items={[
                      [
                        "creator_oecuid",
                        <span className="font-mono">{creator.oecuid ?? "—"}</span>,
                      ],
                      ["名单行号", creator.row ?? "粘贴链接"],
                      ["标签", <Tags tags={creator.labels} />],
                    ]}
                  />
                </Panel>
              </Section>
              {creator.profile && (
                <Section title="平台资料">
                  <Panel className="p-5">
                    <Facts
                      items={[
                        ["昵称", creator.profile.nickname],
                        [
                          "平台用户 ID",
                          <span className="font-mono">
                            {creator.profile.platform_user_id ?? "—"}
                          </span>,
                        ],
                        [
                          "主页",
                          <a
                            href={creator.profile.url}
                            target="_blank"
                            rel="noreferrer"
                            className="hover:text-foreground inline-flex items-center gap-1 font-mono"
                          >
                            @{creator.profile.handle} <ExternalLink className="size-3" />
                          </a>,
                        ],
                      ]}
                    />
                  </Panel>
                </Section>
              )}
              {crawl && (
                <Section title="爬取任务">
                  <Panel className="p-5">
                    <Facts
                      items={[
                        ["状态", <StatusOf labels={taskStatus} value={crawl.status} />],
                        ["轮次", <span className="font-mono">{crawl.run}</span>],
                        ["结论", crawl.conclusion ? conclusions[crawl.conclusion] : "—"],
                        ["原因", crawl.error],
                        ["发布", dateTime(crawl.published_at)],
                        ["受理", dateTime(crawl.accepted_at)],
                        ["结束", dateTime(crawl.finished_at)],
                        ["最近执行器", <ExecutorName id={crawl.last_started_executor_id} />],
                        ["结果执行器", <ExecutorName id={crawl.result_executor_id} />],
                      ]}
                    />
                  </Panel>
                </Section>
              )}
            </aside>
          </div>
        </>
      )}
    </QueryState>
  );
}

function WorkSlot({
  work,
  position,
  crawled,
  count,
}: {
  work?: Schemas["WorkView"];
  position: number;
  crawled: boolean;
  count: number;
}) {
  if (!work) {
    return (
      <div className="text-muted-foreground flex min-h-36 flex-col justify-between rounded-lg border border-dashed p-3 text-xs">
        <span className="font-mono">#{position}</span>
        {crawled ? `作品不足五条（实际 ${count} 条）` : "尚未锁定"}
      </div>
    );
  }
  const body = (
    <>
      <div className="flex items-center justify-between">
        <span className="text-muted-foreground font-mono text-xs">#{position}</span>
        <StatusOf labels={workStatus} value={work.status} />
      </div>
      <div className="text-muted-foreground line-clamp-2 text-xs">
        {work.description || work.platform_id}
      </div>
      {work.error ? (
        <p className="text-status-failed line-clamp-3 text-xs">{work.error}</p>
      ) : (
        <Tags tags={work.tags} max={6} />
      )}
    </>
  );
  const className = cn(
    "flex min-h-36 flex-col gap-2 rounded-lg border bg-card p-3 transition-colors",
    work.kind === "video" && "hover:border-signal/60",
  );
  return work.kind === "video" ? (
    <Link to={`/works/${work.id}`} className={className}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}

function QrCode({ creatorId }: { creatorId: string }) {
  const [missing, setMissing] = useState(false);
  return missing ? (
    <div className="bg-muted text-muted-foreground flex h-32 items-center justify-center gap-2 rounded-md text-xs">
      <ImageIcon className="size-4" /> 名单中没有二维码
    </div>
  ) : (
    <img
      src={`/api/creators/${creatorId}/qrcode`}
      alt="二维码"
      className="mx-auto h-32 rounded-md border bg-white object-contain p-1"
      onError={() => setMissing(true)}
    />
  );
}

/** 执行程序回传的事件，按发生时间排列。 */
export function History({ events }: { events: Schemas["EventView"][] }) {
  if (!events.length) return <Notice>还没有收到事件</Notice>;
  return (
    <ol className="divide-y">
      {events.map((event, index) => {
        const payload = event.payload as Record<string, unknown>;
        return (
          <li
            key={index}
            className="grid grid-cols-[150px_minmax(0,1fr)] gap-4 px-4 py-2.5 text-sm"
          >
            <span className="text-muted-foreground font-mono text-xs">
              {dateTime(String(payload.occurred_at))}
            </span>
            <span className="space-x-2">
              <Status
                label={describe(payload)}
                tone={
                  payload.status === "failed" || payload.type === "download_failed"
                    ? "failed"
                    : "idle"
                }
              />
              <span className="text-muted-foreground font-mono text-xs">
                第 {String(payload.run)} 轮
              </span>
              {event.executor_id && <ExecutorName id={event.executor_id} />}
              {typeof payload.reason === "string" && (
                <span className="text-xs">{payload.reason}</span>
              )}
              {typeof payload.error === "string" && (
                <span className="text-status-failed text-xs">{payload.error}</span>
              )}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function describe(payload: Record<string, unknown>): string {
  switch (payload.type) {
    case "accepted":
      return "已受理";
    case "crawled":
      return `爬取结束：${conclusions[payload.conclusion as Schemas["CrawlConclusion"]]}`;
    case "download_failed":
      return "下载失败";
    case "uploaded":
      return `视频已上传：${bytes(Number(payload.size))}`;
    case "job":
      return `分析任务：${jobStatus[payload.status as Schemas["JobStatus"]]?.[0] ?? payload.status}`;
    case "stage": {
      const name = stageNames[String(payload.stage)] ?? payload.stage;
      const part = typeof payload.segment === "number" ? ` 第 ${payload.segment + 1} 段` : "";
      const status = stageStatus[payload.status as Schemas["StageStatus"]]?.[0] ?? payload.status;
      return `${name}${part}：${status}`;
    }
    default:
      return String(payload.type);
  }
}
