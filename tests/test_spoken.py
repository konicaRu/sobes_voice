from app.llm import LlmError
from app.spoken import SpokenDraft, build_messages, humanize_question

QUESTION = "Расскажите про A/B-тест, который вы запускали."


class FakeLlm:
    def __init__(self, reply: "SpokenDraft | Exception"):
        self.reply = reply
        self.calls = []

    def chat_json(self, messages, model_cls, **kwargs):
        self.calls.append((messages, model_cls, kwargs))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_messages_hold_prompt_and_question():
    system, user = build_messages(QUESTION)
    assert system["role"] == "system" and user["role"] == "user"
    assert "паразит" in system["content"] and "не добавляй" in system["content"].lower()
    assert QUESTION in user["content"]


def test_returns_model_wording_with_normalised_spaces():
    llm = FakeLlm(SpokenDraft(spoken="  Ну,   расскажите  про A/B-тест, который вы запускали.  "))
    assert humanize_question(llm, QUESTION) == "Ну, расскажите про A/B-тест, который вы запускали."
    assert llm.calls[0][1] is SpokenDraft


def test_falls_back_to_original_when_model_is_down():
    assert humanize_question(FakeLlm(LlmError("нет связи")), QUESTION) == QUESTION


def test_falls_back_on_empty_reply():
    assert humanize_question(FakeLlm(SpokenDraft(spoken="   ")), QUESTION) == QUESTION


def test_falls_back_when_model_rambles():
    rambling = "Ну так вот, " + "давайте поговорим подробно. " * 20
    assert humanize_question(FakeLlm(SpokenDraft(spoken=rambling)), QUESTION) == QUESTION
