import createFetchClient from "openapi-fetch";
import createClient from "openapi-react-query";
import type { components as AnalysisComponents, paths as AnalysisPaths } from "./analysis";
import type { components, paths } from "./scheduler";

/** 接口数据由 React Query 管缓存，不用浏览器的 HTTP 缓存，免得拿到旧内容。 */
const options = { cache: "no-store" } as const;

/** 调度中心的接口，类型由后端的 OpenAPI 生成。 */
export const fetchClient = createFetchClient<paths>(options);
export const $api = createClient(fetchClient);

/** AI 配置由后端原样转发到分析服务，类型取自分析服务的 OpenAPI。 */
export const analysisClient = createFetchClient<AnalysisPaths>(options);
export const $ai = createClient(analysisClient);

export type Schemas = components["schemas"];
export type AnalysisSchemas = AnalysisComponents["schemas"];

/** 接口错误的说明文字：后端统一返回 {detail}，校验错误时 detail 是列表。 */
export function errorText(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    const { detail } = error as { detail: unknown };
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((item) => item?.msg ?? String(item)).join("；");
  }
  return error instanceof Error ? error.message : "请求失败，请稍后重试";
}

/** 上传表单：名单文件或粘贴的链接。 */
export function formData(fields: Record<string, string | Blob | undefined>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined) data.append(key, value);
  }
  return data;
}
