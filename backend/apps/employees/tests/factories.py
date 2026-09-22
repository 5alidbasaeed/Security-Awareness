import factory

from apps.employees.models import Department, Employee


class DepartmentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Department

    name = factory.Sequence(lambda n: f"Department {n}")


class EmployeeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Employee

    full_name = factory.Sequence(lambda n: f"Employee {n}")
    email = factory.Sequence(lambda n: f"employee{n}@example.com")
