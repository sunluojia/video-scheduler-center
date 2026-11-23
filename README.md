# 调度中心

管理达人名单批次：向[爬取程序](https://github.com/sunluojia/video-crawler)发布爬取和下载任务，保存上传的原视频，向[视频分析服务](https://github.com/sunluojia/video-analysis-service)发布分析和汇总任务，汇总结论并导出 Excel。带内部使用的管理页面，不设登录。设计和与爬取程序的约定见 [docs/design.md](docs/design.md)。

## 运行角色

| 进程 | 命令 | 职责 |
| --- | --- | --- |
| API | `scheduler-center-api` | 页面、页面接口和 SSE；执行程序的注册心跳、视频上传、原视频下载，以及爬取程序按用途申请和归还线路的接口（要求服务凭据） |
| Worker | `scheduler-center-worker` | 投递发件箱中的任务，判断批次完成，对账重投，清理原视频；把到期的代理交给在线的爬取程序测试 |
| 事件接收 | `dramatiq scheduler_center.entrypoints.consumer:broker` | 接收爬取程序和分析服务回传的事件 |

API 和 Worker 需要共享原视频目录（`SC_MATERIAL_ROOT`）。

## 本地开发

需要 [uv](https://docs.astral.sh/uv/)、Node.js 24、pnpm、PostgreSQL 和 Redis。

```bash
cp .env.example backend/.env
```

在 `backend/.env` 中设置 `SC_SERVICE_TOKEN`（至少 16 位，与爬取程序、分析服务一致）和数据库、Redis 地址。以下命令都在 `backend/` 中执行：

```bash
uv sync
```

```bash
uv run alembic upgrade head
```

```bash
uv run scheduler-center-api
```

```bash
uv run scheduler-center-worker
```

```bash
uv run dramatiq scheduler_center.entrypoints.consumer:broker --processes 1 --threads 4
```

前端在 `frontend/` 中：

```bash
pnpm install
```

```bash
pnpm dev
```

开发服务器在 5173 端口，`/api` 转发到 8080 的 API。`pnpm build` 生成的 `frontend/dist` 由 API 直接提供，部署时不需要单独的前端服务。

后端接口变化后，在 API 运行时更新 OpenAPI 描述并重新生成前端类型（分析服务的描述同理，取自它的 `/openapi.json`）：

```bash
curl -s http://127.0.0.1:8080/openapi.json -o docs/openapi/scheduler.json
```

```bash
pnpm api
```

### 模拟爬取程序

`tools/fake_crawler` 按 [第 4 节的约定](docs/design.md#4-与爬取程序的约定)模拟爬取程序，只用于开发和联调。样例目录中每位达人一个子目录（目录名是账号）：`.mp4`、`.mov` 是视频，`.jpg`、`.png` 是图文，按文件名排序；子目录中放 `PRIVATE` 文件表示私密账号，放 `LOGIN` 文件表示需要登录，放 `FAIL` 文件表示爬取失败（读到了主页，作品列表没读成），没有子目录表示找不到此达人。暂停采集时只停新的达人采集，下载照常。在项目根目录执行（需要设置 `SC_SERVICE_TOKEN` 环境变量）：

```bash
uv run --project backend python tools/fake_crawler/fake_crawler.py --samples samples --redis redis://localhost:6379/0
```

它启动后向调度中心注册，在 8090 端口提供控制接口。获取信息和下载视频前，按[代理方案](docs/proxy-design.md)分别以“获取信息”“下载视频”用途向调度中心申请线路，用完归还；样例仍从本地目录读取，不经代理。调度中心请它测试代理时，它真的经代理访问回显地址（调度中心的 `SC_PROXY_ECHO_URL`，默认 `https://api64.ipify.org`），联调时可以换成本机的回显服务，免得访问外网，但这时经真实远程代理测不出出口。请它测速时，它真的经代理下载测速文件（调度中心的 `SC_PROXY_SPEED_URL`，默认 Cloudflare 测速页的 10 MB 下载接口；大小由 `SC_PROXY_SPEED_SIZE` 给出，只认完整的 200 和对得上的字节数），先单路一次，再 4 路同时下载。它的测速校验有单独的测试：`uv run --project backend python -m pytest tools/fake_crawler -q`。要在一台机器上模拟多台设备，用不同的 `--instance` 和 `--port` 各启动一个。

## 测试

后端测试需要一个独立的测试库，测试会重建其中的表。可以用 Docker 启动一个（映射到 55432，避开本机已有的 PostgreSQL）：

```bash
docker run -d --name scheduler-center-test-db -e POSTGRES_USER=video -e POSTGRES_PASSWORD=video -e POSTGRES_DB=scheduler_center_test -p 55432:5432 postgres:17
```

```bash
SC_TEST_DATABASE_URL=postgresql+asyncpg://video:video@localhost:55432/scheduler_center_test uv run pytest
```

PowerShell 中先执行 `$env:SC_TEST_DATABASE_URL = "postgresql+asyncpg://video:video@localhost:55432/scheduler_center_test"`，再运行 `uv run pytest`。队列测试使用 Dramatiq 的 StubBroker，不需要 Redis；执行程序的接口用 HTTPX 的模拟传输。

前端在 `frontend/` 中：`pnpm test`（Vitest）、`pnpm e2e`（Playwright，拦截接口返回模拟数据，首次运行前执行 `pnpm exec playwright install chromium`）、`pnpm lint`。

## 部署

```bash
cp .env.example .env
```

在 `.env` 中设置 `SC_SERVICE_TOKEN`，然后：

```bash
docker compose -f deployment/compose.yaml up -d --build
```

compose 包含 PostgreSQL、Redis（开启持久化）和三个进程。页面和执行程序都经 8080 端口访问；爬取程序和分析服务连接这里的 Redis（6379 端口），把调度中心地址配置为 `http://<本机局域网地址>:8080`，三方使用相同的服务凭据。
