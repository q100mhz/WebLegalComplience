import asyncio

from app.worker import Gate


def test_gate_limits_and_changes_on_the_fly():
    async def main():
        gate = Gate(1)
        peak, running, order = 0, 0, []

        async def job(i):
            nonlocal peak, running
            await gate.acquire()
            running += 1
            peak = max(peak, running)
            order.append(i)
            await asyncio.sleep(0.05)
            running -= 1
            await gate.release()

        tasks = [asyncio.create_task(job(i)) for i in range(6)]
        await asyncio.sleep(0.02)
        assert peak == 1
        await gate.set_limit(3)  # админ поднял лимит — ожидающие стартуют сразу
        await asyncio.gather(*tasks)
        return peak, order

    peak, order = asyncio.run(main())
    assert peak == 3
    assert order == sorted(order)  # порядок поступления сохраняется
