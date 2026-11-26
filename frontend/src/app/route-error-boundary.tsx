import { useRouteError } from "react-router";
import { isChunkLoadError, type RouteRecovery } from "@/app/route-recovery";
import { Button } from "@/components/ui/button";

export function RouteErrorBoundary({ recovery }: { recovery: RouteRecovery }) {
  const chunkError = isChunkLoadError(useRouteError());
  if (chunkError && recovery.isReloading()) {
    return <div className="workspace min-h-screen" />;
  }

  return (
    <main className="workspace grid min-h-screen place-items-center px-6">
      <section className="w-full max-w-md space-y-4" aria-labelledby="route-error-title">
        <h1 id="route-error-title" className="text-xl font-semibold">
          页面加载失败
        </h1>
        <p className="text-muted-foreground text-sm" role="alert">
          {chunkError
            ? "暂时无法加载页面资源，请检查网络后重试。"
            : "页面遇到问题，请重新加载后重试。"}
        </p>
        <Button type="button" onClick={() => recovery.reload()}>
          重新加载
        </Button>
      </section>
    </main>
  );
}
