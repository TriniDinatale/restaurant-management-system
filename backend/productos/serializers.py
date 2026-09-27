from rest_framework import serializers

from .models import Categoria, Producto
from usuarios.models import Sector


class CategoriaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Categoria
        fields = [
            "id",
            "nombre",
            "activo",
        ]


class ProductoSerializer(serializers.ModelSerializer):
    categoria_nombre = serializers.CharField(
        source="categoria.nombre",
        read_only=True,
    )
    sector_destino_nombre = serializers.CharField(
        source="sector_destino.nombre",
        read_only=True,
    )

    class Meta:
        model = Producto
        fields = [
            "id",
            "nombre",
            "precio",
            "categoria",
            "categoria_nombre",
            "sector_destino",
            "sector_destino_nombre",
            "activo",
        ]
        
    def validate_sector_destino(self, value):
        if value.nombre == Sector.Nombre.SALON:
            raise serializers.ValidationError(
                "SALON no puede ser el sector de destino "
                "de un producto."
            )
    
        return value
    
    def validate_categoria(self, value):
        if not value.activo:
            raise serializers.ValidationError(
                "No se puede asignar un producto "
                "a una categoría inactiva."
            )
    
        return value