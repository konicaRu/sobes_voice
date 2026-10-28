from pydantic import BaseModel


class Question(BaseModel):
    id: str
    text: str
    category: str = "technical"
    what_to_check: list[str] = []
    expected_points: list[str] = []
