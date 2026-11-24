import type { Schemas } from "@/api/client";
import { Panel } from "@/components/page";
import { Status } from "@/components/status";
import { dateTime } from "@/lib/format";
import { crawlerAttemptStatus, jobStatus, stageNames, stageStatus, type Tone } from "@/lib/status";

type Attempt = Schemas["CrawlerAttemptView"] | Schemas["AnalysisAttemptView"];

const statuses: Record<string, [string, Tone]> = {
  ...jobStatus,
  ...stageStatus,
  ...crawlerAttemptStatus,
};

export function ExecutorName({ id }: { id: string | null | undefined }) {
  return id ? (
    <span className="font-mono wrap-anywhere">{id}</span>
  ) : (
    <span className="text-muted-foreground">历史未记录</span>
  );
}

export function ExecutorAttribution({
  last,
  result,
}: {
  last: string | null | undefined;
  result: string | null | undefined;
}) {
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-1 whitespace-normal">
      <dt className="text-muted-foreground">最近</dt>
      <dd>
        <ExecutorName id={last} />
      </dd>
      <dt className="text-muted-foreground">结果</dt>
      <dd>
        <ExecutorName id={result} />
      </dd>
    </dl>
  );
}

export function AttemptHistory({
  title,
  attempts,
  defaultStage,
}: {
  title: string;
  attempts: Attempt[] | undefined;
  defaultStage: string;
}) {
  return (
    <Panel>
      <h3 className="border-b px-4 py-2.5 text-sm font-medium">{title}</h3>
      {attempts?.length ? (
        <ul className="divide-y">
          {attempts.map((attempt) => {
            const analysis = "job_id" in attempt;
            const stage =
              analysis && attempt.stage !== "job"
                ? (stageNames[attempt.stage] ?? attempt.stage)
                : defaultStage;
            const started = analysis ? attempt.started_at : attempt.claimed_at;
            const [label, tone] = statuses[attempt.status] ?? [attempt.status, "muted"];
            return (
              <li key={attempt.attempt_id} className="space-y-1.5 px-4 py-3 text-sm">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="font-medium">{stage}</span>
                  <span className="text-muted-foreground text-xs">
                    第 {attempt.run} 轮 · 第 {attempt.attempt_no} 次
                    {analysis &&
                      attempt.retry_generation > 0 &&
                      ` · 补跑 ${attempt.retry_generation}`}
                    {analysis && attempt.segment !== null && ` · 第 ${attempt.segment + 1} 段`}
                  </span>
                  <Status label={label} tone={tone} />
                </div>
                <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
                  <ExecutorName id={attempt.executor_id} />
                  <span className="text-muted-foreground">
                    {dateTime(started)} → {dateTime(attempt.ended_at)}
                  </span>
                  <span className="text-muted-foreground font-mono wrap-anywhere" title="尝试 ID">
                    {attempt.attempt_id}
                  </span>
                </div>
                {attempt.error && (
                  <p className="text-muted-foreground text-xs wrap-anywhere">{attempt.error}</p>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-muted-foreground px-4 py-3 text-sm">历史未记录</p>
      )}
    </Panel>
  );
}
