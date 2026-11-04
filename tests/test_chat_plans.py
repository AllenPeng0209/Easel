from concurrent.futures import ThreadPoolExecutor

import pytest


SCOPE = {"actor_id": "owner-a", "project_id": "ip-a", "session_id": "session-a"}


def make_store(tmp_path):
    from easel.chat_plans import ChatPlanStore
    return ChatPlanStore(tmp_path)


def propose(store, text="Write two scripts\n", call_id="call-a", **scope):
    return store.propose(**(SCOPE | scope), text=text, turn_id="planning-turn", call_id=call_id)


def exact(record):
    return {"plan_id": record["planId"], "revision": record["revision"], "digest": record["digest"]}


def test_plan_requires_an_explicit_approval_of_exact_immutable_text(tmp_path):
    from easel.chat_plans import ChatPlanError

    store = make_store(tmp_path)
    plan = propose(store)
    assert plan["state"] == "proposed"
    assert store.get(**SCOPE, plan_id=plan["planId"]) == plan
    with pytest.raises(ChatPlanError):
        store.claim_execution(**SCOPE, **exact(plan), turn_id="execution-turn", request_id="request-a")
    with pytest.raises(ChatPlanError):
        store.approve(**SCOPE, **(exact(plan) | {"digest": "0" * 64}))
    approved = store.approve(**SCOPE, **exact(plan))
    assert approved["state"] == "approved"
    assert approved["text"] == plan["text"] and approved["digest"] == plan["digest"]
    assert make_store(tmp_path).approve(**SCOPE, **exact(plan)) == approved


def test_revised_plan_invalidates_prior_approval_and_preserves_history(tmp_path):
    from easel.chat_plans import ChatPlanError

    store = make_store(tmp_path)
    first = propose(store)
    store.approve(**SCOPE, **exact(first))
    updated = propose(store, text="Write three scripts", call_id="call-b")
    assert updated["planId"] == first["planId"] and updated["revision"] == 2
    assert updated["state"] == "proposed"
    assert updated["digest"] != first["digest"]
    assert propose(store)["state"] == "superseded"
    with pytest.raises(ChatPlanError):
        propose(store, text="different content reusing the old call")
    with pytest.raises(ChatPlanError):
        store.claim_execution(**SCOPE, **exact(first), turn_id="execution-turn", request_id="request-a")
    assert [row["state"] for row in store.history(**SCOPE)] == ["superseded", "proposed"]
    assert store.get(**SCOPE, plan_id=first["planId"], revision=1)["text"] == first["text"]


def test_exact_execution_claim_has_one_winner_across_connections(tmp_path):
    from easel.chat_plans import ChatPlanError

    store = make_store(tmp_path)
    plan = propose(store)
    store.approve(**SCOPE, **exact(plan))
    with pytest.raises(ChatPlanError):
        store.claim_execution(**SCOPE, **exact(plan), turn_id="planning-turn", request_id="request-a")

    def try_claim(index):
        try:
            return make_store(tmp_path).claim_execution(**SCOPE, **exact(plan), turn_id="execution-turn",
                                                        request_id=f"request-{index}")
        except ChatPlanError as error:
            assert error.status_code == 409
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = [value for value in pool.map(try_claim, range(8)) if value is not None]
    assert len(claims) == 1
    winner = claims[0]
    assert winner["state"] == "claimed" and winner["executionTurnId"] == "execution-turn"
    assert make_store(tmp_path).get(**SCOPE, plan_id=plan["planId"]) == winner
    assert store.claim_execution(**SCOPE, **exact(plan), turn_id=winner["executionTurnId"],
                                 request_id=winner["executionRequestId"]) == winner
    with pytest.raises(ChatPlanError):
        store.claim_execution(**SCOPE, **exact(plan), turn_id="another-turn", request_id=winner["executionRequestId"])


@pytest.mark.parametrize("scope_key", ["actor_id", "project_id", "session_id"])
def test_plan_scope_cannot_be_read_approved_or_claimed_from_another_identity(tmp_path, scope_key):
    from easel.chat_plans import ChatPlanError

    store = make_store(tmp_path)
    plan = propose(store)
    foreign = SCOPE | {scope_key: "foreign"}
    assert store.history(**foreign) == []
    for action, kwargs in [(store.get, {"plan_id": plan["planId"]}), (store.approve, exact(plan)),
                           (store.claim_execution, exact(plan) | {"turn_id": "turn-b", "request_id": "request-b"})]:
        with pytest.raises(ChatPlanError) as raised:
            action(**foreign, **kwargs)
        assert raised.value.status_code == 404


def test_revision_compare_and_set_preserves_concurrent_approval_and_creation(tmp_path):
    from easel.chat_plans import ChatPlanError

    store = make_store(tmp_path)
    assert store.latest(**SCOPE) is None
    source = store.propose(**SCOPE, text="First", turn_id="proposal", call_id="source", require_empty=True)
    with pytest.raises(ChatPlanError):
        store.propose(**SCOPE, text="Racing first", turn_id="other", call_id="first", require_empty=True)
    expected = {"expected_plan_id": source["planId"], "expected_revision": source["revision"],
                "expected_digest": source["digest"]}
    store.approve(**SCOPE, **exact(source))
    with pytest.raises(ChatPlanError):
        store.propose(**SCOPE, text="Stale edit", turn_id="other", call_id="revision", expected_state="proposed", **expected)
    assert store.latest(**SCOPE)["state"] == "approved"
    replacement = store.propose(**SCOPE, text="Intentional edit", turn_id="other", call_id="revision",
                                expected_state="approved", **expected)
    assert replacement["revision"] == 2
    with pytest.raises(ChatPlanError):
        store.propose(**SCOPE, text="Duplicate stale edit", turn_id="other", call_id="race", **expected)
