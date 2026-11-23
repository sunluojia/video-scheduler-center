"""模拟爬取程序：只用于开发和联调，按调度中心设计文档第 4 节的约定工作，不引用调度中心的代码。

作品从本地样例目录读取：samples/<达人账号>/ 下的 .mp4、.mov 是视频，.jpg、.png 是图文，
按文件名排序取前几条。目录不存在时回报“找不到此达人”；目录里有 PRIVATE 文件时回报私密账号；
有 LOGIN 文件时回报需要登录；有 FAIL 文件时回报爬取失败（读到了主页，作品列表没读成）。

获取信息和下载前都按用途向调度中心申请线路，用完归还；数据仍从样例目录读取，不经代理。
调度中心请它测试代理时，真的经代理请求回显地址（SOCKS5 需要 socksio）；请它测速时，
真的经代理下载测速文件，先单路一次，再几路同时下载。

uv run --project backend python tools/fake_crawler/fake_crawler.py --samples samples
"""

import argparse
import contextlib
import hashlib
import logging
import os
import secrets
import socket
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

import dramatiq
import httpx
import uvicorn
from dramatiq import Message
from dramatiq.brokers.redis import RedisBroker
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

log = logging.getLogger("fake_crawler")
TASKS, EVENTS, EVENT_ACTOR = "crawler.tasks", "crawler.events", "crawler_event"
KINDS = {
    ".mp4": "video",
    ".mov": "video",
    ".jpg": "photo",
    ".jpeg": "photo",
    ".png": "photo",
}
LEASE_WAIT_LIMIT = 300
"""等线路最多这么久；Dramatiq 默认 10 分钟超时，模拟程序不必等更久。"""
CRAWL, DOWNLOAD = "crawler.crawl_creator", "crawler.download_video"
"""申请线路的用途：获取信息、下载视频。线路好坏和并发按用途分开算。"""


class Paused(Exception):
    """批次已暂停：交给 Dramatiq 稍后重试。"""


