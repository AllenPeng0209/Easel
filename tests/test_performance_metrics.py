import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.access_integration


def test_server_timing_reports_setup_time_without_changing_the_response():
    from web.performance import PerformanceMiddleware
    app = FastAPI()
    app.add_middleware(PerformanceMiddleware)

    @app.get('/ready')
    def ready():
        return {'ready': True}

    result = TestClient(app).get('/ready')
    assert result.json() == {'ready': True}
    assert result.headers['server-timing'].startswith('app;dur=')
    assert len(result.headers['x-request-id']) == 32


def test_recent_metrics_are_bounded_and_kept_in_their_project_cohort(tmp_path):
    from web.performance import PerformanceStore
    store = PerformanceStore(tmp_path, limit=4)
    def row(value):
        return {'metric': 'api', 'label': 'outputs', 'value_ms': value, 'network': 'unknown', 'device': 'desktop'}
    store.record('owner', 'alpha', [row(1), row(2), row(3)])
    store.record('other', 'beta', [row(100)])
    first = store.summary('alpha')[0]
    assert first['count'] == 3 and first['p50_ms'] == 2 and first['p95_ms'] == 3
    assert store.summary('beta')[0]['p50_ms'] == 100
    store.record('owner', 'alpha', [row(5)])
    recent = store.summary('alpha')[0]
    assert recent['count'] == 3 and recent['p50_ms'] == 3 and recent['max_ms'] == 5


def test_metric_payload_rejects_urls_and_nonfinite_values():
    from web.performance import MetricBatch
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        MetricBatch.model_validate({'metrics': [{'metric': 'api', 'label': 'https://private.test', 'value_ms': 1}]})
    with pytest.raises(ValidationError):
        MetricBatch.model_validate({'metrics': [{'metric': 'api', 'label': 'outputs', 'value_ms': float('nan')}]})
