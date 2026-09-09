from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


class Sector(models.Model):
    class Nombre(models.TextChoices):
        SALON = "SALON", "Salón"
        BARRA = "BARRA", "Barra"
        PIZZA = "PIZZA", "Pizza"
        PLATOS = "PLATOS", "Platos"

    nombre = models.CharField(
        max_length=20,
        choices=Nombre.choices,
        unique=True,
    )

    activo = models.BooleanField(default=True)

    def __str__(self):
        return self.get_nombre_display()


class User(AbstractUser):
    class Rol(models.TextChoices):
        ADMINISTRADOR = "ADMINISTRADOR", "Administrador"
        MOZO = "MOZO", "Mozo"
        BARRA = "BARRA", "Barra"
        COCINA = "COCINA", "Cocina"

    rol = models.CharField(
        max_length=20,
        choices=Rol.choices,
       
    )
    

    sector = models.ForeignKey(
        Sector,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="usuarios",
    )

    def clean(self):
        super().clean()

        if self.rol == self.Rol.ADMINISTRADOR:
            return

        if self.sector is None:
            raise ValidationError(
                {"sector": "El usuario debe tener un sector asignado."}
            )

        if (
            self.rol == self.Rol.MOZO
            and self.sector.nombre != Sector.Nombre.SALON
        ):
            raise ValidationError(
                {"sector": "Un mozo debe pertenecer al sector Salón."}
            )

        if (
            self.rol == self.Rol.BARRA
            and self.sector.nombre != Sector.Nombre.BARRA
        ):
            raise ValidationError(
                {"sector": "Un usuario de Barra debe pertenecer al sector Barra."}
            )

        if (
            self.rol == self.Rol.COCINA
            and self.sector.nombre
            not in {
                Sector.Nombre.PIZZA,
                Sector.Nombre.PLATOS,
            }
        ):
            raise ValidationError(
                {
                    "sector": (
                        "Un usuario de Cocina debe pertenecer "
                        "a Pizza o Platos."
                    )
                }
            )