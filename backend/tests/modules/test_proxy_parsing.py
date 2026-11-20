"""代理文本识别：不需要数据库。"""

import pytest

from scheduler_center.modules.proxy.models import ProxyProtocol, exit_line
from scheduler_center.modules.proxy.parsing import parse_lines


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.2.3.4:8080", ("1.2.3.4", 8080, ProxyProtocol.AUTO, "", "")),
        ("1.2.3.4:8080:alice:pa:ss", ("1.2.3.4", 8080, ProxyProtocol.AUTO, "alice", "pa:ss")),
        (
            "alice:p@ss@proxy.example:1080",
            ("proxy.example", 1080, ProxyProtocol.AUTO, "alice", "p@ss"),
        ),
        (
            "alice:1234@proxy.example:1080",
            ("proxy.example", 1080, ProxyProtocol.AUTO, "alice", "1234"),
        ),
        (
            "socks5h://a%40b:pw@[2001:db8::1]:1080",
            ("2001:db8::1", 1080, ProxyProtocol.SOCKS5, "a@b", "pw"),
        ),
        ("[2001:db8::2]:3128", ("2001:db8::2", 3128, ProxyProtocol.AUTO, "", "")),
        ("5.6.7.8,1080,alice,secret", ("5.6.7.8", 1080, ProxyProtocol.AUTO, "alice", "secret")),
        ("5.6.7.8\t1080", ("5.6.7.8", 1080, ProxyProtocol.AUTO, "", "")),
        ("ssh://root:pw@10.0.0.1:22", ("10.0.0.1", 22, ProxyProtocol.SSH, "root", "pw")),
    ],
)
def test_supported_formats(text, expected):
    [row] = parse_lines(text)
    address = row.address
    assert (address.host, address.port, address.protocol, address.username, address.password) == (
        expected
    )


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("2001:db8::3:1080", "IPv6 主机要加方括号"),
        ("http://h:99999", "端口应为"),
        ("host:80:onlyuser", "无法识别"),
        ("ftp://h:21", "不支持的协议"),
        ("1.2.3.4 80 alice", "分隔后应为"),
        ("bad_host!:80", "主机 bad_host! 无效"),
    ],
)
def test_unrecognized_lines_explain_why(text, error):
    [row] = parse_lines(text)
    assert row.address is None and error in row.error


def test_blank_and_comment_lines_are_skipped_but_numbers_follow_the_text():
    rows = parse_lines("\n# 备用\n1.2.3.4:80\n")
    assert [row.number for row in rows] == [3]


def test_ipv6_exits_share_a_line_per_64_network():
    assert exit_line("203.0.113.7") == "203.0.113.7"
    assert exit_line("2001:db8:1:2::5") == exit_line("2001:db8:1:2:abcd::9") == "2001:db8:1:2::/64"
