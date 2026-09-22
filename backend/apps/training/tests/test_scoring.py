import pytest

from apps.training.scoring import score_quiz
from apps.training.tests.factories import QuizChoiceFactory, QuizFactory, QuizQuestionFactory

pytestmark = pytest.mark.django_db


def _build_quiz(passing_score_percent=80):
    quiz = QuizFactory(passing_score_percent=passing_score_percent)
    q1 = QuizQuestionFactory(quiz=quiz)
    q1_correct = QuizChoiceFactory(question=q1, is_correct=True)
    QuizChoiceFactory(question=q1, is_correct=False)

    q2 = QuizQuestionFactory(quiz=quiz)
    q2_correct = QuizChoiceFactory(question=q2, is_correct=True)
    QuizChoiceFactory(question=q2, is_correct=False)

    return quiz, q1, q1_correct, q2, q2_correct


def test_all_correct_scores_100_and_passes():
    quiz, q1, q1_correct, q2, q2_correct = _build_quiz()
    score, passed = score_quiz(quiz, {q1.id: q1_correct.id, q2.id: q2_correct.id})
    assert score == 100
    assert passed is True


def test_all_wrong_scores_0_and_fails():
    quiz, q1, q1_correct, q2, q2_correct = _build_quiz()
    wrong1 = q1.choices.exclude(id=q1_correct.id).get()
    wrong2 = q2.choices.exclude(id=q2_correct.id).get()
    score, passed = score_quiz(quiz, {q1.id: wrong1.id, q2.id: wrong2.id})
    assert score == 0
    assert passed is False


def test_partial_score_below_passing_threshold_fails():
    quiz, q1, q1_correct, q2, q2_correct = _build_quiz(passing_score_percent=80)
    score, passed = score_quiz(quiz, {q1.id: q1_correct.id})  # 1/2 = 50%
    assert score == 50
    assert passed is False


def test_missing_answer_counts_as_incorrect():
    quiz, q1, q1_correct, q2, q2_correct = _build_quiz()
    score, passed = score_quiz(quiz, {q1.id: q1_correct.id})  # q2 unanswered
    assert score == 50
    assert passed is False


def test_quiz_with_no_questions_scores_zero_and_fails():
    quiz = QuizFactory()
    score, passed = score_quiz(quiz, {})
    assert score == 0
    assert passed is False
