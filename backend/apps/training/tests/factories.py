import factory

from apps.employees.tests.factories import EmployeeFactory
from apps.training.models import Quiz, QuizChoice, QuizQuestion, TrainingAssignment, TrainingModule


class TrainingModuleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TrainingModule

    title = factory.Sequence(lambda n: f"Module {n}")
    content_url = "https://training.example.com/module"


class QuizFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Quiz

    module = factory.SubFactory(TrainingModuleFactory)
    passing_score_percent = 80


class QuizQuestionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = QuizQuestion

    quiz = factory.SubFactory(QuizFactory)
    text = factory.Sequence(lambda n: f"Question {n}")


class QuizChoiceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = QuizChoice

    question = factory.SubFactory(QuizQuestionFactory)
    text = factory.Sequence(lambda n: f"Choice {n}")
    is_correct = False


class TrainingAssignmentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TrainingAssignment

    employee = factory.SubFactory(EmployeeFactory)
    module = factory.SubFactory(TrainingModuleFactory)
