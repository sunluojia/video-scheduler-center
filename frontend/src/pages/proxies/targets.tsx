import { X } from "lucide-react";
import { useState } from "react";
import { $api, type Schemas } from "@/api/client";
import { QueryState } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { depths, proxyName, useFolders } from "./data";
import { Segmented } from "./shared";

type Target = Schemas["RuleTargetView"];
type TargetRef = Pick<Target, "kind" | "id" | "label">;
type Targets = Target[] | null;

const keyOf = (target: TargetRef) => `${target.kind}:${target.id}`;

export function PoolTargets({
  use,
  mode,
  targets,
  onChange,
}: {
  use: Schemas["Use"];
  mode: Schemas["PickMode"];
  targets: Targets;
  onChange: (value: Targets) => void;
}) {
  const folders = useFolders();
  const [tab, setTab] = useState<Target["kind"]>("folder");
  const selected = targets ?? [];
  const chosen = new Set(selected.map(keyOf));
  const toggle = (target: TargetRef, checked: boolean) =>
    onChange(
      checked
        ? [...selected, { ...target, weight: 1, priority: 1 }]
        : selected.filter((item) => keyOf(item) !== keyOf(target)),
    );
  const numberField = mode === "priority" ? "priority" : mode === "weighted" ? "weight" : null;
  const numberLabel = mode === "priority" ? "优先级" : "权重";
  const folderName = (id: string) => folders.data?.folders.find((folder) => folder.id === id)?.name;

  return (
    <fieldset className="min-w-0 space-y-2">
      <legend className="mb-1.5 text-sm font-medium">代理范围</legend>
      <Segmented
        value={targets === null ? "all" : "selected"}
        options={[
          ["all", "整个代理池"],
          ["selected", "指定分组和代理"],
        ]}
        onChange={(scope) => onChange(scope === "all" ? null : [])}
      />
      {targets !== null && (
        <div className="space-y-2.5 rounded-md border p-3">
          <Segmented
            value={tab}
            options={[
              ["folder", "分组"],
              ["proxy", "单个代理"],
            ]}
            onChange={setTab}
          />
          {tab === "folder" ? (
            <QueryState
              query={folders}
              rows={1}
              isEmpty={(data) => !data.folders.length}
              empty="还没有分组"
            >
              {({ folders }) => {
                const depth = depths(folders);
                return (
                  <div className="max-h-44 space-y-0.5 overflow-y-auto">
                    {folders.map((folder) => {
                      const target: TargetRef = {
                        kind: "folder",
                        id: folder.id,
                        label: folder.name,
                      };
                      return (
                        <Choice
                          key={folder.id}
                          label={folder.name}
                          depth={depth.get(folder.id)}
                          checked={chosen.has(keyOf(target))}
                          onChange={(checked) => toggle(target, checked)}
                        />
                      );
                    })}
                  </div>
                );
              }}
            </QueryState>
          ) : (
            <ProxyChoices use={use} chosen={chosen} onChange={toggle} />
          )}
          <p className="text-muted-foreground text-xs">
            分组涵盖子分组。单独选中的代理和子分组使用各自的设置。
          </p>
          {selected.length === 0 ? (
            <p className="text-muted-foreground text-xs">范围为空时，任务会等待线路。</p>
          ) : (
            <div className="space-y-1 border-t pt-2">
              <div className="text-muted-foreground flex justify-between pr-8 text-xs">
                <span>已选 {selected.length} 项</span>
                {numberField && <span>{numberLabel}</span>}
              </div>
              {selected.map((target) => {
                const label =
                  target.label ??
                  (target.kind === "folder" ? folderName(target.id) : null) ??
                  `已删除的${target.kind === "folder" ? "分组" : "代理"}`;
                return (
                  <div key={keyOf(target)} className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-sm" title={label}>
                      {label}
                    </span>
                    {numberField && (
                      <Input
                        type="number"
                        min={1}
                        max={2147483647}
                        required
                        aria-label={`${label}的${numberLabel}`}
                        className="h-8 w-24 font-mono"
                        value={target[numberField] || ""}
                        onChange={(event) => {
                          const value = event.target.valueAsNumber || 0;
                          onChange(
                            selected.map((item) =>
                              item === target ? { ...item, [numberField]: value } : item,
                            ),
                          );
                        }}
                      />
                    )}
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      aria-label={`移除 ${label}`}
                      onClick={() => toggle(target, false)}
                    >
                      <X />
                    </Button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </fieldset>
  );
}

const PAGE_SIZE = 8;

function ProxyChoices({
  use,
  chosen,
  onChange,
}: {
  use: Schemas["Use"];
  chosen: Set<string>;
  onChange: (target: TargetRef, checked: boolean) => void;
}) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const proxies = $api.useQuery("get", "/api/proxies", {
    params: { query: { use, q: query.trim() || undefined, page, size: PAGE_SIZE } },
  });
  const total = proxies.data?.total;
  return (
    <div className="space-y-1.5">
      <Input
        aria-label="搜索代理"
        placeholder="搜索代理名称、主机或出口 IP"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setPage(1);
        }}
      />
      <QueryState
        query={proxies}
        rows={1}
        empty="没有符合这个用途的代理"
        isEmpty={(data) => !data.items.length}
      >
        {(data) =>
          data.items.map((proxy) => {
            const label = proxyName(proxy);
            const target: TargetRef = { kind: "proxy", id: proxy.id, label };
            return (
              <Choice
                key={proxy.id}
                label={label}
                checked={chosen.has(keyOf(target))}
                onChange={(checked) => onChange(target, checked)}
              />
            );
          })
        }
      </QueryState>
      {(page > 1 || (total ?? 0) > PAGE_SIZE) && (
        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            size="xs"
            disabled={page === 1}
            onClick={() => setPage((current) => current - 1)}
          >
            上一页
          </Button>
          <Button
            type="button"
            variant="outline"
            size="xs"
            disabled={(total ?? 0) <= page * PAGE_SIZE}
            onClick={() => setPage((current) => current + 1)}
          >
            下一页
          </Button>
        </div>
      )}
    </div>
  );
}

function Choice({
  label,
  depth = 0,
  checked,
  onChange,
}: {
  label: string;
  depth?: number;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label
      className="hover:bg-muted flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-sm"
      style={{ marginInlineStart: depth * 14 }}
    >
      <Checkbox checked={checked} onCheckedChange={(value) => onChange(value === true)} />
      <span className="truncate" title={label}>
        {label}
      </span>
    </label>
  );
}
