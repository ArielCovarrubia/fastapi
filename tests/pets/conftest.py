"""Dobles de prueba y datos reutilizables para los tests de Pet.

Los repositorios falsos permiten probar PetsService sin PostgreSQL: guardan
los datos en listas en memoria y exponen la misma interfaz que los
repositorios reales.
"""

from collections.abc import Callable
from uuid import UUID, uuid4

import pytest

from app.pets.models import PetModel
from app.shared.datetimes import utc_now
from app.students.models import StudentModel


class FakeStudentsRepository:
    """Repositorio en memoria con la parte de StudentsRepository que usa Pet.

    PetsService solo consulta estudiantes por id (para validar que existan).
    """

    def __init__(self) -> None:
        self.students: list[StudentModel] = []

    def find_by_id(self, student_id: UUID) -> StudentModel | None:
        return next(
            (student for student in self.students if student.id == student_id), None
        )


class FakePetsRepository:
    """Repositorio en memoria con la misma interfaz que PetsRepository."""

    def __init__(self) -> None:
        self.pets: list[PetModel] = []

    def find_all_by_student(self, student_id: UUID) -> list[PetModel]:
        return [pet for pet in self.pets if pet.studentId == student_id]

    def find_by_id(self, pet_id: UUID) -> PetModel | None:
        return next((pet for pet in self.pets if pet.id == pet_id), None)

    def find_by_student_and_name(self, student_id: UUID, name: str) -> PetModel | None:
        return next(
            (
                pet
                for pet in self.pets
                if pet.studentId == student_id and pet.name == name
            ),
            None,
        )

    def add(self, pet: PetModel) -> PetModel:
        # SQLAlchemy asigna id y timestamps en el flush. El fake los asigna
        # para que Pet.model_validate pueda construir la respuesta.
        pet.id = uuid4()
        now = utc_now()
        pet.createdAt = now
        pet.updatedAt = now
        self.pets.append(pet)
        return pet

    def delete(self, pet: PetModel) -> None:
        self.pets.remove(pet)


@pytest.fixture
def fake_students_repository() -> FakeStudentsRepository:
    """Entrega un repositorio de estudiantes en memoria."""
    return FakeStudentsRepository()


@pytest.fixture
def fake_pets_repository() -> FakePetsRepository:
    """Entrega un repositorio de mascotas en memoria."""
    return FakePetsRepository()


@pytest.fixture
def pet_payload() -> Callable[..., dict[str, object]]:
    """Devuelve una fábrica de payloads válidos para crear mascotas."""

    def build(**overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "name": "Firulais",
            "species": "perro",
            "age": 3,
        }
        data.update(overrides)
        return data

    return build


@pytest.fixture
def make_student_model() -> Callable[..., StudentModel]:
    """Devuelve una fábrica de StudentModel con id y fechas asignados."""

    def build(**overrides: object) -> StudentModel:
        now = utc_now()
        values: dict[str, object] = {
            "id": uuid4(),
            "name": "Ana Pérez",
            "username": "ana_perez",
            "email": "ana@example.com",
            "age": 21,
            "createdAt": now,
            "updatedAt": now,
        }
        values.update(overrides)
        return StudentModel(**values)

    return build


@pytest.fixture
def make_pet_model() -> Callable[..., PetModel]:
    """Devuelve una fábrica de PetModel con id y fechas asignados.

    Hay que indicar `studentId` (el dueño) al construir la mascota.
    """

    def build(**overrides: object) -> PetModel:
        now = utc_now()
        values: dict[str, object] = {
            "id": uuid4(),
            "name": "Firulais",
            "species": "perro",
            "age": 3,
            "createdAt": now,
            "updatedAt": now,
        }
        values.update(overrides)
        return PetModel(**values)

    return build


@pytest.fixture
def student(fake_students_repository, make_student_model) -> StudentModel:
    """Deja un estudiante ya guardado en el repositorio falso y lo devuelve."""
    saved = make_student_model()
    fake_students_repository.students.append(saved)
    return saved


@pytest.fixture
def other_student(fake_students_repository, make_student_model) -> StudentModel:
    """Deja un segundo estudiante guardado, distinto de `student`."""
    saved = make_student_model(
        name="Beto Soto", username="beto_soto", email="beto@example.com"
    )
    fake_students_repository.students.append(saved)
    return saved


@pytest.fixture
def store_pet(fake_pets_repository, make_pet_model) -> Callable[..., PetModel]:
    """Devuelve una función que crea una mascota y la guarda en el repositorio.

    Uso: `store_pet(student, name="Michi", species="gato")`.
    """

    def store(owner: StudentModel, **overrides: object) -> PetModel:
        pet = make_pet_model(studentId=owner.id, **overrides)
        fake_pets_repository.pets.append(pet)
        return pet

    return store