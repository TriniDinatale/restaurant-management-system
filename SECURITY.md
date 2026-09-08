# Security

La seguridad y la protección de los datos forman parte de los requisitos del sistema desde su diseño.

## Principios

- Autenticación gestionada por el backend.
- Autorización y permisos validados en el servidor.
- Principio de mínimo privilegio.
- Contraseñas nunca almacenadas en texto plano.
- Secretos y credenciales fuera del repositorio.
- Validación de datos en backend.
- Protección contra CSRF.
- Protección de sesiones.
- Auditoría de operaciones sensibles.
- Base de datos no expuesta públicamente.
- Backups periódicos.
- Actualización y revisión de dependencias.

## Información que no debe almacenarse en Git

- Archivos `.env`.
- Contraseñas.
- Claves privadas.
- Claves Web Push.
- Backups de la base de datos.
- Datos personales reales.
- Credenciales de producción.

## Operaciones sensibles

El sistema deberá mantener trazabilidad sobre operaciones como:

- Cobros.
- Cancelaciones y devoluciones.
- Pagos a proveedores.
- Movimientos de caja.
- Modificaciones administrativas relevantes.
- Apertura y cierre de turnos.