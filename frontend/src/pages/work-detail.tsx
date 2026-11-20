import { Ban, Download, ExternalLink, RotateCcw, Sparkles } from "lucide-react";
import { Link, useParams } from "react-router";
import { $api, type AnalysisSchemas, type Schemas } from "@/api/client";
import { Confirm } from "@/components/confirm";
import { Facts, Notice, PageHeader, Panel, QueryState, Section, Tags } from "@/components/page";
import { Status, StatusOf } from "@/components/status";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { dateTime, duration } from "@/lib/format";
import { useDone } from "@/lib/refresh";
import { jobStatus, stageNames, stageStatus, taskStatus, workStatus } from "@/lib/status";
import { History } from "@/pages/creator-detail";

type Recognition = {
  tags: string[];
  summary: string;
  products: {
    name: string;
    brand: string;
    original_name: string;
    sizes: { value: string }[];
    evidence: string;
  }[];
  topic_translations: { topic: string; translation: string }[];
};
type Listening = { sounds: string[]; transcript: string };
type Frames = { frames: { index: number; at: number }[] };
type Segments = { segments: { index: number; start: number; end: number }[] };

const REDO = ["completed", "failed", "needs_review", "abandoned"];
const SOUNDS: Record<string, string> = {
  speech: "讲话",
  music: "音乐",
  ambient: "环境声",
  silence: "静音",
};
const OUTCOMES: Record<AnalysisSchemas["CallOutcome"], string> = {
  succeeded: "成功",
  failed: "失败",
  timed_out: "超时",
  lost: "中断，结果不明",
};

