"""
Pure quiz-scoring logic — no HTTP/UI/DB-write dependency, so it's directly
testable without a request or an admin form in the loop.
"""

from .models import Quiz


def score_quiz(quiz: Quiz, answers: dict[int, int]) -> tuple[int, bool]:
    """
    `answers` maps question_id -> chosen choice_id. A question with no
    answer (missing key) counts as incorrect, not skipped.

    Returns (score_percent, passed).
    """
    questions = list(quiz.questions.prefetch_related("choices"))
    if not questions:
        return 0, False

    correct_count = 0
    for question in questions:
        chosen_choice_id = answers.get(question.id)
        if chosen_choice_id is None:
            continue
        correct_choice_ids = {choice.id for choice in question.choices.all() if choice.is_correct}
        if chosen_choice_id in correct_choice_ids:
            correct_count += 1

    score_percent = round((correct_count / len(questions)) * 100)
    passed = score_percent >= quiz.passing_score_percent
    return score_percent, passed
