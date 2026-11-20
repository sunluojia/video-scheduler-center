import { $api, type Schemas } from "@/api/client";
import { QueryState } from "@/components/page";
import { Progress } from "@/components/ui/progress";
import { bytes } from "@/lib/format";

const stages = {
  preparing: "准备下载",
  downloading: "下载中",
  uploading: "下载完成，正在上传",
};

export function DownloadProgress() {
  const query = $api.useQuery(
    "get",
    "/api/executors/crawler/download-progress",
    {},
    {
      refetchInterval: 2_000,
      retry: false,
    },
  );
  const online = query.isError ? undefined : query.data;
  const rates =
    online?.flatMap(({ download_bytes_per_second: rate }) => (rate == null ? [] : [rate])) ?? [];
  const total = rates.reduce((sum, rate) => sum + rate, 0);
  const speed = online && (rates.length > 0 || online.length === 0) ? `${bytes(total)}/s` : "—";
  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h4 className="text-sm font-medium">下载进度</h4>
        <div
          className="text-muted-foreground text-xs"
          title="在线爬取设备最近 5 秒的合计视频下载速度"
        >
          总下载速度 <strong className="text-foreground font-mono tabular-nums">{speed}</strong>
          {online && rates.length < online.length && (
            <span className="ml-2">
              {rates.length}/{online.length} 台设备已上报
            </span>
          )}
        </div>
      </div>
      <QueryState query={query} rows={1}>
        {(devices) => {
          const transfers = devices.flatMap(({ instance_id, downloads }) =>
            (downloads ?? []).map((download) => ({ instanceId: instance_id, download })),
          );
          const unavailable = devices.filter((device) => device.downloads === null).length;
          return (
            <>
              {transfers.length > 0 && (
                <ul className="max-h-60 divide-y overflow-y-auto">
                  {transfers.map(({ instanceId, download }) => (
                    <VideoProgress
                      key={`${instanceId}/${download.task_id}`}
                      instanceId={instanceId}
                      download={download}
                    />
                  ))}
                </ul>
              )}
              {devices.length === 0 && (
                <p className="text-muted-foreground text-xs">暂无在线爬取设备</p>
              )}
              {transfers.length === 0 && devices.length > unavailable && (
                <p className="text-muted-foreground text-xs">暂无进行中的下载</p>
              )}
              {unavailable > 0 && (
                <p className="text-muted-foreground text-xs">
                  {unavailable} 台爬取设备暂时未提供下载进度
                </p>
              )}
            </>
          );
        }}
      </QueryState>
    </section>
  );
}

function VideoProgress({
  instanceId,
  download,
}: {
  instanceId: string;
  download: Schemas["DownloadTransfer"];
}) {
  const { work_id, stage, received_bytes, total_bytes } = download;
  const percent = total_bytes
    ? Math.min(100, Math.floor((received_bytes / total_bytes) * 100))
    : null;
  return (
    <li className="space-y-1.5 py-2">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="min-w-0 truncate font-mono" title={`视频 ${work_id}`}>
          视频 {work_id}
        </span>
        <span className="text-muted-foreground truncate" title={instanceId}>
          {instanceId}
        </span>
      </div>
      <Progress className="h-1.5" aria-label={`视频 ${work_id} 下载进度`} value={percent} />
      <div className="text-muted-foreground flex flex-wrap justify-between gap-x-2 text-xs">
        <span>{stages[stage]}</span>
        {stage !== "preparing" && (
          <span className="font-mono tabular-nums">
            {bytes(received_bytes)}
            {total_bytes ? ` / ${bytes(total_bytes)} · ${percent}%` : " · 总大小未知"}
          </span>
        )}
      </div>
    </li>
  );
}
