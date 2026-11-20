import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useLiveUpdates } from "@/api/live";

/** 只记录回调，由测试手动触发连接事件。 */
class FakeEventSource {
  static CLOSED = 2;
  static last: FakeEventSource;
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor() {
    FakeEventSource.last = this;
  }
  addEventListener() {}
  close() {}
}

describe("useLiveUpdates", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("首次连上不刷新，重连后刷新一次", () => {
    vi.useFakeTimers();
    vi.stubGlobal("EventSource", FakeEventSource);
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    renderHook(() => useLiveUpdates(), { wrapper });
    const source = FakeEventSource.last;

    act(() => source.onopen?.());
    vi.runAllTimers();
    expect(invalidate).not.toHaveBeenCalled();

    act(() => {
      source.onerror?.();
      source.onopen?.();
    });
    vi.runAllTimers();
    expect(invalidate).toHaveBeenCalledOnce();
  });
});
