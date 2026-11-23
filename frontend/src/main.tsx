import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, redirect, RouterProvider } from "react-router";
import { toast } from "sonner";
import { errorText } from "@/api/client";
import { Layout } from "@/app/layout";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import "@/index.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 5_000 } },
  // 操作失败统一提示原因；查询失败在各自的位置显示。
  mutationCache: new MutationCache({ onError: (error) => toast.error(errorText(error)) }),
});

// 页面按路由拆包，打开时再加载
const router = createBrowserRouter([
  {
    element: <Layout />,
    // 首次打开时页面代码还在加载，先铺上工作区底色，避免白屏闪烁
    hydrateFallbackElement: <div className="workspace min-h-screen" />,
    children: [
      { index: true, loader: () => redirect("/batches") },
      {
        // 批次工作台：左侧批次列表，右侧是选中的批次
        path: "batches",
        lazy: () => import("@/pages/batches").then((m) => ({ Component: m.BatchesPage })),
        children: [
          {
            index: true,
            lazy: () => import("@/pages/batches").then((m) => ({ Component: m.FirstBatch })),
          },
          {
            path: ":batchId",
            lazy: () => import("@/pages/batch-detail").then((m) => ({ Component: m.BatchPanel })),
          },
        ],
      },
      {
        path: "batches/new",
        lazy: () => import("@/pages/new-batch").then((m) => ({ Component: m.NewBatchPage })),
      },
      {
        path: "creators/:creatorId",
        lazy: () =>
          import("@/pages/creator-detail").then((m) => ({ Component: m.CreatorDetailPage })),
      },
      {
        path: "works/:workId",
        lazy: () => import("@/pages/work-detail").then((m) => ({ Component: m.WorkDetailPage })),
      },
      {
        path: "stats",
        lazy: () => import("@/pages/stats").then((m) => ({ Component: m.StatsPage })),
      },
      {
        path: "executors",
        lazy: () => import("@/pages/executors").then((m) => ({ Component: m.ExecutorsPage })),
      },
      {
        path: "proxies",
        lazy: () => import("@/pages/proxies").then((m) => ({ Component: m.ProxiesPage })),
      },
      {
        path: "identities",
        lazy: () => import("@/pages/identities").then((m) => ({ Component: m.IdentitiesPage })),
      },
      {
        path: "ai",
        lazy: () => import("@/pages/ai-config").then((m) => ({ Component: m.AiConfigPage })),
      },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={200}>
        <RouterProvider router={router} />
        <Toaster position="top-right" richColors />
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
);
