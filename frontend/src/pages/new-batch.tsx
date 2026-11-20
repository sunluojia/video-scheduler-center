import { zodResolver } from "@hookform/resolvers/zod";
import { format } from "date-fns";
import { FileSpreadsheet, Play, Save, ScanSearch, Upload } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router";
import { z } from "zod";
import { $api, formData, type Schemas } from "@/api/client";
import { Notice, PageHeader, Panel, Section } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { cn } from "@/lib/utils";

const schema = z
  .object({
    name: z.string().trim().min(1, "请填写批次名称").max(128, "批次名称最多 128 个字"),
    source: z.enum(["file", "links"]),
    file: z.instanceof(File).optional(),
    links: z.string().optional(),
  })
  .refine((value) => (value.source === "file" ? value.file : value.links?.trim()), {
    message: "请上传名单文件或粘贴主页链接",
    path: ["source"],
  });
type Form = z.infer<typeof schema>;

const PREVIEW_ROWS = 200;

export function NewBatchPage() {
  const navigate = useNavigate();
  const [preview, setPreview] = useState<Schemas["Preview"]>();
  const form = useForm<Form>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: `${format(new Date(), "yyyy-MM-dd")} 达人名单`,
      source: "file",
      links: "",
    },
  });
  const { source, file } = form.watch();
  const fields = (values: Form) =>
    values.source === "file" ? { file: values.file } : { links: values.links };

  const parse = $api.useMutation("post", "/api/imports/preview", { onSuccess: setPreview });
  const create = $api.useMutation("post", "/api/batches", {
    onSuccess: (batch) => navigate(`/batches/${batch.id}`),
  });
  const multipart = {
    bodySerializer: (body: unknown) => formData(body as Record<string, string | Blob>),
  };

  const runPreview = form.handleSubmit((values) =>
    parse.mutate({ body: fields(values) as never, ...multipart }),
  );
  const save = (start: boolean) =>
    form.handleSubmit((values) =>
      create.mutate({
        body: { name: values.name, start: String(start), ...fields(values) } as never,
        ...multipart,
      }),
    )();
  const reset = () => setPreview(undefined);

  return (
    <>
      <PageHeader
        eyebrow="Batches / New"
        title="新建批次"
        meta="上传 Excel 名单或粘贴 TikTok 主页链接，解析确认后保存。"
      />
      <div className="grid gap-8 lg:grid-cols-[380px_minmax(0,1fr)]">
        <Section title="名单">
          <Panel className="space-y-5 p-5">
            <div className="space-y-2">
              <Label htmlFor="name">批次名称</Label>
              <Input id="name" {...form.register("name")} />
              <FieldError message={form.formState.errors.name?.message} />
            </div>
            <Tabs
              value={source}
              onValueChange={(value) => {
                form.setValue("source", value as Form["source"]);
                reset();
              }}
            >
              <TabsList className="w-full">
                <TabsTrigger value="file">上传 Excel</TabsTrigger>
                <TabsTrigger value="links">粘贴链接</TabsTrigger>
              </TabsList>
              <TabsContent value="file" className="pt-3">
                <label
                  className={cn(
                    "hover:border-signal hover:bg-accent/40 flex cursor-pointer flex-col items-center gap-2 rounded-md border border-dashed px-4 py-8 text-center text-sm transition-colors",
                    file && "border-signal/60 bg-accent/30 border-solid",
                  )}
                >
                  {file ? (
                    <FileSpreadsheet className="text-signal size-6" />
                  ) : (
                    <Upload className="text-muted-foreground size-6" />
                  )}
                  <span className="font-medium">{file ? file.name : "选择 .xlsx 名单文件"}</span>
                  <span className="text-muted-foreground text-xs">
                    读取“达人名称”“creator_oecuid”“二维码”三列
                  </span>
                  <input
                    type="file"
                    accept=".xlsx"
                    className="hidden"
                    onChange={(event) => {
                      form.setValue("file", event.target.files?.[0], { shouldValidate: true });
                      reset();
                    }}
                  />
                </label>
              </TabsContent>
              <TabsContent value="links" className="pt-3">
                <Textarea
                  rows={10}
                  placeholder={"每行一个，例如：\nhttps://www.tiktok.com/@alice\n@bob"}
                  className="font-mono text-xs"
                  {...form.register("links", { onChange: reset })}
                />
              </TabsContent>
            </Tabs>
            <FieldError message={form.formState.errors.source?.message} />
            <Button
              variant="secondary"
              className="w-full"
              onClick={runPreview}
              disabled={parse.isPending}
            >
              <ScanSearch /> {parse.isPending ? "正在解析…" : "解析预览"}
            </Button>
          </Panel>
        </Section>

        <Section
          title="预览"
          extra={
            preview && (
              <div className="flex gap-2">
                <Button variant="outline" onClick={() => save(false)} disabled={create.isPending}>
                  <Save /> 保存为草稿
                </Button>
                <Button
                  className="bg-signal text-signal-foreground hover:bg-signal/90"
                  onClick={() => save(true)}
                  disabled={create.isPending}
                >
                  <Play /> 保存并开始
                </Button>
              </div>
            )
          }
        >
          <Panel>
            {preview ? (
              <PreviewTable preview={preview} />
            ) : (
              <Notice>解析后在这里核对达人名单</Notice>
            )}
          </Panel>
        </Section>
      </div>
    </>
  );
}

function PreviewTable({ preview }: { preview: Schemas["Preview"] }) {
  const rows = preview.rows.slice(0, PREVIEW_ROWS);
  return (
    <>
      <div className="flex flex-wrap gap-6 border-b px-4 py-3 text-sm">
        <span>
          共 <b className="font-mono">{preview.rows.length}</b> 位达人
        </span>
        <span className={cn(preview.invalid ? "text-status-failed" : "text-muted-foreground")}>
          无法识别 <b className="font-mono">{preview.invalid}</b> 行（保存后进入“重新抓取”表）
        </span>
        <span className="text-muted-foreground">
          带二维码{" "}
          <b className="text-foreground font-mono">
            {preview.rows.filter((row) => row.has_qr).length}
          </b>
        </span>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-16">行号</TableHead>
            <TableHead>达人名称</TableHead>
            <TableHead>识别出的账号</TableHead>
            <TableHead>creator_oecuid</TableHead>
            <TableHead className="w-20">二维码</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, index) => (
            <TableRow key={index} className={cn(!row.handle && "bg-status-failed/5")}>
              <TableCell className="text-muted-foreground font-mono text-xs">
                {row.row ?? index + 1}
              </TableCell>
              <TableCell className="max-w-64 truncate">{row.name}</TableCell>
              <TableCell className="font-mono text-xs">
                {row.handle ?? <span className="text-status-failed">无法识别</span>}
              </TableCell>
              <TableCell className="font-mono text-xs">{row.oecuid ?? "—"}</TableCell>
              <TableCell className="text-xs">{row.has_qr ? "有" : "—"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {preview.rows.length > PREVIEW_ROWS && (
        <div className="text-muted-foreground border-t px-4 py-2.5 text-xs">
          只显示前 {PREVIEW_ROWS} 行，保存时包含全部 {preview.rows.length} 行。
        </div>
      )}
    </>
  );
}

function FieldError({ message }: { message?: string }) {
  return message ? <p className="text-status-failed text-xs">{message}</p> : null;
}
