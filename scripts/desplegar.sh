#!/usr/bin/env bash
# Construye, publica y anuncia una versión, en ese orden y sin pasos sueltos.
#
# Existe porque el build-arg APP_COMMIT es fácil de olvidar y falla en silencio:
# es el sello que la tarjeta de Diagnóstico compara con el manifiesto publicado.
# Sin él la imagen se construye igual, pero "Comprobar versión" queda Omitido
# para siempre, diciendo que estás ejecutando desde el código fuente aunque
# estés en la imagen. Ya pasó.
#
# Uso:
#   scripts/desplegar.sh --notas "Lo que cambia, en una línea"
#   scripts/desplegar.sh --notas "..." --version patch   # sube la versión antes
#   scripts/desplegar.sh --solo-prueba                   # sube solo :dev, sin versión ni manifiesto
#   scripts/desplegar.sh ... --fuera-de-dev              # también con un commit que no está en dev de GitHub
#
# Al publicar sigue el orden del workflow: la imagen como :<sha>, la etiqueta
# vX.Y.Z (y main avanza hasta ella), la imagen como :X.Y.Z, :dev y :latest, y el manifiesto. Si algo falla,
# relanzar completa la versión. :X.Y.Z es la que se fija en el compose
# (EMUWAREZ_UPLOADRR_TAG=X.Y.Z) para quedarse en una versión o volver a ella.
#
# Lo normal es el workflow «Publicar versión», que hace lo mismo en GitHub tras
# pasar los tests. Esto es la alternativa local, sin gastar minutos de Actions
# y sin tests: pásalos antes.
#
# Necesita `gh auth login` hecho, para publicar la versión.
set -euo pipefail

IMAGEN="ghcr.io/kisnov/emuwarr"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# En Git Bash de Windows no hay python3.
PYTHON="$(command -v python3 || command -v python)" || { echo "No encuentro python3 ni python en el PATH." >&2; exit 1; }

NOTAS=""
ESCALA=""
PRUEBA=0
FUERA_DE_DEV=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --notas)         NOTAS="${2:?falta el texto de --notas}"; shift 2 ;;
    --version)       ESCALA="${2:?falta la escala de --version: patch, minor o major}"; shift 2 ;;
    --solo-prueba)   PRUEBA=1; shift ;;
    --fuera-de-dev)  FUERA_DE_DEV=1; shift ;;
    # La cabecera entera, hasta la primera línea que no es comentario.
    -h|--help)       sed -n '2,${/^#/!q;p;}' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Argumento no reconocido: $1" >&2; exit 2 ;;
  esac
done

cd "$RAIZ"

if [[ -z "$NOTAS" && "$PRUEBA" -eq 0 ]]; then
  echo "Falta --notas: es la línea que verá el usuario en el aviso de versión." >&2
  echo "(o --solo-prueba para subir solo :dev, sin anunciar nada)" >&2
  exit 2
fi

# El árbol tiene que estar limpio: la imagen se construye desde el commit, y un
# cambio sin commitear no llegaría a ella.
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Hay cambios sin commitear, y la imagen sale del commit: no los llevaría." >&2
  echo "Commitea primero." >&2
  git status --short >&2
  exit 1
fi

if [[ -n "$ESCALA" ]]; then
  "$PYTHON" scripts/subir_version.py "$ESCALA"
  echo "La versión ha cambiado: commitea ese cambio, súbelo a dev y vuelve a lanzar esto." >&2
  exit 1
fi

# Solo lo que ya está en dev de GitHub: desde otra rama, :dev o :latest llevarían código que dev no tiene.
if [[ "$FUERA_DE_DEV" -eq 0 ]]; then
  git fetch -q origin dev
  if ! git merge-base --is-ancestor HEAD origin/dev; then
    echo "Este commit no está en dev de GitHub: no se construye ni se sube nada." >&2
    echo "Súbelo a dev primero, o relanza con --fuera-de-dev si es a propósito." >&2
    exit 1
  fi
fi

COMMIT="$(git rev-parse HEAD)"
FECHA="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
VERSION="$("$PYTHON" scripts/subir_version.py --ver)"

echo "Versión ..... $VERSION"
echo "Commit ...... $COMMIT"
echo "Construida .. $FECHA"

# La etiqueta vX.Y.Z de esta versión. Nunca se pisa: si ya existe en otro
# commit, aquí o en GitHub, se para antes de construir nada y hay que
# resolverlo a mano. Lo que manda es GitHub, porque es su etiqueta la que
# marca la versión publicada. De una etiqueta anotada, ls-remote da también la
# línea "^{}" con el commit al que apunta; de una ligera (las de una release
# hecha en la web), solo esa.
ETIQUETA="v$VERSION"

# Si no se puede preguntar a GitHub (sin red, sin permiso), es un error, no
# "no hay etiqueta": se para en vez de seguir a ciegas.
etiqueta_remota() {
  local salida
  salida="$(git ls-remote --tags origin "refs/tags/$ETIQUETA" "refs/tags/$ETIQUETA^{}")" || {
    echo "No se pudo consultar en GitHub la etiqueta $ETIQUETA." >&2
    return 1
  }
  printf '%s\n' "$salida" | sort -k2 | tail -1 | cut -f1
}

etiqueta_local() {
  git rev-parse -q --verify "refs/tags/$ETIQUETA^{commit}" || true
}

