"""识别粘贴或导入的代理文本，每行一条。

支持 主机:端口、主机:端口:用户名:密码、用户名:密码@主机:端口 和 协议://用户名:密码@主机:端口；
也可以用逗号、制表符或空格分隔成 主机 端口 [用户名 密码]。IPv6 主机要加方括号。
没写协议的记为“自动”，首次测试时再确定。
"""

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from scheduler_center.modules.proxy.models import ProxyProtocol

SCHEMES = {
    "http": ProxyProtocol.HTTP,
    "https": ProxyProtocol.HTTPS,
    "socks5": ProxyProtocol.SOCKS5,
    "socks5h": ProxyProtocol.SOCKS5,
    "socks": ProxyProtocol.SOCKS5,
    "ssh": ProxyProtocol.SSH,
}
HOST = re.compile(r"^[A-Za-z0-9.-]+$")


class Unrecognized(ValueError):
    pass


@dataclass
class Address:
    host: str
    port: int
    protocol: ProxyProtocol = ProxyProtocol.AUTO
    username: str = ""
    password: str = ""


@dataclass
class ParsedLine:
    number: int
    text: str
    address: Address | None = None
    error: str | None = None


def parse_lines(text: str) -> list[ParsedLine]:
    """逐行识别；空行和 # 开头的注释行跳过。"""
    rows = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(ParsedLine(number, line, address=parse(line)))
        except Unrecognized as error:
            rows.append(ParsedLine(number, line, error=str(error)))
    return rows


def parse(line: str) -> Address:
    fields = re.split(r"[,\t ]+", line)
    if len(fields) in (2, 4):
        return _address(*fields)
    if len(fields) > 1:
        raise Unrecognized("分隔后应为 主机 端口，或 主机 端口 用户名 密码")
    if "://" in line:
        return _url(line)
    if "@" in line:  # 密码里也可能有 @，按最后一个拆；拆不成再按冒号格式识别
        credentials, _, endpoint = line.rpartition("@")
        username, _, password = credentials.partition(":")
        try:
            return _address(*_endpoint(endpoint), username, password)
        except Unrecognized:
            pass
    host, rest = _host(line)
    parts = rest.split(":", 2)  # 第三个冒号之后都算密码
    if len(parts) in (1, 3):
        return _address(host, *parts)
    raise Unrecognized("无法识别的格式")


def _url(line: str) -> Address:
    url = urlsplit(line)
    protocol = SCHEMES.get(url.scheme.lower())
    if protocol is None:
        raise Unrecognized(f"不支持的协议 {url.scheme}")
    try:
        port = url.port
    except ValueError as error:
        raise Unrecognized("端口应为 1～65535 的数字") from error
    if not url.hostname or port is None:
        raise Unrecognized("缺少主机或端口")
    address = _address(
        url.hostname, str(port), unquote(url.username or ""), unquote(url.password or "")
    )
    address.protocol = protocol
    return address


def _endpoint(text: str) -> tuple[str, str]:
    host, rest = _host(text)
    if not rest or ":" in rest:
        raise Unrecognized("@ 后面应为 主机:端口")
    return host, rest


def _host(text: str) -> tuple[str, str]:
    """拆出主机和后面的部分；IPv6 主机写在方括号里。"""
    if text.startswith("["):
        host, bracket, rest = text[1:].partition("]")
        if not bracket or not rest.startswith(":"):
            raise Unrecognized("IPv6 主机应写成 [地址]:端口")
        return host, rest[1:]
    host, _, rest = text.partition(":")
    if text.count(":") > 1 and not rest.partition(":")[0].isdigit():
        raise Unrecognized("IPv6 主机要加方括号，例如 [2001:db8::1]:1080")
    return host, rest


def _address(host: str, port: str, username: str = "", password: str = "") -> Address:
    host = host.strip("[]")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not HOST.match(host):
            raise Unrecognized(f"主机 {host or '为空'} 无效") from None
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise Unrecognized("端口应为 1～65535 的数字")
    if bool(username) != bool(password):
        raise Unrecognized("用户名和密码要同时填写")
    return Address(host, int(port), username=username, password=password)
