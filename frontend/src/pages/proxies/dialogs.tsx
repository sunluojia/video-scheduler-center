import { zodResolver } from "@hookform/resolvers/zod";
import { FileUp } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { $api, type Schemas } from "@/api/client";
import { Field } from "@/components/field";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useFilters } from "@/lib/params";
import { useDone } from "@/lib/refresh";
import { proxyProtocols } from "@/lib/status";
import { address, type Tag, UNFILED } from "./data";
import { FolderSelect, UsePicker } from "./shared";

type Proxy = Schemas["ProxyView"];

const USES_HINT = "代理只分配给选中的用途；同一个代理可以同时用于多个用途，各用途分开记录好坏。";

/** 左侧树正在看的文件夹，新代理默认放进去。 */
function useCurrentFolder() {
  const folder = useFilters(["folder"] as const).values.folder;
  return folder && folder !== UNFILED ? folder : null;
}

const proxySchema = z.object({
  name: z.string().trim().max(128),
  protocol: z.enum(["auto", "http", "https", "socks5", "ssh"]),
  host: z.string().trim().min(1, "请填写主机"),
  port: z.coerce
    .number<string>()
    .int("端口是整数")
    .min(1, "端口在 1–65535 之间")
    .max(65535, "端口在 1–65535 之间"),
  username: z.string().trim(),
  password: z.string(),
  private_key: z.string().trim(),
  refresh_url: z.string().trim(),
  folder_id: z.string().nullable(),
  uses: z.array(z.custom<Tag>()),
});

