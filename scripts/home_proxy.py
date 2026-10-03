#!/usr/bin/env python3
"""住宅出口代理：极简 SOCKS5（仅 CONNECT），给服务器的抖音抓取当出口用。

背景：抖音对机房 IP 的视频详情接口返回风控页（2026-09-27 实测），住宅 IP 正常。
部署形态（见 scripts/home-proxy.sh）：
  本机  python3 scripts/home_proxy.py --port 11080      ← 出口（用自己的家宽 IP 直连）
  本机  ssh -N -R 11080:127.0.0.1:11080 <服务器>        ← 把该端口映射到服务器 localhost
  服务器 douyin_proxy=socks5://127.0.0.1:11080          ← 抓取流量经此出口

安全：只监听本机回环（默认 127.0.0.1），且服务器侧 ssh -R 默认也只绑回环；
不做认证（端口不出本机/不暴露公网）。仅支持 CONNECT（浏览器的 HTTPS 走 CONNECT）。
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import socket
import struct


# 需要经上游外网代理出海的目标（家宽直连不通的境外站点，按后缀匹配）
UPSTREAM_SUFFIXES = ("duckduckgo.com",)
_UPSTREAM = 0  # 上游代理端口（--upstream 传入；0=不启用）


def _needs_upstream(host: str) -> bool:
    h = (host or "").lower()
    return any(h == s or h.endswith("." + s) for s in UPSTREAM_SUFFIXES)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:  # noqa: BLE001 连接中断属常态
        pass
    finally:
        writer.close()


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        # 握手：VER(1) NMETHODS(1) METHODS(n)
        head = await reader.readexactly(2)
        if head[0] != 0x05:
            writer.close()
            return
        await reader.readexactly(head[1])
        writer.write(b"\x05\x00")  # 无需认证
        await writer.drain()

        # 请求：VER CMD RSV ATYP DST.ADDR DST.PORT
        req = await reader.readexactly(4)
        if req[1] != 0x01:  # 仅 CONNECT
            writer.write(b"\x05\x07\x00\x01" + b"\x00" * 6)
            await writer.drain()
            writer.close()
            return
        atyp = req[3]
        if atyp == 0x01:  # IPv4
            host = socket.inet_ntoa(await reader.readexactly(4))
        elif atyp == 0x03:  # 域名
            ln = (await reader.readexactly(1))[0]
            host = (await reader.readexactly(ln)).decode("utf-8", "replace")
        elif atyp == 0x04:  # IPv6
            host = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
        else:
            writer.close()
            return
        port = struct.unpack("!H", await reader.readexactly(2))[0]

        # 只允许公网地址（防被当成内网探测跳板）
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except Exception:  # noqa: BLE001
            writer.write(b"\x05\x04\x00\x01" + b"\x00" * 6)
            await writer.drain()
            writer.close()
            return
        target_ip = infos[0][4][0]
        ip = ipaddress.ip_address(target_ip)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            writer.write(b"\x05\x02\x00\x01" + b"\x00" * 6)  # 规则禁止
            await writer.drain()
            writer.close()
            return

        # 上游外网代理：部分境外目标（duckduckgo 等）家宽直连不通，走本机 7890（HTTP 代理）出海
        if _UPSTREAM and _needs_upstream(host):
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", _UPSTREAM)
            req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n"
            up_writer.write(req.encode())
            await up_writer.drain()
            status_line = await up_reader.readline()
            if b" 200 " not in status_line:
                writer.write(b"\x05\x01\x00\x01" + b"\x00" * 6)  # 上游拒绝
                await writer.drain()
                writer.close()
                return
            while (await up_reader.readline()) not in (b"\r\n", b"\n", b""):
                pass  # 吃掉响应头
        else:
            up_reader, up_writer = await asyncio.open_connection(target_ip, port)
        writer.write(b"\x05\x00\x00\x01" + b"\x00" * 6)  # 成功
        await writer.drain()
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
    except Exception:  # noqa: BLE001 任何异常仅断这条连接
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def main(host: str, port: int) -> None:
    server = await asyncio.start_server(_handle, host, port, limit=2**20)
    print(f"[home-proxy] SOCKS5 出口已监听 {host}:{port}（仅 CONNECT，禁内网目标）", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--upstream", type=int, default=0,
                    help="本机外网代理端口（如 7890）；>0 时 duckduckgo.com 等境外目标经它出海")
    ap.add_argument("--port", type=int, default=11080)
a = ap.parse_args()
_UPSTREAM = a.upstream
try:
    asyncio.run(main(a.host, a.port))
except KeyboardInterrupt:
    pass
