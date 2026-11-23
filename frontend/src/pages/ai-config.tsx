import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Check, GitCompare, Pencil, Plus, Trash2, Undo2 } from "lucide-react";
import { Switch } from "radix-ui";
import { type ReactNode, useId, useState } from "react";
import { useForm } from "react-hook-form";
import { useSearchParams } from "react-router";
import { toast } from "sonner";
import { z } from "zod";
import { $ai, type AnalysisSchemas } from "@/api/client";
import { Confirm } from "@/components/confirm";
import { Field } from "@/components/field";
import { Notice, PageHeader, Panel, QueryState, Section } from "@/components/page";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { diffLines } from "@/lib/diff";
import { dateTime, percent } from "@/lib/format";
import { cacheRate, useRecentAiUsage } from "@/lib/usage";
import { cn } from "@/lib/utils";

type Kind = "listen" | "recognize" | "summarize";
type Provider = AnalysisSchemas["ProviderView"];
type Model = AnalysisSchemas["ModelView"];
const KINDS: [Kind, string, string][] = [
  ["listen", "听音", "逐段识别声音类型并转写讲话"],
  ["recognize", "识别", "结合画面和讲话给出标签、商品和摘要"],
  ["summarize", "汇总", "合并一位达人多条视频的标签"],
];

/** AI 配置保存在分析服务，后端原样转发；保存后刷新相关查询。 */
function useSaved(message: string) {
  const queryClient = useQueryClient();
  return () => {
    toast.success(message);
    void queryClient.invalidateQueries({
      predicate: (query) => String(query.queryKey[1]).startsWith("/api/ai/"),
    });
  };
}

export function AiConfigPage() {
  const [search, setSearch] = useSearchParams();
  const selected = search.get("tab");
  const tab =
    selected === "models" || selected === "providers" || selected === "prompts"
      ? selected
      : "routes";
  return (
    <>
      <PageHeader
        eyebrow="AI Configuration"
        title="AI 配置"
        meta="供应商、模型和提示词保存在分析服务，修改后对新开始的阶段生效。"
      />
      <Tabs
        value={tab}
        onValueChange={(value) => {
          const next = new URLSearchParams(search);
          if (value === "routes") next.delete("tab");
          else next.set("tab", value);
          setSearch(next, { replace: true });
        }}
        className="rise space-y-4"
      >
        <TabsList>
          <TabsTrigger value="routes">任务路由</TabsTrigger>
          <TabsTrigger value="models">模型</TabsTrigger>
          <TabsTrigger value="providers">供应商</TabsTrigger>
          <TabsTrigger value="prompts">提示词</TabsTrigger>
        </TabsList>
        <TabsContent value="routes">
          <Routes />
        </TabsContent>
        <TabsContent value="models">
          <Models />
        </TabsContent>
        <TabsContent value="providers">
          <Providers />
        </TabsContent>
        <TabsContent value="prompts">
          <Prompts />
        </TabsContent>
      </Tabs>
    </>
  );
}

function Routes() {
  const routes = $ai.useQuery("get", "/api/ai/routes");
  const models = $ai.useQuery("get", "/api/ai/models");
  return (
    <QueryState query={routes}>
      {(data) => (
        <div className="grid gap-4 lg:grid-cols-3">
          {KINDS.map(([kind, label, description]) => (
            <RouteCard
              key={kind}
              kind={kind}
              label={label}
              description={description}
              route={data.find((route) => route.kind === kind)}
              models={models.data ?? []}
            />
          ))}
        </div>
      )}
    </QueryState>
  );
}

