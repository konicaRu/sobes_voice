from app.profile import MAX_PROFILE_CHARS, MAX_SOURCE_CHARS, ProfileDraft, build_profile, build_profile_messages


class FakeLlm:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def chat_json(self, messages, model_cls, **kwargs):
        self.calls.append((messages, model_cls, kwargs))
        return ProfileDraft(profile=self.text)


def test_messages_label_resume_and_cases():
    messages = build_profile_messages(["Резюме: SQL, ClickHouse"], ["Кейс: retention вырос на 5%"])
    system, user = messages[0]["content"], messages[1]["content"]
    assert "не выдумывай" in system.lower()
    assert "Резюме" in user and "SQL, ClickHouse" in user
    assert "Кейсы" in user and "retention вырос на 5%" in user


def test_empty_cases_section_is_skipped():
    user = build_profile_messages(["Резюме"], [])[1]["content"]
    assert "Кейсы" not in user


def test_long_sources_are_cut_to_the_limit():
    user = build_profile_messages(["р" * (MAX_SOURCE_CHARS * 3)], ["к" * (MAX_SOURCE_CHARS * 3)])[1]["content"]
    assert len(user) < 2 * MAX_SOURCE_CHARS + 500
    assert "р" * MAX_SOURCE_CHARS in user and "к" * MAX_SOURCE_CHARS in user


def test_build_profile_returns_trimmed_text_within_limit():
    llm = FakeLlm("  " + "ф" * (MAX_PROFILE_CHARS + 500) + "  ")
    profile = build_profile(llm, ["Резюме"], [])
    assert len(profile) == MAX_PROFILE_CHARS
    assert llm.calls[0][1] is ProfileDraft
    assert llm.calls[0][2]["temperature"] == 0.0
