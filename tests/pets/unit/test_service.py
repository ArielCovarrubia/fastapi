"""Pruebas unitarias de PetsService.

Usan repositorios falsos para aislar la lógica de negocio. No tocan
PostgreSQL ni pasan por HTTP.

Reglas de negocio (RN) derivadas del requisito, y qué criterio de aceptación
verifica cada grupo de tests:

RN-1  Toda operación sobre mascotas exige un estudiante existente.
    CA: si el estudiante no existe (o el id no es un UUID) -> 404
    "Estudiante no encontrado" y no se modifica nada.
RN-2  Un estudiante solo ve sus propias mascotas.
    CA: el listado devuelve únicamente las mascotas de ese estudiante.
RN-3  Una mascota nueva queda asociada al estudiante; la edad es opcional.
    CA: se guarda con el studentId correcto, con o sin edad, y se confirma
    la transacción una sola vez.
RN-4  El nombre de la mascota es único dentro de un mismo estudiante.
    CA: repetir un nombre del mismo estudiante -> 409 sin commit; el mismo
    nombre en otro estudiante es válido; al actualizar, la mascota puede
    conservar su propio nombre pero no tomar el de otra.
RN-5  La actualización es parcial.
    CA: solo cambian los campos enviados; una actualización vacía no cambia
    nada.
RN-6  Una mascota solo se puede modificar o eliminar a través de su dueño.
    CA: si la mascota no existe, el id es inválido o pertenece a otro
    estudiante -> 404 "Mascota no encontrada" y la mascota no se altera.
RN-7  Eliminar devuelve la mascota eliminada y la quita del repositorio.
    CA: la mascota deja de existir, las demás siguen y hay un commit.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.pets.schemas import CreatePetDto, UpdatePetDto
from app.pets.service import PetsService

pytestmark = pytest.mark.unit

STUDENT_NOT_FOUND = "Estudiante no encontrado"
PET_NOT_FOUND = "Mascota no encontrada"
DUPLICATE_NAME_ERRORS = {"name": {"message": "Ya tienes una mascota con ese nombre"}}

INVALID_IDS = pytest.mark.parametrize(
    "bad_id",
    [str(uuid4()), "no-es-un-uuid"],
    ids=["inexistente", "uuid-invalido"],
)


@pytest.fixture
def service(fake_students_repository, fake_pets_repository):
    """Crea PetsService con repositorios falsos y un commit simulado."""
    db = MagicMock()
    pets_service = PetsService(db)
    pets_service.repository = fake_pets_repository
    pets_service.students_service.repository = fake_students_repository
    return pets_service


# --- Listar (RN-1, RN-2) ---------------------------------------------------


def test_find_all_returns_only_pets_of_student(
    service, student, other_student, store_pet
):
    """
    Verifica que el listado incluya solo las mascotas del estudiante pedido.

    Espera el total correcto y que no aparezcan mascotas de otro estudiante.
    """
    store_pet(student, name="Firulais")
    store_pet(student, name="Michi", species="gato")
    store_pet(other_student, name="Rocky")

    result = service.find_all_for_student(str(student.id))

    assert result.total == 2
    assert sorted(pet.name for pet in result.items) == ["Firulais", "Michi"]
    assert all(pet.studentId == student.id for pet in result.items)


def test_find_all_student_without_pets(service, student):
    """Verifica que un estudiante sin mascotas devuelva una lista vacía."""
    result = service.find_all_for_student(str(student.id))

    assert result.total == 0
    assert result.items == []


@INVALID_IDS
def test_find_all_student_not_found(service, bad_id):
    """Verifica que listar mascotas de un estudiante inexistente dé 404."""
    with pytest.raises(HTTPException) as error:
        service.find_all_for_student(bad_id)

    assert error.value.status_code == 404
    assert error.value.detail == STUDENT_NOT_FOUND


# --- Crear (RN-1, RN-3, RN-4) ----------------------------------------------


def test_create_pet(service, student, fake_pets_repository, pet_payload):
    """
    Verifica que crear una mascota la asocie al estudiante y haga commit.

    Espera los datos del payload, el studentId del dueño y un único commit.
    """
    result = service.create(str(student.id), CreatePetDto(**pet_payload()))

    assert result.name == "Firulais"
    assert result.species == "perro"
    assert result.age == 3
    assert result.studentId == student.id
    assert len(fake_pets_repository.pets) == 1
    assert fake_pets_repository.pets[0].studentId == student.id
    service.db.commit.assert_called_once()


def test_create_pet_without_age(service, student, pet_payload):
    """Verifica que la edad sea opcional al crear una mascota."""
    payload = pet_payload()
    del payload["age"]

    result = service.create(str(student.id), CreatePetDto(**payload))

    assert result.age is None
    service.db.commit.assert_called_once()


def test_create_duplicate_name_for_same_student(
    service, student, store_pet, fake_pets_repository, pet_payload
):
    """
    Verifica que repetir un nombre del mismo estudiante produzca 409.

    Espera el error en el campo `name`, que no se agregue otra mascota y que
    no se llame a commit.
    """
    store_pet(student, name="Firulais")

    with pytest.raises(HTTPException) as error:
        service.create(str(student.id), CreatePetDto(**pet_payload()))

    assert error.value.status_code == 409
    assert error.value.detail["errors"] == DUPLICATE_NAME_ERRORS
    assert len(fake_pets_repository.pets) == 1
    service.db.commit.assert_not_called()


def test_create_same_name_for_different_student(
    service, student, other_student, store_pet, fake_pets_repository, pet_payload
):
    """
    Verifica que el nombre solo deba ser único dentro de un estudiante.

    Espera que otro estudiante pueda tener una mascota con el mismo nombre.
    """
    store_pet(other_student, name="Firulais")

    result = service.create(str(student.id), CreatePetDto(**pet_payload()))

    assert result.studentId == student.id
    assert len(fake_pets_repository.pets) == 2
    service.db.commit.assert_called_once()


@INVALID_IDS
def test_create_student_not_found(service, fake_pets_repository, pet_payload, bad_id):
    """Verifica que crear una mascota para un estudiante inexistente dé 404."""
    with pytest.raises(HTTPException) as error:
        service.create(bad_id, CreatePetDto(**pet_payload()))

    assert error.value.status_code == 404
    assert error.value.detail == STUDENT_NOT_FOUND
    assert fake_pets_repository.pets == []
    service.db.commit.assert_not_called()


# --- Actualizar (RN-1, RN-4, RN-5, RN-6) -----------------------------------


def test_update_partial(service, student, store_pet):
    """Verifica que una actualización parcial cambie solo los campos enviados."""
    pet = store_pet(student)

    result = service.update(
        str(student.id), str(pet.id), UpdatePetDto(name="Rex", age=5)
    )

    assert result.name == "Rex"
    assert result.age == 5
    assert result.species == "perro"
    assert result.studentId == student.id
    service.db.commit.assert_called_once()


def test_update_empty_does_not_change_fields(service, student, store_pet):
    """Verifica que una actualización vacía conserve los datos y haga commit."""
    pet = store_pet(student)

    result = service.update(str(student.id), str(pet.id), UpdatePetDto())

    assert result.name == "Firulais"
    assert result.species == "perro"
    assert result.age == 3
    service.db.commit.assert_called_once()


def test_update_allows_same_name(service, student, store_pet):
    """Verifica que la mascota pueda conservar su propio nombre."""
    pet = store_pet(student)

    result = service.update(
        str(student.id), str(pet.id), UpdatePetDto(name="Firulais", species="lobo")
    )

    assert result.name == "Firulais"
    assert result.species == "lobo"
    service.db.commit.assert_called_once()


def test_update_conflict_with_other_pet_name(service, student, store_pet):
    """
    Verifica que tomar el nombre de otra mascota del estudiante produzca 409.

    Espera que la mascota conserve su nombre y que no se llame a commit.
    """
    pet = store_pet(student, name="Firulais")
    store_pet(student, name="Michi", species="gato")

    with pytest.raises(HTTPException) as error:
        service.update(str(student.id), str(pet.id), UpdatePetDto(name="Michi"))

    assert error.value.status_code == 409
    assert error.value.detail["errors"] == DUPLICATE_NAME_ERRORS
    assert pet.name == "Firulais"
    service.db.commit.assert_not_called()


def test_update_allows_name_used_by_other_student(
    service, student, other_student, store_pet
):
    """Verifica que se pueda usar un nombre que solo tiene otro estudiante."""
    pet = store_pet(student, name="Firulais")
    store_pet(other_student, name="Rocky")

    result = service.update(str(student.id), str(pet.id), UpdatePetDto(name="Rocky"))

    assert result.name == "Rocky"
    service.db.commit.assert_called_once()


@INVALID_IDS
def test_update_pet_not_found(service, student, bad_id):
    """Verifica que actualizar una mascota inexistente o inválida dé 404."""
    with pytest.raises(HTTPException) as error:
        service.update(str(student.id), bad_id, UpdatePetDto(name="Rex"))

    assert error.value.status_code == 404
    assert error.value.detail == PET_NOT_FOUND
    service.db.commit.assert_not_called()


def test_update_pet_of_other_student_returns_404(
    service, student, other_student, store_pet
):
    """
    Verifica que no se pueda actualizar una mascota que pertenece a otro.

    Espera 404 (no se revela que existe), sin cambios y sin commit.
    """
    pet = store_pet(other_student, name="Rocky")

    with pytest.raises(HTTPException) as error:
        service.update(str(student.id), str(pet.id), UpdatePetDto(name="Rex"))

    assert error.value.status_code == 404
    assert error.value.detail == PET_NOT_FOUND
    assert pet.name == "Rocky"
    service.db.commit.assert_not_called()


def test_update_student_not_found(service):
    """
    Verifica que un estudiante inexistente dé 404 de estudiante.

    Se comprueba primero al estudiante y luego a la mascota.
    """
    with pytest.raises(HTTPException) as error:
        service.update(str(uuid4()), str(uuid4()), UpdatePetDto(name="Rex"))

    assert error.value.status_code == 404
    assert error.value.detail == STUDENT_NOT_FOUND
    service.db.commit.assert_not_called()


# --- Eliminar (RN-1, RN-6, RN-7) -------------------------------------------


def test_delete(service, student, store_pet, fake_pets_repository):
    """
    Verifica que eliminar una mascota la quite del repositorio y haga commit.

    Espera recibir la mascota eliminada y que las demás sigan existiendo.
    """
    pet = store_pet(student, name="Firulais")
    remaining = store_pet(student, name="Michi", species="gato")

    result = service.delete(str(student.id), str(pet.id))

    assert result.id == pet.id
    assert result.name == "Firulais"
    assert fake_pets_repository.pets == [remaining]
    service.db.commit.assert_called_once()


@INVALID_IDS
def test_delete_pet_not_found(service, student, bad_id):
    """Verifica que eliminar una mascota inexistente o inválida dé 404."""
    with pytest.raises(HTTPException) as error:
        service.delete(str(student.id), bad_id)

    assert error.value.status_code == 404
    assert error.value.detail == PET_NOT_FOUND
    service.db.commit.assert_not_called()


def test_delete_pet_of_other_student_returns_404(
    service, student, other_student, store_pet, fake_pets_repository
):
    """
    Verifica que no se pueda eliminar una mascota que pertenece a otro.

    Espera 404, que la mascota siga guardada y que no se llame a commit.
    """
    pet = store_pet(other_student, name="Rocky")

    with pytest.raises(HTTPException) as error:
        service.delete(str(student.id), str(pet.id))

    assert error.value.status_code == 404
    assert error.value.detail == PET_NOT_FOUND
    assert fake_pets_repository.pets == [pet]
    service.db.commit.assert_not_called()


def test_delete_student_not_found(service):
    """Verifica que eliminar con un estudiante inexistente dé 404 de estudiante."""
    with pytest.raises(HTTPException) as error:
        service.delete(str(uuid4()), str(uuid4()))

    assert error.value.status_code == 404
    assert error.value.detail == STUDENT_NOT_FOUND
    service.db.commit.assert_not_called()