export function WorkDetailPage() {
  const workId = useParams().workId!;
  const done = useDone();
  const path = { params: { path: { work_id: workId } } };
  const detail = $api.useQuery("get", "/api/works/{work_id}", path);
  const redownload = $api.useMutation("post", "/api/works/{work_id}/redownload", {
    onSuccess: () => done("已重新发布下载任务"),
  });
  const retry = $api.useMutation("post", "/api/works/{work_id}/retry", {
    onSuccess: () => done("已补跑"),
  });
  const reanalyze = $api.useMutation("post", "/api/works/{work_id}/reanalyze", {
    onSuccess: () => done("已重新提交分析"),
  });
  const abandon = $api.useMutation("post", "/api/works/{work_id}/abandon", {
    onSuccess: () => done("已放弃这条视频"),
  });

  return (
    <QueryState query={detail} rows={8}>
      {({ work, creator, download, analysis, stages, history }) => {
        const result = analysis?.result as {
          recognition?: Recognition;
          listening?: Listening;
        } | null;
        const frames = stages.find((stage) => stage.stage === "extract_frames")
          ?.result as Frames | null;
        const audio = stages.find((stage) => stage.stage === "extract_audio")
          ?.result as Segments | null;
        const expired = !analysis?.artifacts_until
          ? false
          : new Date(analysis.artifacts_until) < new Date();
        return (
          <>
            <PageHeader
              eyebrow={
                <Link to={`/creators/${creator.id}`} className="hover:text-foreground">
                  {creator.name} / 作品 #{work.position}
                </Link>
              }
              title={work.description || `作品 ${work.platform_id}`}
              meta={
                <>
                  <StatusOf labels={workStatus} value={work.status} />
                  <a
                    href={work.url}
                    target="_blank"
                    rel="noreferrer"
                    className="hover:text-foreground inline-flex items-center gap-1 font-mono"
                  >
                    {work.platform_id} <ExternalLink className="size-3" />
                  </a>
                  {work.published_at && <span>发布于 {dateTime(work.published_at)}</span>}
                </>
              }
              actions={
                <>
                  {analysis && ["failed", "needs_review"].includes(analysis.status) && (
                    <Button
                      variant="outline"
                      onClick={() => retry.mutate(path)}
                      disabled={retry.isPending}
                    >
                      <RotateCcw /> 补跑
                    </Button>
                  )}
                  {REDO.includes(work.status) && analysis && (
                    <Button
                      variant="outline"
                      onClick={() => reanalyze.mutate(path)}
                      disabled={reanalyze.isPending}
                    >
                      <Sparkles /> 重新分析
                    </Button>
                  )}
                  {REDO.includes(work.status) && (
                    <Button
                      variant="outline"
                      onClick={() => redownload.mutate(path)}
                      disabled={redownload.isPending}
                    >
                      <Download /> 重新下载
                    </Button>
                  )}
                  {work.status === "needs_review" && (
                    <Confirm
                      title="放弃这条视频"
                      description="模型请求已发出但结果不明。放弃后这条视频算作失败，不再补跑，达人用其余视频汇总。"
                      action="放弃"
                      onConfirm={() => abandon.mutate(path)}
                    >
                      <Button variant="ghost" className="text-status-failed">
                        <Ban /> 放弃
                      </Button>
                    </Confirm>
                  )}
                </>
              }
            />
            {work.error && (
              <div className="rise border-status-failed/30 bg-status-failed/5 text-status-failed rounded-md border px-4 py-2.5 text-sm">
                {work.error}
              </div>
            )}
            <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_380px]">
              <div className="space-y-8">
                <Section title="处理经过">
                  <Panel>
                    <Timeline download={download} analysis={analysis} stages={stages} />
                  </Panel>
                </Section>
                {result?.recognition && <RecognitionResult recognition={result.recognition} />}
                <Section title="讲话文本">
                  <Panel className="p-5">
                    {result?.listening ? (
                      <div className="space-y-2">
                        <div className="text-muted-foreground text-xs">
                          声音：
                          {result.listening.sounds
                            .map((sound) => SOUNDS[sound] ?? sound)
                            .join("、") || "—"}
                        </div>
                        <p className="text-sm leading-7 whitespace-pre-wrap">
                          {result.listening.transcript || "没有讲话"}
                        </p>
                      </div>
                    ) : (
                      <p className="text-muted-foreground text-sm">听音完成后显示。</p>
                    )}
                  </Panel>
                </Section>
              </div>
              <aside className="space-y-8">
                <Section
                  title="抽帧图片"
                  extra={
                    analysis?.artifacts_until && (
                      <span className="text-muted-foreground text-xs">
                        保留到 {dateTime(analysis.artifacts_until)}
                      </span>
                    )
                  }
                >
                  <Panel className="p-3">
                    {!frames?.frames.length ? (
                      <Notice>抽帧完成后显示</Notice>
                    ) : expired ? (
                      <Notice>图片已过保留期</Notice>
                    ) : (
                      <div className="grid grid-cols-2 gap-2">
                        {frames.frames.map((frame) => (
                          <figure key={frame.index} className="space-y-1">
                            <img
                              src={`/api/works/${workId}/frames/${frame.index}`}
                              alt={`第 ${frame.index + 1} 张`}
                              loading="lazy"
                              className="bg-muted aspect-[9/16] w-full rounded-md object-cover"
                            />
                            <figcaption className="text-muted-foreground font-mono text-[11px]">
                              #{frame.index} · {frame.at.toFixed(1)}s
                            </figcaption>
                          </figure>
                        ))}
                      </div>
                    )}
                  </Panel>
                </Section>
                {!!audio?.segments.length && !expired && (
                  <Section title="音频段">
                    <Panel className="space-y-2 p-3">
                      {audio.segments.map((segment) => (
                        <div key={segment.index} className="space-y-1">
                          <div className="text-muted-foreground font-mono text-[11px]">
                            #{segment.index} · {segment.start}s – {segment.end}s
                          </div>
                          <audio
                            controls
                            preload="none"
                            className="h-8 w-full"
                            src={`/api/works/${workId}/audio/${segment.index}`}
                          />
                        </div>
                      ))}
                    </Panel>
                  </Section>
                )}
                {analysis && <ModelCalls workId={workId} />}
              </aside>
            </div>
            <Section title="事件记录">
              <Panel>
                <History events={history} />
              </Panel>
            </Section>
          </>
        );
      }}
    </QueryState>
  );
}

