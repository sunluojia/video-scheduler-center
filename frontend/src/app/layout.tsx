import { Bot, Boxes, ChartColumn, Fingerprint, Layers, Network, Radio } from "lucide-react";
import { Link, NavLink, Outlet } from "react-router";
import { useIdentities, useProxyAlerts } from "@/api/alerts";
import { $api } from "@/api/client";
import { useLiveUpdates } from "@/api/live";
import { executorKinds } from "@/lib/status";
import { cn } from "@/lib/utils";
import { useLive } from "@/stores/live";

const NAV = [
  { to: "/batches", label: "批次工作台", icon: Layers },
  { to: "/stats", label: "数据看板", icon: ChartColumn },
  { to: "/executors", label: "执行程序", icon: Boxes },
  { to: "/proxies", label: "代理", icon: Network, badge: ProxyAlertCount },
  { to: "/identities", label: "账号池", icon: Fingerprint, badge: IdentityAlertCount },
  { to: "/ai", label: "AI 配置", icon: Bot },
];

const CONNECTION = {
  open: ["实时更新", "bg-status-done"],
  connecting: ["正在连接", "bg-status-attention"],
  closed: ["已断开", "bg-status-failed"],
} as const;

export function Layout() {
  useLiveUpdates();
  const connection = useLive((state) => state.connection);
  const [label, dot] = CONNECTION[connection];
  return (
    <div className="flex min-h-screen">
      <aside className="bg-sidebar text-sidebar-foreground sticky top-0 flex h-dvh w-14 shrink-0 flex-col sm:w-52">
        <div className="border-sidebar-border flex items-center justify-center gap-2.5 border-b px-2 py-5 sm:justify-start sm:px-5">
          <span className="bg-signal text-signal-foreground grid size-7 place-items-center rounded-md font-mono text-sm font-bold">
            调
          </span>
          <div className="sr-only leading-tight sm:not-sr-only">
            <div className="text-sm font-semibold text-white">调度中心</div>
            <div className="text-sidebar-muted font-mono text-[10px] tracking-[0.2em]">
              DISPATCH
            </div>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 px-1.5 py-4 sm:px-3">
          {NAV.map(({ to, label: text, icon: Icon, badge: Badge }) => (
            <NavLink
              key={to}
              to={to}
              title={text}
              className={({ isActive }) =>
                cn(
                  "relative flex items-center justify-center gap-3 rounded-md px-2 py-2 text-sm transition-colors hover:bg-white/5 hover:text-white sm:justify-start sm:px-3",
                  isActive &&
                    "before:bg-signal bg-white/[0.07] text-white before:absolute before:inset-y-2 before:-left-1.5 before:w-0.5 before:rounded-full sm:before:-left-3",
                )
              }
            >
              <Icon aria-hidden="true" className="size-4" />
              <span className="sr-only sm:not-sr-only">{text}</span>
              {Badge && <Badge />}
            </NavLink>
          ))}
        </nav>
        <ExecutorHealth />
        <div
          title={label}
          className="border-sidebar-border text-sidebar-muted flex items-center justify-center gap-2 border-t px-2 py-4 text-xs sm:justify-start sm:px-5"
        >
          <Radio aria-hidden="true" className="size-3.5" />
          <span className="sr-only sm:not-sr-only">{label}</span>
          <span className={cn("size-1.5 rounded-full sm:ml-auto", dot)} />
        </div>
      </aside>
      <main className="workspace min-w-0 flex-1">
        <div className="mx-auto max-w-[1680px] space-y-6 px-3 py-6 sm:px-6">
          <Outlet />
        </div>
      </main>
    </div>
  );
}

/** 有需要人处理的问题时，在导航上标出条数。 */
function AlertCount({ count }: { count: number | undefined }) {
  return count ? (
    <span className="bg-status-attention absolute top-0 right-0 rounded-full px-1 font-mono text-[10px] leading-4 font-semibold text-black sm:static sm:ml-auto sm:px-1.5">
      {count}
    </span>
  ) : null;
}

function ProxyAlertCount() {
  return <AlertCount count={useProxyAlerts().data?.length} />;
}

function IdentityAlertCount() {
  return <AlertCount count={useIdentities().data?.alerts.length} />;
}

/** 侧栏底部的执行程序在线情况，一眼看出爬取和分析能不能跑。 */
function ExecutorHealth() {
  const { data } = $api.useQuery("get", "/api/executors", {}, { refetchInterval: 5_000 });
  return (
    <Link
      to="/executors"
      className="border-sidebar-border mx-3 mb-3 hidden space-y-2 rounded-md border px-3 py-2.5 text-xs transition-colors hover:bg-white/5 sm:block"
    >
      {(["crawler", "analysis"] as const).map((kind) => {
        const active =
          data?.filter((item) => item.kind === kind && item.withdrawn_at === null) ?? [];
        const online = active.filter((item) => item.online).length;
        const dot = !active.length
          ? "bg-sidebar-muted"
          : online === active.length
            ? "bg-status-done"
            : online > 0
              ? "bg-status-attention"
              : "bg-status-failed";
        return (
          <div key={kind} className="flex items-center gap-2">
            <span aria-hidden="true" className={cn("size-1.5 rounded-full", dot)} />
            {executorKinds[kind]}
            <span className="text-sidebar-muted ml-auto font-mono">
              {data ? `${online}/${active.length} 在线` : "—"}
            </span>
          </div>
        );
      })}
    </Link>
  );
}
