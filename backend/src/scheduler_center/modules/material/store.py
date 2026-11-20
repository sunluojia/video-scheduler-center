"""原视频文件：上传时先写入 incoming 目录，校验通过后移到 <素材 ID>。"""

import hashlib
import os
import time
from collections.abc import AsyncIterator, Collection
from pathlib import Path
from uuid import UUID, uuid4

import anyio


class ContentMismatch(ValueError):
    """收到的内容与声明的大小或 SHA-256 不一致。"""


class MaterialStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._incoming = root / "incoming"

    def path(self, material_id: UUID) -> Path:
        return self._root / str(material_id)

    async def receive(self, chunks: AsyncIterator[bytes], size: int, sha256: str) -> Path:
        """边接收边写入临时文件并计算摘要；不一致时删除临时文件。"""
        self._incoming.mkdir(parents=True, exist_ok=True)
        path = self._incoming / f"{uuid4()}.part"
        digest, received = hashlib.sha256(), 0
        try:
            async with await anyio.open_file(path, "wb") as file:
                async for chunk in chunks:
                    digest.update(chunk)
                    received += len(chunk)
                    await file.write(chunk)
            if received != size or digest.hexdigest() != sha256:
                raise ContentMismatch(f"收到 {received} 字节，与声明的大小或 SHA-256 不一致")
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path

    def keep(self, received: Path, material_id: UUID) -> Path:
        return received.replace(self.path(material_id))

    def sweep(self, live: Collection[str], grace_seconds: float) -> int:
        """删除不再需要的文件：素材已清理、批次已删除，或上传中断留下的临时文件。

        刚写入的文件可能还没登记，留出宽限时间。
        """
        if not self._root.exists():
            return 0
        deadline = time.time() - grace_seconds
        stale = [
            path
            for path in (*self._root.iterdir(), *self._incoming.glob("*.part"))
            if path.is_file() and path.name not in live and path.stat().st_mtime < deadline
        ]
        for path in stale:
            os.remove(path)
        return len(stale)