# Deja en REMOTA el commit de la etiqueta en GitHub, si existe.
comprobar_etiqueta() {
  local local_
  # Con `|| return 1` explícito: se llama dentro de un `||`, donde set -e no
  # actúa, y un fallo de la consulta pasaría por "no hay etiqueta".
  REMOTA="$(etiqueta_remota)" || return 1
  local_="$(etiqueta_local)"
  if [[ -n "$REMOTA" && "$REMOTA" != "$COMMIT" ]]; then
    echo "La etiqueta $ETIQUETA ya está en GitHub en $REMOTA, no en este commit." >&2
    echo "No se pisa: sube la versión (--version patch) o quita la etiqueta que sobra." >&2
    return 1
  fi
  if [[ -n "$local_" && "$local_" != "$COMMIT" ]]; then
    echo "La etiqueta $ETIQUETA existe aquí en $local_, no en este commit." >&2
    echo "Si sobra:  git tag -d $ETIQUETA  y vuelve a lanzar esto." >&2
    return 1
  fi
}

# Con :<sha> ya subida y antes que :X.Y.Z y :latest: crea la etiqueta, o sube
# la que una ejecución anterior dejó solo aquí. Solo si el commit es la punta de
# dev en GitHub: la etiqueta marca en el repo el commit de esta versión, y main avanza hasta él.
etiquetar() {
  # Otra vez, por si algo ha cambiado mientras se construía.
  comprobar_etiqueta || return 1
  if [[ -n "$REMOTA" ]]; then
    echo "La etiqueta $ETIQUETA ya está en GitHub."
    return 0
  fi
  git fetch -q origin dev
  if [[ "$(git rev-parse origin/dev)" != "$COMMIT" ]]; then
    echo "Este commit no es la punta de dev en GitHub, así que no creo $ETIQUETA." >&2
    echo "Cuando lo sea:  git tag -a $ETIQUETA -m \"…\" $COMMIT && git push --atomic origin $ETIQUETA $COMMIT:refs/heads/main" >&2
    return 0
  fi
  if [[ -n "$(etiqueta_local)" ]]; then
    echo "La etiqueta $ETIQUETA estaba creada aquí pero sin subir: la subo."
  else
    git tag -a "$ETIQUETA" -m "${NOTAS:-Versión $VERSION}" "$COMMIT"
  fi
  # Si alguien la ha subido en otro commit desde la comprobación, git push no la pisa; main solo avanza (fast-forward).
  git push -q --atomic origin "$ETIQUETA" "$COMMIT:refs/heads/main"
  echo "Etiqueta .... $ETIQUETA"
}

if [[ "$PRUEBA" -eq 0 ]]; then
  comprobar_etiqueta || exit 1
  # Antes de construir: repetir una versión ya publicada movería :latest y :X.Y.Z a otro digest.
  "$PYTHON" scripts/publicar_version.py --solo-comprobar --notas "$NOTAS" --repo
  # Y que gh puede escribir el manifiesto: el endpoint de push de git solo da 200 a quien puede.
  # El token va por stdin (printf es interno), no en los argumentos de curl.
  if ! printf 'user = "x:%s"\n' "$(gh auth token)" | curl -sf -o /dev/null --connect-timeout 10 --max-time 30 \
      --config - "https://github.com/Kisnov/eMuwarr.git/info/refs?service=git-receive-pack"; then
    echo "gh no puede escribir en Kisnov/eMuwarr: no se sube ninguna imagen." >&2
    exit 1
  fi
fi

REGISTRO="$(mktemp -t desplegar-XXXXXX.log)"
echo "Construyendo (registro en $REGISTRO)…"
# Desde el commit y no desde el árbol: en Windows (autocrlf) el entrypoint iría con CRLF y el contenedor no arrancaría.
if ! git -c core.autocrlf=false archive HEAD | docker build --pull \
    --build-arg "APP_COMMIT=$COMMIT" \
    --build-arg "APP_BUILD_DATE=$FECHA" \
    --build-arg "APT_UPGRADE_DATE=${FECHA%%T*}" \
    -t "$IMAGEN:$COMMIT" - > "$REGISTRO" 2>&1; then
  echo "La construcción ha fallado. Últimas líneas:" >&2
  tail -30 "$REGISTRO" >&2
  exit 1
fi

# Comprobar el sello en la imagen ya construida, no fiarse de haberlo pasado:
# es justo el fallo que este script existe para evitar.
SELLO="$(docker image inspect "$IMAGEN:$COMMIT" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
if [[ "$SELLO" != "$COMMIT" ]]; then
  echo "La imagen ha salido sellada como '$SELLO' en vez de '$COMMIT'." >&2
  echo "No se sube: quedaría otra build muda." >&2
  exit 1
fi
echo "Sello verificado en la imagen."

subir() {
  docker tag "$IMAGEN:$COMMIT" "$IMAGEN:$1"
  docker push "$IMAGEN:$1"
}

echo "Subiendo…"
if [[ "$PRUEBA" -eq 1 ]]; then
  subir dev
  echo "Subida como :dev, sin versión ni manifiesto. Para publicarla, relanza sin --solo-prueba."
  exit 0
fi
subir "$COMMIT"
# Otra vez: si durante la construcción salió una versión más nueva, :latest y :dev no vuelven atrás.
"$PYTHON" scripts/publicar_version.py --solo-comprobar --notas "$NOTAS" --repo
# Como el workflow: la etiqueta de git antes que :X.Y.Z y :latest, y el manifiesto lo último.
etiquetar
subir "$VERSION"
# :dev antes que :latest: si algo falla entre medias, :dev nunca queda por detrás.
subir dev
subir latest
DIGEST="$(docker image inspect "$IMAGEN:latest" --format '{{index .RepoDigests 0}}' | cut -d@ -f2)"
echo "Digest ...... $DIGEST"

echo "Publicando el manifiesto…"
"$PYTHON" scripts/publicar_version.py --digest "$DIGEST" --notas "$NOTAS" --repo