class FakeCrawler:
    def __init__(
        self, samples: Path, scheduler: httpx.Client, broker: RedisBroker, instance: str
    ) -> None:
        self.samples = samples
        self.scheduler = scheduler
        self.broker = broker
        self.instance = instance
        self.paused: set[str] = set()
        self.cancelled: set[str] = set()
        self.runs: dict[str, int] = {}
        self.running: Counter[str] = Counter()
        """每类任务正在做几个。"""
        self.leases: set[str] = set()
        """正在用的线路，随心跳续期。"""
        broker.declare_queue(EVENTS)

    def tracked(self, handler):
        """记下每类任务正在做几个，状态接口显示。"""

        def run(task: dict) -> None:
            self.running[handler.__name__] += 1
            try:
                handler(task)
            finally:
                self.running[handler.__name__] -= 1

        return run

    def crawl_creator(self, task: dict) -> None:
        if task["batch_id"] in self.paused:
            raise Paused(task["batch_id"])  # 暂停采集只拦获取作品，下载照常
        if not self._take(task):
            return
        lease = self._lease(task, CRAWL, "crawled", conclusion="failed")
        if lease is None:
            return
        try:
            self._crawl(task)
        finally:
            self._release(lease, "normal")

    def _crawl(self, task: dict) -> None:
        handle = task["creator"]["handle"]
        folder = self.samples / handle
        if not folder.is_dir():
            return self._emit(task, "crawled", conclusion="not_found")
        if (folder / "PRIVATE").exists():
            return self._emit(task, "crawled", conclusion="private", profile=_profile(handle))
        if (folder / "LOGIN").exists():
            return self._emit(
                task, "crawled", conclusion="login_required", profile=_profile(handle)
            )
        if (folder / "FAIL").exists():
            # 主页读到了，作品列表没读成
            return self._emit(
                task,
                "crawled",
                conclusion="failed",
                reason="第 1 次：作品列表等待响应超时",
                profile=_profile(handle),
            )
        files = sorted(path for path in folder.iterdir() if path.suffix.lower() in KINDS)
        works = [
            {
                "position": position,
                "work_id": _work_id(path),
                "url": f"https://www.tiktok.com/@{handle}/{KINDS[path.suffix.lower()]}/{_work_id(path)}",
                "type": KINDS[path.suffix.lower()],
                "published_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                "description": path.stem,
                "topics": [],
            }
            for position, path in enumerate(files[: task["max_works"]], 1)
        ]
        self._emit(task, "crawled", conclusion="ok", profile=_profile(handle), works=works)

    def download_video(self, task: dict) -> None:
        if not self._take(task):
            return
        lease = self._lease(task, DOWNLOAD, "download_failed")
        if lease is None:
            return
        outcome = "suspect"
        try:
            self._upload(task)
            outcome = "normal"
        finally:
            self._release(lease, outcome)

    def _upload(self, task: dict) -> None:
        folder = self.samples / task["creator"]["handle"]
        found = [p for p in folder.glob("*") if _work_id(p) == task["work"]["work_id"]]
        if not found:
            return self._emit(task, "download_failed", reason="样例目录中没有这条视频")
        data = found[0].read_bytes()
        response = self.scheduler.put(
            f"/api/download-tasks/{task['task_id']}/runs/{task['run']}/video",
            content=data,
            headers={"X-Content-SHA256": hashlib.sha256(data).hexdigest()},
        )
        if response.status_code in (404, 409):  # 已取消或已有更新的轮次，丢弃
            return
        response.raise_for_status()

    def _lease(self, task: dict, use: str, failed: str, **fields) -> dict | None:
        """按用途申请一条线路；分不到时按调度中心给的时间再来。批次取消就放弃，
        等太久就回报失败事件 failed。"""
        deadline = time.monotonic() + LEASE_WAIT_LIMIT
        while task["batch_id"] not in self.cancelled and task["task_id"] not in self.cancelled:
            body = {"device": self.instance, "use": use, "task_id": task["task_id"]}
            reply = self.scheduler.post("/api/proxy-leases", json=body).raise_for_status().json()
            if lease := reply["lease"]:
                self.leases.add(lease["lease_id"])
                route = lease["route"]
                log.info("%s 走线路 %s（%s）", use, lease["line"], route["kind"])
                return lease
            if time.monotonic() > deadline:
                self._emit(task, failed, reason=f"等不到线路：{reply['reason']}", **fields)
                return None
            log.info("暂时没有线路（%s），%s 秒后再来", reply["reason"], reply["retry_after"])
            time.sleep(reply["retry_after"])
        return None

    def _release(self, lease: dict, outcome: str) -> None:
        self.leases.discard(lease["lease_id"])
        self.scheduler.post(
            f"/api/proxy-leases/{lease['lease_id']}/release", json={"outcome": outcome}
        ).raise_for_status()

    def _take(self, task: dict) -> bool:
        """受理任务：重复或过期的轮次忽略，已取消的丢弃。"""
        if task["batch_id"] in self.cancelled or task["task_id"] in self.cancelled:
            return False
        if self.runs.get(task["task_id"], 0) >= task["run"]:
            return False
        self.runs[task["task_id"]] = task["run"]
        self._emit(task, "accepted")
        return True

    def _emit(self, task: dict, type_: str, **fields) -> None:
        event = {
            "event_id": str(uuid.uuid4()),
            "task_id": task["task_id"],
            "run": task["run"],
            "type": type_,
            "occurred_at": datetime.now(UTC).isoformat(),
            **fields,
        }
        self.broker.enqueue(Message(EVENTS, EVENT_ACTOR, (event,), {}, {}))


def socks5_answers(host: str, port: int) -> bool:
    """发 SOCKS5 问候，几秒内按 SOCKS5 回应才算。httpx 的 SOCKS 握手不受超时控制，
    遇到不回应问候的 HTTP 代理会一直等，所以先自己探一下。"""
    try:
        with socket.create_connection((host, port), timeout=5) as conn:
            conn.sendall(b"\x05\x02\x00\x02")
            return conn.recv(2)[:1] == b"\x05"
    except OSError:
        return False


