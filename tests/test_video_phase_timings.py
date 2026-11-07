from tests.native_video_fixtures import setup as generation_setup, action


def test_generation_receipt_distinguishes_preparation_and_transport_without_resubmitting(generation_setup):
    _, service, backend, _, _, payload, _ = generation_setup
    prepared = service.run(payload)
    assert prepared['timings']['preparedAt'] > 0
    submitted = service.run(action(payload, 'submit'))
    timings = submitted['timings']
    assert timings['submitStartedAt'] >= prepared['timings']['preparedAt']
    assert timings['acceptedAt'] >= timings['submitStartedAt']
    assert timings['submitDurationMs'] >= 0
    repeated = service.run(action(payload, 'submit'))
    assert repeated['timings']['acceptedAt'] == timings['acceptedAt']
    assert len(backend.submitted) == 1