/** 添加或编辑代理。编辑时凭据和刷新地址不回显，留空表示不改。 */
export function ProxyDialog({ proxy, trigger }: { proxy?: Proxy; trigger: ReactNode }) {
  const [open, setOpen] = useState(false);
  const current = useCurrentFolder();
  const form = useForm<z.input<typeof proxySchema>, unknown, z.output<typeof proxySchema>>({
    resolver: zodResolver(proxySchema),
    values: {
      name: proxy?.name ?? "",
      protocol: proxy?.protocol ?? "auto",
      host: proxy?.host ?? "",
      port: proxy ? String(proxy.port) : "",
      username: "",
      password: "",
      private_key: "",
      refresh_url: "",
      folder_id: proxy ? proxy.folder_id : current,
      uses: proxy?.uses ?? [],
    },
  });
  const done = useDone();
  const onSuccess = () => {
    done(proxy ? "已保存；地址或凭据改过时会重新测试" : "已添加，稍后由爬取程序测试");
    setOpen(false);
    form.reset();
  };
  const create = $api.useMutation("post", "/api/proxies", { onSuccess });
  const update = $api.useMutation("patch", "/api/proxies/{proxy_id}", { onSuccess });
  const submit = form.handleSubmit((values) => {
    if (!proxy) return create.mutate({ body: values });
    const { username, password, private_key, refresh_url, ...rest } = values;
    const secrets = { username, password, private_key, refresh_url };
    const changed = Object.fromEntries(Object.entries(secrets).filter(([, value]) => value));
    update.mutate({ params: { path: { proxy_id: proxy.id } }, body: { ...rest, ...changed } });
  });
  const keep = (has: boolean) => (proxy && has ? "已设置，留空不修改" : undefined);
  const errors = form.formState.errors;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{proxy ? "编辑代理" : "添加代理"}</DialogTitle>
          <DialogDescription>
            保存后由在线的爬取程序测试连通性和出口 IP，通过后分配给选中的用途。
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="grid grid-cols-[1fr_8rem] gap-3">
            <Field label="主机" error={errors.host?.message}>
              <Input
                className="font-mono"
                placeholder="1.2.3.4 或域名"
                {...form.register("host")}
              />
            </Field>
            <Field label="端口" error={errors.port?.message}>
              <Input type="number" className="font-mono" {...form.register("port")} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="协议" hint="不确定就选自动识别，测试时依次试 SOCKS5 和 HTTP">
              <Select
                value={form.watch("protocol")}
                onValueChange={(value) => form.setValue("protocol", value as Proxy["protocol"])}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(proxyProtocols).map(([key, label]) => (
                    <SelectItem key={key} value={key}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="备注名" error={errors.name?.message}>
              <Input placeholder="比如 美国-1" {...form.register("name")} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="用户名">
              <Input
                autoComplete="off"
                placeholder={proxy?.username ? `${proxy.username}，留空不修改` : "没有可不填"}
                {...form.register("username")}
              />
            </Field>
            <Field label="密码">
              <Input
                type="password"
                autoComplete="new-password"
                placeholder={keep(proxy?.has_password ?? false) ?? "没有可不填"}
                {...form.register("password")}
              />
            </Field>
          </div>
          {form.watch("protocol") === "ssh" && (
            <Field label="SSH 私钥" hint="与密码二选一">
              <Textarea
                rows={4}
                className="font-mono text-xs"
                placeholder={keep(proxy?.has_private_key ?? false)}
                {...form.register("private_key")}
              />
            </Field>
          )}
          <Field label="刷新地址" hint="动态代理才填：访问这个地址会换一个出口 IP">
            <Input
              className="font-mono text-xs"
              placeholder={keep(proxy?.dynamic ?? false) ?? "https://…"}
              {...form.register("refresh_url")}
            />
          </Field>
          <Field label="文件夹">
            <FolderSelect
              value={form.watch("folder_id")}
              onChange={(value) => form.setValue("folder_id", value)}
              none="不放文件夹"
              label="文件夹"
            />
          </Field>
          <Field label="用途" hint={USES_HINT} group>
            <UsePicker
              value={form.watch("uses")}
              onChange={(value) => form.setValue("uses", value)}
            />
          </Field>
          <DialogFooter>
            <Button type="submit" disabled={create.isPending || update.isPending}>
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** 批量导入：粘贴或选文件，先预览识别结果，重复和无法识别的行导入时跳过。 */
export function ImportDialog() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const current = useCurrentFolder();
  const [folder, setFolder] = useState<string | null>(null);
  const [uses, setUses] = useState<Tag[]>([]);
  const [rows, setRows] = useState<Schemas["ParsedRow"][]>();
  const edit = (value: string) => {
    setText(value);
    setRows(undefined);
  };
  const done = useDone();
  const parse = $api.useMutation("post", "/api/proxies/parse", { onSuccess: setRows });
  const save = $api.useMutation("post", "/api/proxies/import", {
    onSuccess: (result) => {
      done(
        `已导入 ${result.created} 个代理${result.skipped ? `，跳过 ${result.skipped} 行` : ""}，稍后自动测试`,
      );
      setOpen(false);
      edit("");
    },
  });
  const fresh = rows?.filter((row) => !row.error && !row.duplicate).length ?? 0;
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) setFolder(current);
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline">
          <FileUp /> 导入
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>导入代理</DialogTitle>
          <DialogDescription>
            每行一个，支持 host:port、host:port:用户名:密码、用户名:密码@host:port、socks5://…
            等常见写法，也可以用逗号、空格或制表符分隔；IPv6 地址要加方括号。
          </DialogDescription>
        </DialogHeader>
        <Textarea
          rows={rows ? 4 : 8}
          value={text}
          onChange={(event) => edit(event.target.value)}
          className="font-mono text-xs"
          placeholder={
            "1.2.3.4:8080\n1.2.3.5:1080:alice:secret\nsocks5://bob:pass@[2001:db8::1]:1080"
          }
          aria-label="代理列表"
        />
        <div className="grid gap-4 sm:grid-cols-[14rem_1fr]">
          <Field label="放进文件夹">
            <FolderSelect
              value={folder}
              onChange={setFolder}
              none="不放文件夹"
              label="放进文件夹"
            />
          </Field>
          <Field label="用途" hint={USES_HINT} group>
            <UsePicker value={uses} onChange={setUses} />
          </Field>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" asChild>
            <label className="cursor-pointer">
              <FileUp /> 选择文件
              <input
                type="file"
                accept=".txt,.csv,text/plain"
                className="sr-only"
                onChange={async (event) => {
                  const file = event.target.files?.[0];
                  if (file) edit(await file.text());
                  event.target.value = "";
                }}
              />
            </label>
          </Button>
          <Button
            size="sm"
            variant="secondary"
            className="ml-auto"
            disabled={!text.trim() || parse.isPending}
            onClick={() => parse.mutate({ body: { text } })}
          >
            预览
          </Button>
        </div>
        {rows && <Preview rows={rows} fresh={fresh} />}
        <DialogFooter>
          <Button
            disabled={!fresh || save.isPending}
            onClick={() => save.mutate({ body: { text, folder_id: folder, uses } })}
          >
            导入 {fresh || ""} 个代理
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Preview({ rows, fresh }: { rows: Schemas["ParsedRow"][]; fresh: number }) {
  return (
    <div className="space-y-2">
      <p className="text-muted-foreground text-xs">
        共 {rows.length} 行：可导入 <span className="text-foreground font-mono">{fresh}</span>
        ，重复 {rows.filter((row) => row.duplicate).length}，无法识别{" "}
        {rows.filter((row) => row.error).length}
      </p>
      <div className="max-h-56 overflow-y-auto rounded-md border">
        <table className="w-full text-xs">
          <thead className="bg-muted/50 text-muted-foreground sticky top-0">
            <tr>
              {["行", "地址", "协议", "用户名", "密码", "结果"].map((title) => (
                <th key={title} className="px-3 py-1.5 text-left font-normal">
                  {title}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="font-mono">
            {rows.map((row) => (
              <tr key={row.number} className="border-t">
                <td className="text-muted-foreground px-3 py-1">{row.number}</td>
                <td className="px-3 py-1">
                  {row.host && row.port ? address(row.host, row.port) : "—"}
                </td>
                <td className="px-3 py-1">{row.protocol && proxyProtocols[row.protocol]}</td>
                <td className="px-3 py-1">{row.username}</td>
                <td className="px-3 py-1">{row.has_password ? "有" : ""}</td>
                <td className="px-3 py-1 font-sans">
                  {row.error ? (
                    <span className="text-status-failed">{row.error}</span>
                  ) : row.duplicate ? (
                    <span className="text-muted-foreground">重复，跳过</span>
                  ) : (
                    <span className="text-status-done">导入</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