function RouteCard({
  kind,
  label,
  description,
  route,
  models,
}: {
  kind: Kind;
  label: string;
  description: string;
  route?: AnalysisSchemas["RouteView"];
  models: Model[];
}) {
  const [modelId, setModelId] = useState(route?.model_id ?? "");
  const [share, setShare] = useState(route?.share ?? 1);
  const saved = useSaved(`已保存${label}的模型`);
  const save = $ai.useMutation("put", "/api/ai/routes/{kind}", { onSuccess: saved });
  const remove = $ai.useMutation("delete", "/api/ai/routes/{kind}", { onSuccess: saved });
  const path = { params: { path: { kind } } };
  return (
    <Panel className="space-y-4 p-5">
      <div>
        <div className="font-medium">{label}</div>
        <div className="text-muted-foreground text-xs">{description}</div>
      </div>
      <Field label="模型">
        <Select value={modelId} onValueChange={setModelId}>
          <SelectTrigger className="w-full">
            <SelectValue placeholder="未配置" />
          </SelectTrigger>
          <SelectContent>
            {models.map((model) => (
              <SelectItem key={model.id} value={model.id}>
                {model.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <Field label="目标份额">
        <Input
          type="number"
          min={1}
          value={share}
          onChange={(event) => setShare(Number(event.target.value))}
        />
      </Field>
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={!modelId || save.isPending}
          onClick={() => save.mutate({ ...path, body: { model_id: modelId, share } })}
        >
          <Check /> 保存
        </Button>
        {route && (
          <Button size="sm" variant="ghost" onClick={() => remove.mutate(path)}>
            移除
          </Button>
        )}
      </div>
    </Panel>
  );
}

const providerSchema = z.object({
  name: z.string().trim().min(1, "请填写名称"),
  provider: z.string().trim().min(1, "请填写供应商标识"),
  base_url: z.string().trim().optional(),
  credential_ref: z.string().trim().optional(),
});

function Providers() {
  const providers = $ai.useQuery("get", "/api/ai/providers");
  const saved = useSaved("已保存供应商");
  const remove = $ai.useMutation("delete", "/api/ai/providers/{provider_id}", { onSuccess: saved });
  return (
    <Section
      title="供应商"
      extra={
        <ProviderDialog
          trigger={
            <Button size="sm">
              <Plus /> 新增
            </Button>
          }
        />
      }
    >
      <Panel>
        <QueryState query={providers} isEmpty={(data) => !data.length} empty="还没有供应商">
          {(data) => (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>名称</TableHead>
                  <TableHead>供应商标识</TableHead>
                  <TableHead>接口地址</TableHead>
                  <TableHead>密钥所在的环境变量</TableHead>
                  <TableHead className="w-24" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.map((provider) => (
                  <TableRow key={provider.id}>
                    <TableCell className="font-medium">{provider.name}</TableCell>
                    <TableCell className="font-mono text-xs">{provider.provider}</TableCell>
                    <TableCell className="font-mono text-xs">{provider.base_url ?? "—"}</TableCell>
                    <TableCell className="font-mono text-xs">
                      {provider.credential_ref ?? "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <ProviderDialog
                        provider={provider}
                        trigger={
                          <Button variant="ghost" size="icon" aria-label="编辑">
                            <Pencil />
                          </Button>
                        }
                      />
                      <Confirm
                        title="删除供应商"
                        description={`删除“${provider.name}”。仍有模型使用它时不能删除。`}
                        action="删除"
                        onConfirm={() =>
                          remove.mutate({ params: { path: { provider_id: provider.id } } })
                        }
                      >
                        <Button variant="ghost" size="icon" aria-label="删除">
                          <Trash2 />
                        </Button>
                      </Confirm>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </QueryState>
      </Panel>
    </Section>
  );
}

function ProviderDialog({ provider, trigger }: { provider?: Provider; trigger: ReactNode }) {
  const [open, setOpen] = useState(false);
  const form = useForm<z.infer<typeof providerSchema>>({
    resolver: zodResolver(providerSchema),
    values: {
      name: provider?.name ?? "",
      provider: provider?.provider ?? "openai",
      base_url: provider?.base_url ?? "",
      credential_ref: provider?.credential_ref ?? "",
    },
  });
  const saved = useSaved("已保存供应商");
  const onSuccess = () => {
    saved();
    setOpen(false);
    form.reset();
  };
  const create = $ai.useMutation("post", "/api/ai/providers", { onSuccess });
  const update = $ai.useMutation("put", "/api/ai/providers/{provider_id}", { onSuccess });
  const submit = form.handleSubmit((values) => {
    const body = {
      ...values,
      base_url: values.base_url || null,
      credential_ref: values.credential_ref || null,
    };
    if (provider) update.mutate({ params: { path: { provider_id: provider.id } }, body });
    else create.mutate({ body });
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{provider ? "编辑供应商" : "新增供应商"}</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <Field label="名称" error={form.formState.errors.name?.message}>
            <Input {...form.register("name")} />
          </Field>
          <Field
            label="供应商标识"
            hint="LangChain 的供应商名，例如 openai、google_genai"
            error={form.formState.errors.provider?.message}
          >
            <Input className="font-mono" {...form.register("provider")} />
          </Field>
          <Field label="接口地址" hint="兼容 OpenAI 的网关填写地址，官方接口留空">
            <Input className="font-mono" {...form.register("base_url")} />
          </Field>
          <Field label="密钥所在的环境变量" hint="密钥不保存在数据库，只填分析服务上的环境变量名">
            <Input className="font-mono" {...form.register("credential_ref")} />
          </Field>
          <DialogFooter>
            <Button type="submit">保存</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function parseModelParams(text: string): Model["params"] | null {
  try {
    const params: unknown = JSON.parse(text.trim() || "{}");
    return params !== null && typeof params === "object" && !Array.isArray(params)
      ? (params as Model["params"])
      : null;
  } catch {
    return null;
  }
}

/** 百炼（华北2）支持显式缓存的千问模型，与分析服务的列表一致。 */
const EXPLICIT_CACHE_MODELS = new Set([
  "qwen4-max",
  "qwen3.8-max",
  "qwen3.7-max",
  "qwen3.7-plus",
  "qwen3.6-plus",
  "qwen3.5-plus",
  "qwen-plus",
  "qwen3.8-flash",
  "qwen3.7-flash",
  "qwen3.6-flash",
  "qwen3.5-flash",
  "qwen-flash",
  "qwen3-vl-plus",
]);

/** 与分析服务应用百炼显式缓存标记的条件一致；支持的模型默认开启。 */
function supportsExplicitCache(
  provider: Provider | undefined,
  name: string,
  params: Model["params"] | null,
) {
  if (
    !EXPLICIT_CACHE_MODELS.has(name.trim()) ||
    provider?.provider !== "openai" ||
    !provider.base_url ||
    params?.use_responses_api
  )
    return false;

  try {
    const { hostname } = new URL(provider.base_url);
    return (
      hostname.endsWith(".maas.aliyuncs.com") ||
      hostname.endsWith(".dashscope.aliyuncs.com") ||
      [
        "dashscope.aliyuncs.com",
        "dashscope-intl.aliyuncs.com",
        "dashscope-us.aliyuncs.com",
      ].includes(hostname)
    );
  } catch {
    return false;
  }
}

const modelSchema = z.object({
  provider_id: z.string().min(1, "请选择供应商"),
  name: z.string().trim().min(1, "请填写模型名"),
  max_concurrency: z.coerce.number<string>().int().min(1),
  requests_per_minute: z.coerce.number<string>().int().min(1),
  timeout_seconds: z.coerce.number<string>().positive(),
  params: z.string().transform((text, context) => {
    const params = parseModelParams(text);
    if (params) return params;
    context.addIssue({ code: "custom", message: "请填写 JSON 对象" });
    return z.NEVER;
  }),
  bailian_explicit_cache: z.boolean(),
});

function Models() {
  const models = $ai.useQuery("get", "/api/ai/models");
  const providers = $ai.useQuery("get", "/api/ai/providers");
  const usage = useRecentAiUsage();
  const saved = useSaved("已删除模型");
  const remove = $ai.useMutation("delete", "/api/ai/models/{model_id}", { onSuccess: saved });
  const providerOf = (id: string) => providers.data?.find((provider) => provider.id === id);
  const explicit = (model: Model) =>
    supportsExplicitCache(providerOf(model.provider_id), model.name, model.params) &&
    model.params.bailian_explicit_cache !== false;
  return (
    <Section
      title="模型"
      extra={
        <ModelDialog
          providers={providers.data ?? []}
          trigger={
            <Button size="sm">
              <Plus /> 新增
            </Button>
          }
        />
      }
    >
      <Panel>
        <QueryState query={models} isEmpty={(data) => !data.length} empty="还没有模型">
          {(data) => (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>模型</TableHead>
                  <TableHead>供应商</TableHead>
                  <TableHead className="text-right">请求并发上限</TableHead>
                  <TableHead className="text-right">每分钟请求</TableHead>
                  <TableHead className="text-right">超时</TableHead>
                  <TableHead className="text-right" title="命中缓存的输入 token ÷ 输入 token">
                    缓存命中（24 小时）
                  </TableHead>
                  <TableHead>参数</TableHead>
                  <TableHead className="w-24" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.map((model) => (
                  <TableRow key={model.id}>
                    <TableCell className="font-mono text-xs font-medium">{model.name}</TableCell>
                    <TableCell>{providerOf(model.provider_id)?.name ?? "—"}</TableCell>
                    <TableCell className="text-right font-mono">{model.max_concurrency}</TableCell>
                    <TableCell className="text-right font-mono">
                      {model.requests_per_minute}
                    </TableCell>
                    <TableCell className="text-right font-mono">{model.timeout_seconds}s</TableCell>
                    <TableCell className="text-right">
                      <div className="font-mono">
                        {percent(cacheRate(usage.data?.filter((row) => row.model_id === model.id)))}
                      </div>
                      <div className="text-muted-foreground text-xs">
                        {explicit(model) ? "显式缓存" : "隐式缓存"}
                      </div>
                    </TableCell>
                    <TableCell className="max-w-56 truncate font-mono text-xs">
                      {JSON.stringify(model.params)}
                    </TableCell>
                    <TableCell className="text-right">
                      <ModelDialog
                        model={model}
                        providers={providers.data ?? []}
                        trigger={
                          <Button
                            variant="ghost"
                            size="xs"
                            aria-label="编辑"
                            title="编辑模型并发和参数"
                          >
                            <Pencil /> 设置
                          </Button>
                        }
                      />
                      <Confirm
                        title="删除模型"
                        description={`删除“${model.name}”。仍被任务路由使用时不能删除。`}
                        action="删除"
                        onConfirm={() =>
                          remove.mutate({ params: { path: { model_id: model.id } } })
                        }
                      >
                        <Button variant="ghost" size="icon" aria-label="删除">
                          <Trash2 />
                        </Button>
                      </Confirm>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </QueryState>
      </Panel>
    </Section>
  );
}

function ModelDialog({
  model,
  providers,
  trigger,
}: {
  model?: Model;
  providers: Provider[];
  trigger: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const cacheSwitchId = useId();
  const { bailian_explicit_cache, ...modelParams } = model?.params ?? {};
  const form = useForm<z.input<typeof modelSchema>, unknown, z.output<typeof modelSchema>>({
    resolver: zodResolver(modelSchema),
    values: {
      provider_id: model?.provider_id ?? providers[0]?.id ?? "",
      name: model?.name ?? "",
      max_concurrency: String(model?.max_concurrency ?? 4),
      requests_per_minute: String(model?.requests_per_minute ?? 60),
      timeout_seconds: String(model?.timeout_seconds ?? 120),
      params: JSON.stringify(modelParams, null, 2),
      bailian_explicit_cache: bailian_explicit_cache !== false,
    },
  });
  const saved = useSaved("已保存模型");
  const onSuccess = () => {
    saved();
    setOpen(false);
    form.reset();
  };
  const create = $ai.useMutation("post", "/api/ai/models", { onSuccess });
  const update = $ai.useMutation("put", "/api/ai/models/{model_id}", { onSuccess });
  const submit = form.handleSubmit(
    ({ params, bailian_explicit_cache: cacheEnabled, ...values }) => {
      // 支持的模型默认开启，只在关掉时记下 false
      const provider = providers.find(({ id }) => id === values.provider_id);
      delete params.bailian_explicit_cache;
      if (!cacheEnabled && supportsExplicitCache(provider, values.name, params)) {
        params.bailian_explicit_cache = false;
      }
      const body = { ...values, params };
      if (model) update.mutate({ params: { path: { model_id: model.id } }, body });
      else create.mutate({ body });
    },
  );
  const providerId = form.watch("provider_id");
  const showCacheSwitch = supportsExplicitCache(
    providers.find(({ id }) => id === providerId),
    form.watch("name"),
    parseModelParams(form.watch("params")),
  );
  const errors = form.formState.errors;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-h-[calc(100dvh-2rem)] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{model ? "编辑模型" : "新增模型"}</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <Field label="供应商" error={errors.provider_id?.message}>
            <Select
              value={providerId}
              onValueChange={(value) => form.setValue("provider_id", value)}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="先在“供应商”中新增" />
              </SelectTrigger>
              <SelectContent>
                {providers.map((provider) => (
                  <SelectItem key={provider.id} value={provider.id}>
                    {provider.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="模型名" error={errors.name?.message}>
            <Input className="font-mono" {...form.register("name")} />
          </Field>
          <div className="grid grid-cols-3 gap-3">
            <Field label="请求并发上限" error={errors.max_concurrency?.message}>
              <Input type="number" {...form.register("max_concurrency")} />
            </Field>
            <Field label="每分钟请求" error={errors.requests_per_minute?.message}>
              <Input type="number" {...form.register("requests_per_minute")} />
            </Field>
            <Field label="超时（秒）" error={errors.timeout_seconds?.message}>
              <Input type="number" {...form.register("timeout_seconds")} />
            </Field>
          </div>
          <p className="text-muted-foreground text-xs">
            请求并发是所有分析 Worker
            同时发给此模型的请求数。使用同一模型的听音、识别和汇总阶段共享这个上限。
          </p>
          {showCacheSwitch && (
            <div className="bg-muted/50 flex items-center justify-between gap-4 rounded-md border px-3 py-2.5">
              <div className="space-y-1.5">
                <Label htmlFor={cacheSwitchId}>显式 Prompt Cache</Label>
                <p id={`${cacheSwitchId}-hint`} className="text-muted-foreground text-xs">
                  默认开启，复用固定的提示词和工具定义。建缓存按输入单价的 125% 计费，命中按
                  10%，命中率在模型列表和数据看板里看。
                </p>
              </div>
              <Switch.Root
                id={cacheSwitchId}
                aria-describedby={`${cacheSwitchId}-hint`}
                checked={form.watch("bailian_explicit_cache")}
                onCheckedChange={(checked) => form.setValue("bailian_explicit_cache", checked)}
                className="bg-input focus-visible:ring-ring/50 data-[state=checked]:bg-primary inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full p-0.5 transition-colors outline-none focus-visible:ring-[3px]"
              >
                <Switch.Thumb className="bg-background pointer-events-none block size-4 rounded-full shadow-xs transition-transform data-[state=checked]:translate-x-4" />
              </Switch.Root>
            </div>
          )}
          <Field label="调用参数" hint="JSON，例如 temperature" error={errors.params?.message}>
            <Textarea rows={4} className="font-mono text-xs" {...form.register("params")} />
          </Field>
          <DialogFooter>
            <Button type="submit">保存</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Prompts() {
  const [kind, setKind] = useState<Kind>("recognize");
  const history = $ai.useQuery("get", "/api/ai/prompts/{kind}", { params: { path: { kind } } });
  return (
    <div className="space-y-4">
      <Tabs value={kind} onValueChange={(value) => setKind(value as Kind)}>
        <TabsList>
          {KINDS.map(([value, label]) => (
            <TabsTrigger key={value} value={value}>
              {label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <QueryState query={history}>
        {(data) => <PromptEditor key={kind} kind={kind} history={data} />}
      </QueryState>
    </div>
  );
}

function PromptEditor({
  kind,
  history,
}: {
  kind: Kind;
  history: AnalysisSchemas["PromptHistory"];
}) {
  const active = history.versions.find((version) => version.active);
  const activeContent = active?.content ?? history.builtin;
  const [draft, setDraft] = useState(activeContent);
  const [note, setNote] = useState("");
  const [compared, setCompared] = useState<string>();
  const saved = useSaved("已保存提示词");
  const path = { params: { path: { kind } } };
  const add = $ai.useMutation("post", "/api/ai/prompts/{kind}", {
    onSuccess: () => {
      saved();
      setNote("");
    },
  });
  const activate = $ai.useMutation("put", "/api/ai/prompts/{kind}/active", { onSuccess: saved });

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
      <Panel className="space-y-3 p-5">
        <div className="flex items-center justify-between text-sm">
          <span>
            当前使用：<b>{active ? `v${active.version}` : "内置提示词"}</b>
          </span>
          {draft !== activeContent && <span className="text-signal text-xs">有未保存的修改</span>}
        </div>
        <Textarea
          rows={18}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          className="font-mono text-xs leading-6"
        />
        <div className="flex gap-2">
          <Input
            placeholder="版本说明，例如：补充尺码规则"
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
          <Button
            disabled={!draft.trim() || draft === activeContent || add.isPending}
            onClick={() => add.mutate({ ...path, body: { content: draft, note, activate: true } })}
          >
            保存为新版本并启用
          </Button>
        </div>
        {compared !== undefined && (
          <Diff before={compared} after={draft} onClose={() => setCompared(undefined)} />
        )}
      </Panel>
      <Section title="版本">
        <Panel>
          <ul className="divide-y">
            {history.versions.map((version) => (
              <li key={version.id} className="space-y-1.5 px-4 py-3">
                <div className="flex items-center gap-2 text-sm">
                  <b className="font-mono">v{version.version}</b>
                  {version.active && (
                    <span className="bg-signal text-signal-foreground rounded-sm px-1.5 text-xs">
                      使用中
                    </span>
                  )}
                  <span className="text-muted-foreground ml-auto text-xs">
                    {dateTime(version.created_at)}
                  </span>
                </div>
                {version.note && (
                  <div className="text-muted-foreground text-xs">{version.note}</div>
                )}
                <VersionActions
                  onCompare={() => setCompared(version.content)}
                  onLoad={() => setDraft(version.content)}
                  onActivate={
                    version.active
                      ? undefined
                      : () => activate.mutate({ ...path, body: { prompt_id: version.id } })
                  }
                />
              </li>
            ))}
            <li className="space-y-1.5 px-4 py-3">
              <div className="flex items-center gap-2 text-sm">
                <b>内置提示词</b>
                {!active && (
                  <span className="bg-signal text-signal-foreground rounded-sm px-1.5 text-xs">
                    使用中
                  </span>
                )}
              </div>
              <VersionActions
                onCompare={() => setCompared(history.builtin)}
                onLoad={() => setDraft(history.builtin)}
                onActivate={
                  active ? () => activate.mutate({ ...path, body: { prompt_id: null } }) : undefined
                }
              />
            </li>
          </ul>
        </Panel>
      </Section>
    </div>
  );
}

function VersionActions({
  onCompare,
  onLoad,
  onActivate,
}: {
  onCompare: () => void;
  onLoad: () => void;
  onActivate?: () => void;
}) {
  return (
    <div className="flex gap-1">
      <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={onCompare}>
        <GitCompare /> 与编辑区对比
      </Button>
      <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={onLoad}>
        <Undo2 /> 载入
      </Button>
      {onActivate && (
        <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={onActivate}>
          <Check /> 启用
        </Button>
      )}
    </div>
  );
}

function Diff({ before, after, onClose }: { before: string; after: string; onClose: () => void }) {
  const lines = diffLines(before, after);
  if (lines.every((line) => line.kind === "same")) {
    return <Notice>与编辑区内容相同</Notice>;
  }
  return (
    <div className="space-y-2">
      <div className="text-muted-foreground flex items-center justify-between text-xs">
        <span>
          <span className="text-status-failed">− 所选版本</span> ·{" "}
          <span className="text-status-done">+ 编辑区</span>
        </span>
        <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={onClose}>
          关闭对比
        </Button>
      </div>
      <pre className="bg-muted/40 max-h-96 overflow-auto rounded-md border py-2 text-xs leading-6">
        {lines.map((line, index) => (
          <div
            key={index}
            className={cn(
              "px-3 whitespace-pre-wrap",
              line.kind === "added" && "bg-status-done/10 text-status-done",
              line.kind === "removed" &&
                "bg-status-failed/10 text-status-failed decoration-status-failed/40 line-through",
            )}
          >
            {line.kind === "added" ? "+ " : line.kind === "removed" ? "− " : "  "}
            {line.text}
          </div>
        ))}
      </pre>
    </div>
  );
}
