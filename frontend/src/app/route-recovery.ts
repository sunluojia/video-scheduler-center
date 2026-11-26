import { createPath, type DataRouter, type RouterState } from "react-router";

const RELOAD_KEY = "scheduler-center:route-reload";
const CHUNK_ERROR_MESSAGE =
  /failed to fetch dynamically imported module|importing a module script failed|error loading dynamically imported module|chunkloaderror|loading chunk .+ failed|unable to preload css for/i;

export function isChunkLoadError(error: unknown): boolean {
  return (
    error instanceof Error &&
    (error.name === "ChunkLoadError" || CHUNK_ERROR_MESSAGE.test(error.message))
  );
}

function reloadPage(path?: string) {
  if (path && path !== createPath(window.location)) {
    // 懒加载失败时地址栏可能还停在上一页，这时直接打开目标页。
    window.location.assign(window.location.origin + path);
  } else {
    window.location.reload();
  }
}

export function createRouteRecovery(reload = reloadPage) {
  let reloading = false;

  function recover(path?: string) {
    if (reloading) return;
    try {
      if (sessionStorage.getItem(RELOAD_KEY)) return;
      sessionStorage.setItem(RELOAD_KEY, "1");
    } catch {
      // 无法记录刷新时，交给错误页处理，避免重复自动刷新。
      return;
    }
    reloading = true;
    reload(path);
  }

  function connect(router: DataRouter) {
    function onPreloadError(event: Event & { payload?: unknown }) {
      if (isChunkLoadError(event.payload)) {
        const target = router.state.navigation.location;
        recover(target && createPath(target));
        // 保留 Vite 的异常，让路由在无法自动刷新时渲染错误页。
      }
    }

    function onRouteSettled(state: RouterState) {
      if (reloading || !state.initialized || state.navigation.state !== "idle") return;
      if (state.errors) {
        if (Object.values(state.errors).some(isChunkLoadError)) recover(createPath(state.location));
        return;
      }
      // 等页面资源成功加载后，再允许下一次自动刷新。
      try {
        sessionStorage.removeItem(RELOAD_KEY);
      } catch {
        // 浏览器可能禁止使用会话存储。
      }
    }

    window.addEventListener("vite:preloadError", onPreloadError);
    const unsubscribe = router.subscribe(onRouteSettled);
    onRouteSettled(router.state);
    return () => {
      unsubscribe();
      window.removeEventListener("vite:preloadError", onPreloadError);
    };
  }

  return { connect, reload, isReloading: () => reloading };
}

export type RouteRecovery = ReturnType<typeof createRouteRecovery>;
