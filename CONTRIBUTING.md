# Contribuir

Gracias por querer ayudar. eMuwarr lo mantiene una sola persona, así que cuanto más concreto sea lo que propones,
antes se podrá revisar.

## Reportar un fallo o proponer algo

Abre un [issue](https://github.com/Kisnov/eMuwarr/issues/new/choose) con la plantilla de fallo o de sugerencia. Un
fallo de seguridad no va en un issue: sigue [`SECURITY.md`](SECURITY.md). No pegues nunca API keys, passkeys ni tokens.

## Ramas

- `dev` es la rama por defecto: todo el trabajo y todos los PR van ahí.
- `main` es siempre la última versión publicada. Solo avanza cuando se publica una versión; no abras PR contra `main`.
- Nombra la rama por lo que hace: `feat/…`, `fix/…`, `docs/…` o `chore/…`. El prefijo pone la etiqueta del PR y decide
  en qué apartado de las notas de la versión sale.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/es/) en una línea, en castellano, contando lo que cambia
para quien usa la aplicación: `fix: la subida usa el enlace de la carpeta de subidas`. Un cambio por PR.

## Antes de abrir el PR

Pasa los tests del backend y las comprobaciones del frontend (los comandos están en
[Desarrollo](README.md#desarrollo)). Los tests del backend necesitan Linux: hardlinks, `mediainfo` y `mkvtoolnix`.

Si el cambio toca el contrato con [Uploadrr-EMU](https://github.com/Kisnov/Uploadrr-EMU) (argumentos, claves de
`config.yaml` o líneas de salida que se leen), cambia los dos repos a la vez: [`CLAUDE.md`](CLAUDE.md) explica cómo.

## Revisión

El CI corre en cada PR. Cada PR de una rama de este repo construye además la imagen `ghcr.io/kisnov/emuwarr:pr-<número>`
para probarlo antes de fusionarlo. CodeRabbit revisa cuando se le pide con un comentario `@coderabbitai review`.

## Licencia

Al contribuir aceptas que tu código se publique bajo la [GPL-3.0-or-later](LICENSE), como el resto del proyecto.
