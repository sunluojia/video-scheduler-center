import { $api, type Schemas } from "@/api/client";
import { QueryState } from "@/components/page";
import { Progress } from "@/components/ui/progress";

type ActiveCrawl = { instanceId: string; task: Schemas["CrawlProgress"] };

export function CrawlProgress() {
  const query = $api.useQuery(
    "get",
    "/api/executors/crawler/crawl-progress",
    {},
    { refetchInterval: 2_000, retry: false },
  );
  return (
    <QueryState query={query} rows={2}>
      {(devices) => {
        const tasks = devices.flatMap(({ instance_id, crawls }) =>
          (crawls ?? []).map((task) => ({ instanceId: instance_id, task })),
        );
        const missing = devices.filter((device) => device.crawls === null).length;
        const available = devices.length === 0 || missing < devices.length;
        return (
          <>
            <CrawlStage
              title="达人信息获取"
              tasks={tasks.filter(({ task }) => task.stage !== "listing")}
              available={available}
            />
            <CrawlStage
              title="达人视频列表获取"
              tasks={tasks.filter(({ task }) => task.stage === "listing")}
              available={available}
            />
            {devices.length === 0 && (
              <p className="text-muted-foreground text-xs">暂无在线爬取设备</p>
            )}
            {missing > 0 && (
              <p className="text-muted-foreground text-xs">{missing} 台设备暂未上报采集进度</p>
            )}
          </>
        );
      }}
    </QueryState>
  );
}

function CrawlStage({
  title,
  tasks,
  available,
}: {
  title: string;
  tasks: ActiveCrawl[];
  available: boolean;
}) {
  return (
    <section aria-label={title} className="space-y-1 [&+section]:border-t [&+section]:pt-3">
      <div className="flex items-baseline justify-between gap-2">
        <h4 className="text-sm font-medium">{title}</h4>
        <span className="text-muted-foreground text-xs tabular-nums">
          {available ? `${tasks.length} 个任务` : "—"}
        </span>
      </div>
      {tasks.length > 0 && (
        <ul className="max-h-44 divide-y overflow-y-auto">
          {tasks.map(({ instanceId, task }) => (
            <li key={`${instanceId}/${task.task_id}`} className="space-y-1.5 py-2 text-xs">
              <div className="flex justify-between gap-2">
                <span className="min-w-0 truncate font-mono" title={`@${task.handle}`}>
                  @{task.handle}
                </span>
                <span className="text-muted-foreground truncate" title={instanceId}>
                  {instanceId}
                </span>
              </div>
              {task.stage === "listing" ? (
                <ListingProgress task={task} />
              ) : (
                <p className="text-muted-foreground">
                  {task.stage === "waiting_line" ? "申请获取信息线路" : "读取达人主页"}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ListingProgress({ task }: { task: Schemas["CrawlProgress"] }) {
  const found = Math.min(task.works_found, task.max_works);
  const count = `候选 ${found}/${task.max_works} 条`;
  return (
    <>
      <Progress
        className="h-1.5"
        aria-label={`@${task.handle} 作品列表获取进度`}
        aria-valuetext={count}
        value={found}
        max={task.max_works}
      />
      <div className="text-muted-foreground flex flex-wrap justify-between gap-x-2">
        <span>
          {task.pages_read > 0 ? `已读 ${task.pages_read} 页` : "等待读取作品列表"}
          {found === task.max_works && " · 确认最新作品"}
        </span>
        <span className="font-mono tabular-nums" title="目标作品数包含视频和图文">
          {count}
        </span>
      </div>
    </>
  );
}
