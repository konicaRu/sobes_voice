import pytest

from app.models import Attempt, Question
from app.storage import NotFoundError, Storage, StorageError

QUESTIONS = [
    Question(id="q01", text="Что такое SRM?", what_to_check=["пропорции"]),
    Question(id="q02", text="Что такое MDE?"),
]


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path)


def make_interview(storage, title="Т2 — продуктовый аналитик"):
    return storage.create_interview(title)


def test_create_interview_makes_safe_unique_ids(storage):
    first, second = make_interview(storage), make_interview(storage)
    assert first.title == "Т2 — продуктовый аналитик"
    assert first.id != second.id
    assert all(i.id.replace("-", "").isalnum() and i.id == i.id.lower() and i.id.isascii() for i in (first, second))


@pytest.mark.parametrize("title", ["", "   ", "я" * 101])
def test_bad_titles_are_rejected(storage, title):
    with pytest.raises(StorageError):
        storage.create_interview(title)


def test_title_without_letters_still_gets_an_id(storage):
    assert make_interview(storage, "!!!").id


def test_interviews_survive_restart(tmp_path):
    created = make_interview(Storage(tmp_path))
    listed = Storage(tmp_path).list_interviews()
    assert [i.id for i in listed] == [created.id]
    assert Storage(tmp_path).get_interview(created.id).title == created.title


@pytest.mark.parametrize("bad_id", ["../x", "nope", "", "a/b", "..\\x"])
def test_unknown_or_malicious_interview_id_is_not_found(storage, bad_id):
    with pytest.raises(NotFoundError):
        storage.get_interview(bad_id)


def test_documents_are_saved_listed_read_and_deleted(storage):
    interview = make_interview(storage)
    name = storage.save_document(interview.id, "vacancy", "../вакансия.md", "Нужен аналитик".encode())
    assert name == "вакансия.md"
    assert storage.list_documents(interview.id) == {"questions": [], "vacancy": ["вакансия.md"], "resume": [], "cases": []}
    assert storage.read_document(interview.id, "vacancy", name) == "Нужен аналитик".encode()
    storage.delete_document(interview.id, "vacancy", name)
    assert storage.list_documents(interview.id)["vacancy"] == []


def test_duplicate_document_names_get_a_suffix(storage):
    interview = make_interview(storage)
    first = storage.save_document(interview.id, "cases", "кейс.md", b"1")
    second = storage.save_document(interview.id, "cases", "кейс.md", b"2")
    assert (first, second) == ("кейс.md", "кейс (2).md")
    assert storage.read_document(interview.id, "cases", first) == b"1"


def test_unknown_slot_is_rejected(storage):
    interview = make_interview(storage)
    with pytest.raises(StorageError, match="слот"):
        storage.save_document(interview.id, "secrets", "a.md", b"x")


def test_reading_outside_the_slot_folder_is_not_found(storage):
    interview = make_interview(storage)
    storage.save_document(interview.id, "resume", "cv.md", b"x")
    for name in ["../../interview.json", "..\\cv.md", "missing.md"]:
        with pytest.raises(NotFoundError):
            storage.read_document(interview.id, "resume", name)
        with pytest.raises(NotFoundError):
            storage.delete_document(interview.id, "resume", name)


def test_too_many_documents_in_a_slot_are_rejected(storage):
    interview = make_interview(storage)
    for i in range(20):
        storage.save_document(interview.id, "cases", f"c{i}.md", b"x")
    with pytest.raises(StorageError, match="слишком много"):
        storage.save_document(interview.id, "cases", "extra.md", b"x")


def test_questions_roundtrip_and_survive_restart(tmp_path):
    storage = Storage(tmp_path)
    interview = make_interview(storage)
    assert storage.load_questions(interview.id) is None
    storage.save_questions(interview.id, QUESTIONS)
    assert Storage(tmp_path).load_questions(interview.id) == QUESTIONS


def test_session_requires_approved_questions(storage):
    interview = make_interview(storage)
    with pytest.raises(StorageError, match="Утвердите"):
        storage.create_session(interview.id)


def test_session_snapshots_questions_and_survives_restart(tmp_path):
    storage = Storage(tmp_path)
    interview = make_interview(storage)
    storage.save_questions(interview.id, QUESTIONS)
    session = storage.create_session(interview.id)
    storage.save_questions(interview.id, QUESTIONS[:1])  # правка списка не меняет начатую сессию

    session.attempts.append(Attempt(q=0, n=1, text="ответ", seconds=3.0, audio="audio/q01_a1.wav"))
    storage.save_session(session)

    loaded = Storage(tmp_path).load_session(session.sid)
    assert loaded.questions == QUESTIONS
    assert loaded.attempts[0].text == "ответ"
    assert loaded.interview_id == interview.id


def test_two_sessions_in_the_same_second_get_different_ids(storage):
    interview = make_interview(storage)
    storage.save_questions(interview.id, QUESTIONS)
    assert storage.create_session(interview.id).sid != storage.create_session(interview.id).sid


@pytest.mark.parametrize("bad_sid", ["../../x", "nope", "a__b", "x__20260101-000000/../../y"])
def test_malicious_session_id_is_not_found(storage, bad_sid):
    with pytest.raises(NotFoundError):
        storage.load_session(bad_sid)


def test_audio_is_saved_next_to_the_session(storage):
    interview = make_interview(storage)
    storage.save_questions(interview.id, QUESTIONS)
    session = storage.create_session(interview.id)
    relative = storage.save_audio(session, q=1, n=2, wav=b"RIFFdata")
    assert relative == "audio/q02_a2.wav"
    assert storage.audio_path(session, relative).read_bytes() == b"RIFFdata"
