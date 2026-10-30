import json
from types import SimpleNamespace as NS

import pytest
from pydantic import BaseModel

from app.llm import LlmCancelled, LlmClient, LlmError, extract_json, strip_think


class Out(BaseModel):
    verdict: str
    score: int


class FakeOpenAI:
    def __init__(self, replies=(), models=("text-embedding-nomic-embed-text-v1.5", "qwen2.5-14b"), error=None):
        self.replies = list(replies)
        self.error = error
        self.requests = []
        self._models = models
        self.models = NS(list=self._list_models)
        self.chat = NS(completions=NS(create=self._create))

    def _list_models(self):
        if self.error:
            raise self.error
        return NS(data=[NS(id=m) for m in self._models])

    def _create(self, **kwargs):
        if self.error:
            raise self.error
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        if kwargs.get("stream"):
            pieces = [reply[i : i + 5] for i in range(0, len(reply), 5)]
            return iter([NS(choices=[NS(delta=NS(content=piece))]) for piece in pieces] + [NS(choices=[])])
        return NS(choices=[NS(message=NS(content=reply))], usage=NS(completion_tokens=10))


def make(fake, **kwargs):
    params: dict = dict(model="auto", temperature=0.3, max_tokens=100, disable_thinking=True)
    params.update(kwargs)
    return LlmClient(fake, **params)


def test_strip_think_removes_block():
    assert strip_think("<think>рассуждаю\nдолго</think>Ответ") == "Ответ"


def test_extract_json_from_fenced_block():
    assert extract_json('Вот:\n```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_from_prose():
    assert extract_json('Результат {"a": 1} готов') == {"a": 1}


def test_extract_json_raises_on_garbage():
    with pytest.raises(ValueError):
        extract_json("никакого json")


def test_auto_model_skips_embedding_models():
    assert make(FakeOpenAI()).get_model() == "qwen2.5-14b"


def test_explicit_model_is_used_as_is():
    assert make(FakeOpenAI(), model="my-model").get_model() == "my-model"


def test_no_loaded_models_gives_russian_error():
    with pytest.raises(LlmError, match="не загружена"):
        make(FakeOpenAI(models=("text-embedding-x",))).get_model()


def test_unreachable_server_gives_russian_error():
    with pytest.raises(LlmError, match="LM Studio"):
        make(FakeOpenAI(error=ConnectionError("refused"))).get_model()


def test_chat_json_parses_into_model_and_sends_schema():
    fake = FakeOpenAI(replies=['{"verdict": "ok", "score": 4}'])
    result = make(fake).chat_json([{"role": "user", "content": "привет"}], Out)
    assert result == Out(verdict="ok", score=4)
    request = fake.requests[0]
    assert request["response_format"]["type"] == "json_schema"
    assert request["model"] == "qwen2.5-14b"


def test_chat_json_temperature_can_be_overridden_per_call():
    fake = FakeOpenAI(replies=['{"verdict": "ok", "score": 4}', '{"verdict": "ok", "score": 4}'])
    client = make(fake)
    client.chat_json([{"role": "user", "content": "x"}], Out)
    client.chat_json([{"role": "user", "content": "x"}], Out, temperature=0.0)
    assert [r["temperature"] for r in fake.requests] == [0.3, 0.0]


def test_chat_json_streams_and_reports_progress_when_callback_given():
    reply = '{"verdict": "ok", "score": 4}'
    fake = FakeOpenAI(replies=[reply])
    seen = []
    result = make(fake).chat_json([{"role": "user", "content": "x"}], Out, on_progress=seen.append)
    assert result == Out(verdict="ok", score=4)
    assert fake.requests[0]["stream"] is True
    assert len(seen) > 1 and seen == sorted(seen) and seen[-1] == len(reply)


def test_chat_json_does_not_stream_without_callback():
    fake = FakeOpenAI(replies=['{"verdict": "ok", "score": 4}'])
    make(fake).chat_json([{"role": "user", "content": "x"}], Out)
    assert not fake.requests[0].get("stream")


def test_streamed_reply_is_retried_and_logged_like_a_normal_one(tmp_path):
    log = tmp_path / "llm.jsonl"
    fake = FakeOpenAI(replies=["не json", '{"verdict": "ok", "score": 4}'])
    result = make(fake, log_path=log).chat_json([{"role": "user", "content": "x"}], Out, on_progress=lambda n: None)
    assert result.score == 4 and len(fake.requests) == 2
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2


def test_chat_json_appends_no_think_when_enabled():
    fake = FakeOpenAI(replies=['{"verdict": "ok", "score": 4}'])
    make(fake).chat_json([{"role": "user", "content": "привет"}], Out)
    assert fake.requests[0]["messages"][-1]["content"].endswith("/no_think")


def test_chat_json_retries_after_invalid_reply_with_error_hint():
    fake = FakeOpenAI(replies=["не json", '{"verdict": "ok", "score": 4}'])
    result = make(fake).chat_json([{"role": "user", "content": "привет"}], Out)
    assert result.score == 4
    assert len(fake.requests) == 2
    assert "JSON" in fake.requests[1]["messages"][-1]["content"]


def test_chat_json_gives_up_after_retries():
    fake = FakeOpenAI(replies=["мусор"] * 3)
    with pytest.raises(LlmError, match="JSON"):
        make(fake).chat_json([{"role": "user", "content": "привет"}], Out)
    assert len(fake.requests) == 3


def test_chat_json_validates_fields():
    fake = FakeOpenAI(replies=['{"verdict": "ok"}', '{"verdict": "ok", "score": 2}'])
    assert make(fake).chat_json([{"role": "user", "content": "x"}], Out).score == 2


def test_calls_are_logged_to_jsonl(tmp_path):
    log = tmp_path / "logs" / "llm.jsonl"
    fake = FakeOpenAI(replies=['{"verdict": "ok", "score": 4}'])
    make(fake, log_path=log).chat_json([{"role": "user", "content": "привет"}], Out)
    record = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert record["response"] == '{"verdict": "ok", "score": 4}'
    assert record["messages"][0]["content"] == "привет"
    assert record["elapsed_s"] >= 0


def test_cancel_during_stream_raises_without_retry():
    fake = FakeOpenAI(replies=['{"verdict": "ok", "score": 4}'])
    answers = iter([False, False, True])  # перед запросом, после первого куска, после второго
    with pytest.raises(LlmCancelled):
        make(fake).chat_json([{"role": "user", "content": "x"}], Out, should_cancel=lambda: next(answers))
    assert len(fake.requests) == 1 and fake.requests[0]["stream"] is True


def test_cancel_before_request_does_not_call_the_model():
    fake = FakeOpenAI(replies=['{"verdict": "ok", "score": 4}'])
    with pytest.raises(LlmCancelled):
        make(fake).chat_json([{"role": "user", "content": "x"}], Out, should_cancel=lambda: True)
    assert fake.requests == []


def test_should_cancel_false_still_returns_result_via_stream():
    fake = FakeOpenAI(replies=['{"verdict": "ok", "score": 4}'])
    result = make(fake).chat_json([{"role": "user", "content": "x"}], Out, should_cancel=lambda: False)
    assert result.score == 4
