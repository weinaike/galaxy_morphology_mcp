from src.tools.fit_event_publisher import publish_fit_round


# modified by zl: verify standalone tools do not perform network communication.
def test_publish_fit_round_is_noop_without_service_environment(monkeypatch):
    monkeypatch.delenv("FIT_ROUND_EVENT_URL", raising=False)
    monkeypatch.delenv("FIT_ROUND_CALLBACK_URL", raising=False)

    assert publish_fit_round({"event": "fit_round_finished"}) == {
        "attempted": False, "status": "not_configured",
    }


# modified by zl: verify service-injected event delivery.
def test_publish_fit_round_uses_injected_event_url(monkeypatch):
    calls = []

    class Response:
        ok = True
        status_code = 200

    # modified by zl: capture the event publisher HTTP contract.
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setenv("FIT_ROUND_EVENT_URL", "http://service/task/fit-events")
    monkeypatch.setattr("src.tools.fit_event_publisher.requests.post", post)
    result = publish_fit_round({"event": "fit_round_finished", "fitter": "galfit"})

    assert result == {"attempted": True, "status": "sent", "http_status": 200}
    assert calls[0][0] == "http://service/task/fit-events"
    assert calls[0][1]["json"]["fitter"] == "galfit"