def check_proxy(item: dict, echo_url: str) -> dict:
    """经代理请求回显地址。连不上、握手或认证失败才算不通；握手之后失败算回显服务的问题。"""
    reply = {"key": item["key"]}
    if item["protocol"] == "ssh":
        return reply | {"result": "unreachable", "error": "模拟爬取程序不支持 SSH 代理"}
    if refresh_url := item.get("refresh_url"):
        try:
            httpx.get(refresh_url, timeout=15).raise_for_status()
        except httpx.HTTPError as error:
            return reply | {"result": "unreachable", "error": f"刷新出口失败：{error}"}
    protocols = ["socks5", "http"] if item["protocol"] == "auto" else [item["protocol"]]
    failure = ""
    if "socks5" in protocols and not socks5_answers(item["host"] or "127.0.0.1", item["port"]):
        protocols.remove("socks5")
        failure = "SOCKS5 握手没有回应"
    for protocol in protocols:
        started = time.monotonic()
        try:
            with httpx.Client(proxy=proxy_address(item, protocol), timeout=15) as c:
                response = c.get(echo_url)
        except httpx.ProxyError as error:
            if "407" in str(error):  # 回显地址是 https 时，407 出现在建立隧道时
                return reply | {
                    "result": "auth_failed",
                    "protocol": protocol,
                    "error": str(error),
                }
            failure = f"{protocol} 握手失败：{error}"
            continue
        except (httpx.ConnectError, httpx.ConnectTimeout) as error:
            failure = f"连不上代理：{error}"
            continue
        except httpx.HTTPError as error:
            return reply | {
                "result": "echo_failed",
                "protocol": protocol,
                "error": str(error),
            }
        if response.status_code == 407:  # 回显地址是 http 时，代理直接回 407
            return reply | {
                "result": "auth_failed",
                "protocol": protocol,
                "error": "代理认证失败（407）",
            }
        if response.is_error:
            return reply | {
                "result": "echo_failed",
                "protocol": protocol,
                "error": f"回显服务返回 {response.status_code}",
            }
        latency = int((time.monotonic() - started) * 1000)
        exit_ip = response.text.strip()
        return reply | {
            "result": "ok",
            "protocol": protocol,
            "exit_ip": exit_ip,
            "latency_ms": latency,
        }
    return reply | {"result": "unreachable", "error": failure}


def proxy_address(item: dict, protocol: str) -> str:
    """代理地址；主机为空表示本机端口，IPv6 主机加方括号。"""
    plain = item["host"] or "127.0.0.1"
    host = f"[{plain}]" if ":" in plain else plain
    auth = f"{quote(item['username'])}:{quote(item['password'])}@" if item["username"] else ""
    return f"{protocol}://{auth}{host}:{item['port']}"


class Incomplete(Exception):
    """测速文件没下完整：不是 200、空的、网页或者被截断。"""


def download(client: httpx.Client, url: str, size: int) -> int:
    """完整下载一次测速文件。只认 200，收到的字节数要和预期一致；
    206 部分内容、空正文、网页和截断都算没下完整。"""
    with client.stream("GET", url) as response:
        if response.status_code != 200:
            raise Incomplete(f"测速地址返回 {response.status_code}")
        received = sum(len(chunk) for chunk in response.iter_bytes())
    if received != size:
        raise Incomplete(f"收到 {received} 字节，应为 {size} 字节")
    return received


def mbps(size: int, started: float) -> float:
    return round(size * 8 / (time.monotonic() - started) / 1e6, 1)


def measure_speed(body: dict) -> dict:
    """先单路下载一次，再几路同时下载，速度按总字节数除以从发起到全部下完的时间，
    包括建连接的时间，与实际下载的感受一致。每路各用一个连接，不压缩。"""
    item, url, size, streams = body["item"], body["url"], body["size"], body["streams"]
    proxy = proxy_address(item, item["protocol"])

    def once(_: int = 0) -> int:
        headers = {"Accept-Encoding": "identity"}
        with httpx.Client(proxy=proxy, timeout=30, headers=headers) as client:
            return download(client, url, size)

    reply = {"key": item["key"]}
    try:
        started = time.monotonic()
        single = mbps(once(), started)
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=streams) as pool:
            total = sum(pool.map(once, range(streams)))
        return reply | {"single_mbps": single, "parallel_mbps": mbps(total, started)}
    except Incomplete as error:
        return reply | {"error": str(error)}
    except httpx.HTTPError as error:
        return reply | {"error": f"没下完整：{str(error) or type(error).__name__}"}


def _work_id(path: Path) -> str:
    """同一个文件始终得到同一个数字作品 ID。"""
    digest = hashlib.sha1(f"{path.parent.name}/{path.name}".encode()).hexdigest()
    return str(int(digest[:15], 16))


def _profile(handle: str) -> dict:
    """模拟的平台资料：昵称用账号，平台用户 ID 由账号算出。"""
    digest = hashlib.sha1(handle.encode()).hexdigest()
    return {
        "handle": handle,
        "url": f"https://www.tiktok.com/@{handle}",
        "nickname": handle,
        "platform_user_id": str(int(digest[:15], 16)),
    }


