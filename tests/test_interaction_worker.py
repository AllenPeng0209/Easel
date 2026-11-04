import asyncio
import threading


def test_worker_processes_enabled_rules_periodically_and_stops():
    from easel.interaction_worker import InteractionWorker

    async def scenario():
        calls = []
        twice = threading.Event()

        def tick():
            calls.append(len(calls))
            if len(calls) >= 2:
                twice.set()

        worker = InteractionWorker(tick, interval=.01)
        worker.start()
        worker.start()  # Starting the same owner twice must not duplicate loops.
        assert await asyncio.to_thread(twice.wait, 1)
        await worker.stop()
        count = len(calls)
        await asyncio.sleep(.03)
        assert len(calls) == count
        assert worker.running is False

    asyncio.run(scenario())


def test_worker_recovers_after_a_failure_without_leaking_upstream_details():
    from easel.interaction_worker import InteractionWorker

    async def scenario():
        recovered = threading.Event()
        calls = 0

        def tick():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError('upstream-private-access-token')
            recovered.set()

        worker = InteractionWorker(tick, interval=.01)
        worker.start()
        assert await asyncio.to_thread(recovered.wait, 1)
        await worker.stop()
        assert calls >= 2
        assert 'upstream-private-access-token' not in worker.last_error

    asyncio.run(scenario())
