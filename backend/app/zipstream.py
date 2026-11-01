"""Потоковая отдача ZIP: архив собирается в отдельном потоке и уходит клиенту по частям.

Зачем: на больших пакетах (десятки сайтов × документы Word, скриншоты, снимки) сборка занимает минуты.
Если собирать архив целиком, nginx не дождётся первых байтов (proxy_read_timeout) и вернёт 504,
а синхронная сборка в обработчике блокирует остальные запросы API.
"""

import asyncio
import io
import logging
import queue
import threading
import zipfile
from collections.abc import AsyncIterator, Callable

log = logging.getLogger(__name__)
CHUNK = 256 * 1024


class _Pipe(io.RawIOBase):
    """Файлоподобный приёмник: ZipFile пишет сюда, порции уходят в очередь. Без seek — ZipFile это поддерживает."""

    def __init__(self, q: queue.Queue, cancelled: threading.Event):
        self.q, self.cancelled, self.buf = q, cancelled, bytearray()

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        self.buf += b
        if len(self.buf) >= CHUNK:
            self._send()
        return len(b)

    def _send(self) -> None:
        data, self.buf = bytes(self.buf), bytearray()
        while not self.cancelled.is_set():
            try:
                self.q.put(data, timeout=1)
                return
            except queue.Full:
                continue
        raise BrokenPipeError("клиент прервал скачивание")

    def flush(self) -> None:
        if self.buf:
            self._send()


def stream_zip(build: Callable[[zipfile.ZipFile], None]) -> AsyncIterator[bytes]:
    """build(zf) выполняется в отдельном потоке; возвращает асинхронный поток байтов архива."""
    q: queue.Queue = queue.Queue(maxsize=8)
    cancelled = threading.Event()
    done = object()

    def worker() -> None:
        try:
            pipe = _Pipe(q, cancelled)
            with zipfile.ZipFile(pipe, "w", zipfile.ZIP_DEFLATED) as zf:
                build(zf)
            pipe.flush()
        except BrokenPipeError:
            return
        except Exception as e:  # noqa: BLE001
            log.exception("zip build failed")
            q.put(e)
        finally:
            if not cancelled.is_set():
                q.put(done)

    threading.Thread(target=worker, daemon=True, name="zipstream").start()

    async def gen() -> AsyncIterator[bytes]:
        try:
            while True:
                item = await asyncio.to_thread(q.get)
                if item is done:
                    return
                if isinstance(item, Exception):
                    raise item  # архив оборван — браузер покажет ошибку скачивания, а не битый файл как целый
                yield item
        finally:
            cancelled.set()

    return gen()