def control_api(crawler: FakeCrawler, token: str) -> FastAPI:
    """调度中心转发的控制和查询接口。"""

    def authorized(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
        ],
    ) -> None:
        supplied = credentials.credentials if credentials else ""
        if not secrets.compare_digest(supplied, token):
            raise HTTPException(401, "服务凭据无效")

    app = FastAPI(title="模拟爬取程序", dependencies=[Depends(authorized)])

    @app.post("/api/batches/{batch_id}/{action}", status_code=204)
    def control_batch(batch_id: str, action: str) -> None:
        if action not in ("pause", "resume", "cancel"):
            raise HTTPException(404)
        crawler.paused.discard(batch_id)
        if action == "pause":
            crawler.paused.add(batch_id)
        elif action == "cancel":
            crawler.cancelled.add(batch_id)

    @app.post("/api/tasks/{task_id}/cancel", status_code=204)
    def cancel_task(task_id: str) -> None:
        crawler.cancelled.add(task_id)

    @app.get("/api/status")
    def status() -> dict:
        """格式和真实爬取程序相同；两类任务在同一个队列里，排队数都算在获取作品上。"""
        running = crawler.running
        return {
            "crawl": {
                "running": running["crawl_creator"],
                "queued": crawler.broker.client.llen(f"{crawler.broker.namespace}:{TASKS}"),
            },
            "download": {"running": running["download_video"], "queued": 0},
            "identities": None,
            "leases": len(crawler.leases),
            "paused_batches": sorted(crawler.paused),
            "download_paused_until": None,
        }

    @app.post("/api/proxy-tests")
    def proxy_tests(body: dict) -> list[dict]:
        """调度中心请本机代为测试一批代理，并行进行。"""
        with ThreadPoolExecutor(max_workers=10) as pool:
            return list(pool.map(lambda item: check_proxy(item, body["echo_url"]), body["items"]))

    speed_lock = threading.Lock()

    @app.post("/api/proxy-speed")
    def proxy_speed(body: dict) -> dict:
        """经代理下载测速文件。本机同时只测一个，免得几个测速互相抢带宽。"""
        with speed_lock:
            return measure_speed(body)

    return app


def keep_registered(
    scheduler: httpx.Client, crawler: FakeCrawler, url: str, stop: threading.Event
) -> None:
    """注册兼心跳，同时为正在用的线路续期。"""
    while not stop.is_set():
        body = {"url": url, "version": "fake", "leases": sorted(crawler.leases)}
        try:
            scheduler.put(f"/api/crawler-services/{crawler.instance}", json=body)
        except httpx.HTTPError as error:
            log.warning("注册失败，稍后重试：%s", error)
        stop.wait(15)


def main() -> None:
    parser = argparse.ArgumentParser(description="模拟爬取程序，只用于开发和联调")
    parser.add_argument("--samples", type=Path, required=True, help="样例目录，每位达人一个子目录")
    parser.add_argument("--scheduler", default="http://localhost:8080")
    parser.add_argument("--redis", default="redis://localhost:6379/0")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--public-url", help="调度中心访问本程序的地址，默认 http://<host>:<port>")
    parser.add_argument(
        "--instance", help="设备标识，默认 fake-<主机名>；本机模拟多台设备时各填一个"
    )
    args = parser.parse_args()
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    token = os.environ["SC_SERVICE_TOKEN"]
    broker = RedisBroker(url=args.redis)
    scheduler = httpx.Client(
        base_url=args.scheduler,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    instance = args.instance or f"fake-{socket.gethostname()}"
    crawler = FakeCrawler(args.samples, scheduler, broker, instance)
    for handler in (crawler.crawl_creator, crawler.download_video):
        dramatiq.actor(
            crawler.tracked(handler), broker=broker, actor_name=handler.__name__, queue_name=TASKS
        )
    # 只消费任务队列；事件队列也声明在这个 broker 上，但由调度中心消费
    worker = dramatiq.Worker(broker, queues={TASKS}, worker_threads=4)
    worker.start()

    stop = threading.Event()
    url = args.public_url or f"http://{args.host}:{args.port}"
    threading.Thread(
        target=keep_registered, args=(scheduler, crawler, url, stop), daemon=True
    ).start()
    try:
        uvicorn.run(control_api(crawler, token), host=args.host, port=args.port)
    finally:
        stop.set()
        worker.stop()
        with contextlib.suppress(httpx.HTTPError):
            scheduler.delete(f"/api/crawler-services/{instance}")


if __name__ == "__main__":
    main()
