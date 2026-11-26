import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentType } from "react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { RouteErrorBoundary } from "@/app/route-error-boundary";
import { createRouteRecovery } from "@/app/route-recovery";

const activeApps = new Set<() => void>();
const target = "/reports?range=7#chart";

function openApp(
  reload: (path?: string) => void,
  loadReports: () => Promise<{ Component: ComponentType }>,
  url = "/",
) {
  const recovery = createRouteRecovery(reload);
  const router = createMemoryRouter(
    [
      {
        element: <Outlet />,
        errorElement: <RouteErrorBoundary recovery={recovery} />,
        hydrateFallbackElement: <div />,
        children: [
          { index: true, element: <h1>首页</h1> },
          { path: "reports", lazy: loadReports },
        ],
      },
    ],
    { initialEntries: [url] },
  );
  const disconnect = recovery.connect(router);
  const view = render(<RouterProvider router={router} />);
  const close = () => {
    activeApps.delete(close);
    view.unmount();
    disconnect();
    router.dispose();
  };
  activeApps.add(close);
  return { router, close };
}

function failToFetchChunk() {
  return new TypeError("Failed to fetch dynamically imported module: /assets/reports-old.js");
}

function notifyVite(error: Error) {
  window.dispatchEvent(
    Object.assign(new Event("vite:preloadError", { cancelable: true }), { payload: error }),
  );
}

afterEach(() => {
  for (const close of activeApps) close();
  cleanup();
  sessionStorage.clear();
});

it("silently reloads the pending destination when Vite reports a stale chunk", async () => {
  const reload = vi.fn();
  const { router } = openApp(reload, async () => {
    const error = failToFetchChunk();
    // Vite emits the event after an asynchronous import or preload rejects.
    await Promise.resolve();
    notifyVite(error);
    expect(reload).toHaveBeenCalledExactlyOnceWith(target);
    throw error;
  });

  await act(() => router.navigate(target));

  expect(reload).toHaveBeenCalledExactlyOnceWith(target);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it.each([
  failToFetchChunk(),
  new TypeError("Importing a module script failed."),
  new TypeError("error loading dynamically imported module: /assets/reports-old.js"),
  Object.assign(new Error("Could not fetch the page"), { name: "ChunkLoadError" }),
])("recovers when React Router's lazy import rejects without a Vite event (%s)", async (error) => {
  const reload = vi.fn();
  const { router } = openApp(reload, () => Promise.reject(error));

  await act(() => router.navigate(target));

  expect(reload).toHaveBeenCalledExactlyOnceWith(target);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("shows a Chinese error page after the one automatic reload has already failed", async () => {
  const reload = vi.fn();
  const first = openApp(reload, () => Promise.reject(failToFetchChunk()));
  await act(() => first.router.navigate(target));
  first.close();

  openApp(
    reload,
    async () => {
      await Promise.resolve();
      const error = failToFetchChunk();
      notifyVite(error);
      throw error;
    },
    target,
  );

  expect(await screen.findByRole("heading", { name: "页面加载失败" })).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent("暂时无法加载页面资源，请检查网络后重试。");
  expect(reload).toHaveBeenCalledExactlyOnceWith(target);

  const user = userEvent.setup();
  await user.tab();
  expect(screen.getByRole("button", { name: "重新加载" })).toHaveFocus();
  await user.keyboard("{Enter}");
  expect(reload).toHaveBeenNthCalledWith(2);
});

it("allows recovery again once the refreshed page loads successfully", async () => {
  const reload = vi.fn();
  const first = openApp(reload, () => Promise.reject(failToFetchChunk()));
  await act(() => first.router.navigate(target));
  first.close();

  const { router } = openApp(reload, async () => ({ Component: () => <h1>数据看板</h1> }), target);
  expect(await screen.findByRole("heading", { name: "数据看板" })).toBeVisible();
  expect(reload).toHaveBeenCalledTimes(1);

  notifyVite(failToFetchChunk());
  expect(reload).toHaveBeenCalledTimes(2);
  expect(router.state.errors).toBeNull();
});

it("shows a friendly error without auto-reloading for unrelated exceptions", async () => {
  const reload = vi.fn();
  const { router } = openApp(reload, () => Promise.reject(new Error("unexpected exception")));

  await act(() => router.navigate(target));

  expect(screen.getByRole("heading", { name: "页面加载失败" })).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent("页面遇到问题，请重新加载后重试。");
  expect(reload).not.toHaveBeenCalled();
});