function Timeline({
  download,
  analysis,
  stages,
}: {
  download: Schemas["CrawlerTaskView"] | null;
  analysis: Schemas["JobView"] | null;
  stages: Schemas["StageView"][];
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead>环节</TableHead>
          <TableHead className="w-28">状态</TableHead>
          <TableHead className="w-24">耗时</TableHead>
          <TableHead>模型</TableHead>
          <TableHead>说明</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {download && (
          <TableRow>
            <TableCell>
              爬取程序下载{" "}
              <span className="text-muted-foreground font-mono text-xs">第 {download.run} 轮</span>
            </TableCell>
            <TableCell>
              <StatusOf labels={taskStatus} value={download.status} />
            </TableCell>
            <TableCell className="font-mono text-xs">
              {duration(download.published_at, download.finished_at)}
            </TableCell>
            <TableCell />
            <TableCell className="text-status-failed text-xs">{download.error}</TableCell>
          </TableRow>
        )}
        {analysis && (
          <TableRow>
            <TableCell>
              分析任务{" "}
              <span className="text-muted-foreground font-mono text-xs">第 {analysis.run} 轮</span>
            </TableCell>
            <TableCell>
              <StatusOf labels={jobStatus} value={analysis.status} />
            </TableCell>
            <TableCell className="font-mono text-xs">
              {duration(analysis.submitted_at, analysis.finished_at)}
            </TableCell>
            <TableCell />
            <TableCell className="text-xs">
              {analysis.reused_from ? (
                `采用了任务 ${analysis.reused_from} 的结果`
              ) : (
                <span className="text-status-failed">{analysis.error}</span>
              )}
            </TableCell>
          </TableRow>
        )}
        {stages.map((stage) => (
          <TableRow key={`${stage.stage}-${stage.segment}`}>
            <TableCell className="pl-8">
              {stageNames[stage.stage] ?? stage.stage}
              {stage.segment !== null && (
                <span className="text-muted-foreground font-mono text-xs">
                  {" "}
                  第 {stage.segment + 1} 段
                </span>
              )}
              {stage.attempt > 1 && (
                <span className="text-muted-foreground font-mono text-xs">
                  {" "}
                  · 第 {stage.attempt} 次
                </span>
              )}
            </TableCell>
            <TableCell>
              <StatusOf labels={stageStatus} value={stage.status} />
            </TableCell>
            <TableCell className="font-mono text-xs">
              {stage.started_at && stage.status !== "started"
                ? duration(stage.started_at, stage.occurred_at)
                : stage.started_at
                  ? duration(stage.started_at)
                  : "—"}
            </TableCell>
            <TableCell className="font-mono text-xs">
              {stage.model}
              {stage.model && (
                <span className="text-muted-foreground">
                  {" "}
                  · {stage.prompt_version ? `v${stage.prompt_version}` : "内置提示词"}
                </span>
              )}
            </TableCell>
            <TableCell className="text-xs">
              {stage.error && <span className="text-status-failed">{stage.error}</span>}
              {stage.retry_at && (
                <span className="text-muted-foreground"> · {dateTime(stage.retry_at)} 重试</span>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function RecognitionResult({ recognition }: { recognition: Recognition }) {
  return (
    <Section title="识别结果">
      <Panel className="space-y-5 p-5">
        <Tags tags={recognition.tags} />
        {recognition.summary && <p className="text-sm leading-7">{recognition.summary}</p>}
        {!!recognition.products.length && (
          <div className="space-y-2">
            <div className="text-muted-foreground text-xs">商品</div>
            {recognition.products.map((product, index) => (
              <div key={index} className="rounded-md border px-3 py-2 text-sm">
                <div className="font-medium">
                  {product.name}
                  {product.brand && (
                    <span className="text-muted-foreground"> · {product.brand}</span>
                  )}
                  {product.sizes.map((size) => (
                    <span
                      key={size.value}
                      className="bg-accent ml-2 rounded-sm px-1.5 font-mono text-xs"
                    >
                      {size.value}
                    </span>
                  ))}
                </div>
                {product.original_name && (
                  <div className="text-muted-foreground text-xs">{product.original_name}</div>
                )}
                <div className="text-muted-foreground text-xs">依据：{product.evidence}</div>
              </div>
            ))}
          </div>
        )}
        {!!recognition.topic_translations.length && (
          <Facts
            items={recognition.topic_translations.map((item) => [item.topic, item.translation])}
          />
        )}
      </Panel>
    </Section>
  );
}

/** 分析服务中的模型调用记录，包括之前轮次。 */
function ModelCalls({ workId }: { workId: string }) {
  const query = $api.useQuery("get", "/api/works/{work_id}/analysis", {
    params: { path: { work_id: workId } },
  });
  return (
    <Section title="模型调用">
      <Panel>
        <QueryState
          query={query}
          isEmpty={(data) => !(data as AnalysisSchemas["JobDetail"]).ai_calls.length}
          empty="还没有模型调用"
        >
          {(data) => (
            <ol className="divide-y">
              {(data as AnalysisSchemas["JobDetail"]).ai_calls.map((call, index) => (
                <li key={index} className="space-y-1 px-4 py-2.5 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">{stageNames[call.kind] ?? call.kind}</span>
                    <Status
                      label={call.outcome ? OUTCOMES[call.outcome] : "进行中"}
                      tone={
                        !call.outcome ? "active" : call.outcome === "succeeded" ? "done" : "failed"
                      }
                    />
                  </div>
                  <div className="text-muted-foreground font-mono">
                    {call.model} · {call.prompt_version ? `v${call.prompt_version}` : "内置提示词"}{" "}
                    · {duration(call.started_at, call.finished_at)}
                  </div>
                  {call.error && <div className="text-status-failed">{call.error}</div>}
                </li>
              ))}
            </ol>
          )}
        </QueryState>
      </Panel>
    </Section>
  );
}
