import asyncio
import io
import os
import time
import zipfile

import pytest

from app.zipstream import stream_zip


def test_first_bytes_arrive_before_build_finishes(tmp_path):
    big = tmp_path / "shot.jpg"
    big.write_bytes(os.urandom(600_000))  # несжимаемые данные, как скриншот

    def build(zf):
        zf.write(big, "files/a/shot.jpg", compress_type=zipfile.ZIP_STORED)  # путь с файлом на диске
        for i in range(3):
            time.sleep(0.4)  # медленная генерация документов
            zf.writestr(f"doc{i}.txt", "Политика " * 1000)

    async def main():
        t0, first, chunks = time.monotonic(), None, []
        async for c in stream_zip(build):
            first = first or time.monotonic() - t0
            chunks.append(c)
        return first, time.monotonic() - t0, b"".join(chunks)

    first, total, data = asyncio.run(main())
    assert first < 0.5 < total  # nginx получает байты сразу, а не через всю сборку
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.testzip() is None
    assert z.read("files/a/shot.jpg") == big.read_bytes() and z.read("doc2.txt").startswith("Политика".encode())


def test_build_error_is_not_a_complete_file():
    def build(zf):
        zf.writestr("a.txt", "x")
        raise RuntimeError("сбой при генерации документа")

    async def main():
        async for _ in stream_zip(build):
            pass

    with pytest.raises(RuntimeError):
        asyncio.run(main())


def test_client_abort_stops_build():
    built = []

    def build(zf):
        for i in range(200):
            zf.writestr(f"f{i}.bin", os.urandom(300_000))
            built.append(i)

    async def main():
        agen = stream_zip(build)
        await agen.__anext__()  # клиент получил первую порцию и закрыл соединение
        await agen.aclose()
        await asyncio.sleep(1.5)

    asyncio.run(main())
    assert len(built) < 200  # сборка остановилась, а не крутится вхолостую
