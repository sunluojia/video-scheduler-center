"""模拟爬取程序的测速：只有完整的 200 响应、字节数和预期一致才算下完。

uv run --project backend python -m pytest tools/fake_crawler -q
"""

import httpx
import pytest
from fake_crawler import Incomplete, download

SIZE = 10


def client(status: int, body: bytes, headers: dict | None = None) -> httpx.Client:
    response = httpx.Response(status, content=body, headers=headers)
    return httpx.Client(transport=httpx.MockTransport(lambda _: response))


def test_a_complete_file_counts():
    assert download(client(200, b"0" * SIZE), "https://speed.test/file", SIZE) == SIZE


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (206, b"01234", "测速地址返回 206"),
        (200, b"", "收到 0 字节，应为 10 字节"),
        (200, b"<html>blocked</html>", "收到 20 字节，应为 10 字节"),
        (200, b"01234", "收到 5 字节，应为 10 字节"),
        (403, b"0", "测速地址返回 403"),
    ],
    ids=["部分内容", "空正文", "网页", "截断", "拒绝"],
)
def test_partial_empty_or_html_responses_fail(status, body, reason):
    with pytest.raises(Incomplete, match=reason):
        download(client(status, body), "https://speed.test/file", SIZE)
