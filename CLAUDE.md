# eMuwarr

Self-hosted web app: FastAPI + SQLite backend (`emuwarez_uploadrr/`) and React + Vite + TypeScript frontend
(`frontend/`, served as static files). It syncs Radarr/Sonarr, cross-checks eMuwarez (UNIT3D tracker,
https://emu-warez.com) requests, renames to eMuwarez nomenclature, links files into an uploads folder and runs
the upload with Uploadrr-EMU (`uploadrr-emu/`, copied into the image at `/opt/Uploadrr` and run as a subprocess). It
came in from `Kisnov/Uploadrr-EMU` with `git subtree add`, history kept; that repo is no longer where it changes.
`uploadrr-emu/CLAUDE.md` has its own rules (only the EMU path is maintained).

## Workflow

- Sole maintainer: Gabriel (Kisnov). Main repo `Kisnov/eMuwarr`. All work goes to `dev` (the default branch); `main` is
  always the last published version and only moves when `publicar.yml` (or `desplegar.sh`) fast-forwards it to the
  version commit: never commit or open a PR to `main`. It started as a private fork of
  Rovaal-code's project (abandoned; Alvaro gave permission to publish it under the GPL); the history was moved here
  without the fork link and with his commit e-mail replaced. The image moved with it to `ghcr.io/kisnov/emuwarr` (the old `emuwarez-uploadrr` package is retired).
- Docs-only changes and very simple fixes go straight to `dev` as a commit, no PR. Everything else: branch and
  PR. Group related changes: no PR per one-line fix. Open PRs as non-draft so CodeRabbit reviews them; its standing
  rules per path live in `.coderabbit.yaml`. Greptile (on trial until 2026-12-06) reviews each PR once, when it opens;
  `.greptile/` points it at this file and `.coderabbit.yaml`, so the rules live in one place.
- UI text is in Spanish (the users are Spanish speakers), and so are the README and docs. Code comments and commits
  may be in Spanish or English; most of the code is in Spanish, so Spanish keeps it consistent, but don't translate
  existing comments just to change their language. Conventional Commits with a one-line summary of the user-visible
  outcome (`fix: la subida usa el enlace de la carpeta de subidas`).
- Don't bump the version in a PR: `scripts/desplegar.sh --version` does it at release.
- Commit identity is whatever the environment sets up; AI attribution in commits or comments is fine.

## Saving review and CI budget

- Run the full backend suite locally only when code changed or a merge conflict was resolved. A clean merge of
  `dev` into a PR branch is left to CI.
- CodeRabbit's free OSS tier allows only a few reviews per hour (more as the repo gains stars), and every push to an
  open PR costs one. Fix real Major/Critical findings
  right away. A Minor, nitpick or trivial finding gets a reply on its thread (fix it or say why not), and its fix
  rides on the next push that is happening anyway, not on a push of its own.
- Don't fight CodeRabbit's auto-pause on busy branches: post `@coderabbitai review` (alone in the comment) only
  once the PR is otherwise ready (CI green, every thread answered).

## Verify

- Backend: run the tests in Linux (hardlinks, mediainfo, mkvtoolnix), e.g. `python:3.12-slim` with
  `pip install -c constraints.txt -e ".[web,dev]"`, then `python -m pytest tests -q -p no:cacheprovider` (~2180 tests, ~7 min).
  On Windows, copy the tree into the container (`cp -r /src/. /w/`) rather than running on the bind mount.
- Uploadrr-EMU: from `uploadrr-emu/`, `pip install -r requirements.txt pytest pytest-cov`, then
  `python -m pytest tests -q -p no:cacheprovider` (~460 tests, ~1 min; its `conftest.py` blocks network and subprocesses).
  The root `pytest` only collects `tests/` (`testpaths`), and the root ruff skips `uploadrr-emu/`: its CI job checks
  only `E9,F63,F7,F82`, because the inherited code does not pass `F` yet.
- Frontend: `cd frontend && npm ci && npx tsc --noEmit && npm run lint && npx vitest run && npx vite build`.
- Architecture guards: `test_una_sola_puerta_a_settings.py` (settings only through `services/settings.py`
  or `database.py`; `services/` never imports from `web/`), `test_web_no_habla_sql.py`,
  `test_copias_frontend.py` (frontend copies of backend constants must match), `test_dependencia_legacy.py`
  (`legacy.py` stays deleted).
- `test_media_info_congelado.py` compares the mediainfo report against
  `tests/fixtures/mediainfo_informe_esperado.json`. For an intended change, regenerate it with
  `python -m tests.test_media_info_congelado --regenerar` and review the JSON diff.
- CI (`.github/workflows/ci.yml`) runs pytest, ruff (`--select E9,F`: syntax errors and pyflakes, so no unused imports or variables),
  the Uploadrr-EMU suite (job `uploadrr-emu`) and the frontend checks on every PR, and from
  `publicar.yml` before a release; it uploads coverage, test results and the frontend bundle size to Codecov
  (`codecov.yml`: the changed `emuwarez_uploadrr/` lines of a PR need 70 % coverage or `codecov/patch` fails; for
  `uploadrr-emu/` it is informational, but its job fails below 55 % total, without the inherited code its `pyproject.toml` omits; the frontend is not measured; the total is informational;
  needs the `CODECOV_TOKEN` secret). On pushes to `dev` CI runs the backend only; `bundle.yml`
  only builds the frontend on each push that touches `frontend/` (~40 s) so PRs have a bundle size to compare against.
- `imagen-pr.yml` builds `ghcr.io/kisnov/emuwarr:pr-<n>` (amd64) for PRs from this repo's branches; `etiquetas.yml` labels
  each PR by its branch prefix (`.github/labeler.yml`) and `.github/release.yml` groups the release notes by those labels.
- `descargas.yml` (daily) writes the README's downloads badge, `descargas.json`, to the orphan branch `insignias` (never
  merge or delete it): `scripts/descargas_latest.py` reads the downloads of the version tagged `latest` from the
  package's versions page, since GHCR counts per version and has no API for it.
- `zizmor.yml` audits the workflows and `dependabot.yml` when `.github/` changes; run `zizmor --gh-token "$(gh auth token)" .` locally (without a token it skips online audits) before pushing a workflow edit.

## Not obvious

- Push events: call `eventos.publicar` from the one place that writes the data, not from its callers; it
  must never raise. The server stays a single process (no extra uvicorn workers): the event bus is in memory.
  A new `setInterval` in the frontend fails `test_no_han_aparecido_sondeos_nuevos` unless it is listed in
  `SONDEOS_ESPERADOS` or `RELOJES`: push from the server instead of polling.
- CI on a PR that only touches `frontend/`, `docs/` or `.md` runs just the test files that contain the word
  `frontend` (`grep -l frontend tests/test_*.py`). A test that reads frontend files must name `frontend` in its own file.

- Migrations: bump `SCHEMA_VERSION` in `database.py` and add a `_migrate_*` call at the end of the cascade in
  `init_db`; new columns go through `_anadir_columna`, which only stays quiet when the column is already there. The
  cascade is not one transaction and the version is stored at the end, so a pass that fails halfway runs again from the
  top: every migration must be safe to run twice, and one that rewrites data runs only when `desde` crosses its schema.
  `preparar_base()` runs it once at startup (lifespan and `web serve`): it copies the DB to
  `backups/pre-migracion-*.db` first (not started without room for it; the bell then says the update had no copy) and
  refuses a DB left by a newer version (`BaseMasNuevaError`); after that
  `init_db` returns at once for that file. A restore migrates the restored DB itself, and a lock keeps the cascade to
  one thread at a time. Where nobody called
  `preparar_base` (tests, scripts) `init_db` still checks the stored version. Open the DB with `db()` (commits,
  rolls back, closes) or the `get_db` dependency; a DB failure in a request is a 503, never a 401.
- SQLite settings live in `connect()`: WAL, `synchronous = NORMAL`, a 64 MB `journal_size_limit` and `secure_delete = FAST`
  (the image's Debian `libsqlite3` is built with `SECURE_DELETE`, which zero-fills every freed page into the WAL). Never
  hold a write transaction across a network call: commit first (`tmdb_titles._resolve_title` does).
- Upload pipeline: `upload_queue._run_queued_job` → `carpeta_subida` links the files into
  `UPLOADRR_UPLOADS_DIR` (default `/config/Uploadrr/uploads`) → `upload_execution.start_upload_job` →
  Uploadrr-EMU subprocess. Uploadrr hands that same folder to qBittorrent as the save path, so qBittorrent
  must see it at the same path. `_enlazar` hardlinks and falls back to a symlink across filesystems; keep the
  folder on the media's filesystem (e.g. under `/data`): a symlink only seeds if qBittorrent resolves its target.
- Contract with Uploadrr-EMU, change both sides in the same PR: argv in `services/uploadrr_contract.py` (below 720p it
  adds `-avbc`, and Uploadrr-EMU puts its `AVISO_BAJA_CALIDAD` notice at the top of the description;
  `test_contrato_uploadrr_real.py` parses the app's argv with the real `uploadrr-emu/src/args.py`);
  `config.yaml` keys set by `upload_execution/config.py` with `uploadrr_config.py` / `uploadrr_yaml.py` (among them the stock signature image, replaced by the current one, `AUTO.keep_quiet`, `SCREENS.use_oxipng`/`oxipng_level`, `DEFAULT.always_anon`
  and `TRACKERS.EMU.anon`, all `false`: only the `-a` flag makes an upload anonymous), on top of the user's
  `config.yaml`, go into the job's own file,
  `config_uploadrr/<job>.yaml` next to the DB (`<job>-reenvio.yaml` for «Reenviar a qBittorrent»; dir 0700, file 0600 via `O_EXCL`,
  deleted after the process, swept at startup, never in a backup), passed as `--config <absolute path>` (global, like `--eventos`),
  and the user's files are never written (`test_config_uploadrr_por_subida.py` has the golden files). The checkout must have
  `"config": true` in its `contrato_eventos.json`; an older `uploadrr.path` fails the job with a clear error (`sin_config_por_subida`).
  At startup `migrar_config_bak` turns the `config.yaml.bak` older versions left into `config.yaml` once (it was the real
  config) and moves it aside as `config.yaml.migrado`;
  progress and result parsing in `uploadrr_progress.py` / `upload_result_parser.py` match its output lines
  verbatim, but they are now the fallback (one version, then they go): when the checkout has `contrato_eventos.json` (version 1) the
  app launches with `--eventos <absolute path>` (a global flag, after the media path; the file is `eventos_uploadrr/<job>.jsonl`
  next to the DB, emptied before launch and deleted at the end) and `services/uploadrr_eventos.py` follows that JSON Lines file.
  Once an `inicio` event is read, events decide phases, steps, `torrent_subido`, the result and the qBittorrent outcome, and stdout
  is only logged; the exit code must agree (no `resultado`, `ok:false`, or exit ≠ 0 with `ok:true` are all failed jobs;
  `subido`, when present, is kept and authoritative). Without `inicio` (old Uploadrr-EMU, unwritable path) the text path runs
  unchanged. The schema copy is `tests/fixtures/contrato_eventos.json`, with the golden `.jsonl` beside the `.txt` ones;
  `test_transcripcion_dorada_uploadrr.py` compares both with the originals in `uploadrr-emu/` (those rule). Uploadrr-EMU exits 0 only if something was uploaded (any other code is a failed job), and after
  a successful upload prints `Torrent URL: …`. Resuming: Uploadrr-EMU prints `Step done: screenshots` / `Step done: torrent` (plain `print`) and
  the app stores them (`upload_jobs.pasos_uploadrr_json`, with `Torrent URL:` as `tracker`); a retry that reuses the
  previous job's prepared files, when that job itself launched Uploadrr-EMU, sets `reanuda_uploadrr` and launches with `--resume` (`subida_pasos.py`). Without
  `--resume`, Uploadrr-EMU wipes its `tmp/<name>` first, so a re-prepared file never meets a stale `BASE.torrent`.
  The nightly maintenance deletes a `tmp/<name>` nobody touched for `mantenimiento.dias_tmp` days (7) unless its job is
  running; `--resume` and `--solo-cliente` then start from nothing and regenerate it.
  «Reenviar a qBittorrent» (`upload_execution/reenvio_qbit.py`) launches the same argv plus `--resume --solo-cliente <id>`:
  Uploadrr-EMU only re-adds that eMuwarez torrent to qBittorrent, exits 0 only if it was added and never prints `Torrent URL:`.
  eMuwarez hides every new upload until a moderator approves it (up to a day): `/api/torrents/<id>` is a 404 and the
  `uploader=` search leaves it out, so `mis_torrents.pendiente_de_moderacion` flags a completed, non-anonymous upload
  missing from a later «Tus subidas» read, without asking the tracker anything else.
  The 30-calls-a-minute eMuwarez budget is shared by the server, the CLI and every Uploadrr-EMU: `_lanzar_uploadrr` sets
  `LIMITADOR_DIR` (`CONFIG_DIR/limitador`), and `integrations/limitador_compartido.py` and `uploadrr-emu/src/utils/limitador.py`
  must stay byte-identical (`test_limitador_compartido.py`); without `fcntl` or a usable directory each process counts alone.
- One UNIT3D client: every eMuwarez request, the app's (`emu.cliente`) and Uploadrr-EMU's (`Unit3d._cliente`), goes through
  `integrations/cliente_unit3d.py`. It builds URL and headers (`User-Agent: emuwarr/<version>`; `_lanzar_uploadrr` passes the
  version as `EMUWARR_VERSION`), sends the key only to the tracker's own origin and takes a slot in the limiter first. The key goes
  as `Authorization: Bearer` (the next UNIT3D only takes that); a 401/403 is retried once, with another slot, as `?api_token=`.
  `uploadrr-emu/src/utils/cliente_unit3d.py` is a byte-identical copy (`test_cliente_unit3d.py`; stdlib only, Python 3.9).
- TMDb keys travel in the URL query, and so do eMuwarez's on that retry. Never log or return a raw URL: use
  `integrations.base.url_sin_claves`.
- Image: `ghcr.io/kisnov/emuwarr`, built only when a version is published. `publicar.yml` ("Publicar
  versión": by hand, or on odd days of the month when 3+ PRs from `feat/` or `fix/` branches (merge commits, or `feat:`/`fix:` squash
  commits) merged since the last `__version__` change) runs
  the full CI on dev's tip, bumps `__version__` (`auto`: minor if any `feat/` PR, else patch), pushes that commit to `dev`, fast-forwards
  `main` to it and pushes `vX.Y.Z` (all atomic), builds `:X.Y.Z`, `:<sha>`, `:latest` and `:dev`, publishes the manifest (secret
  `UPLOADRR_VERSION_TOKEN`) and creates the GitHub release with the PRs merged since the previous tag. Release when Gabriel asks: `gh workflow run publicar.yml -f notas="…"`, with a
  neutral public note. Every push to `dev` (except docs/`.md`-only) and `solo_prueba=true` build `:dev` from dev's tip only (no tests). The long-lived branch `multitracker` (Gabriel's multi-tracker work, `docs/PLAN.md`; its PRs target it, `dev` is merged into it, never rebased) builds `:multitracker` the same way and can never publish a version. `ref=<commit|branch|tag>` publishes that commit's version instead (no tests; the version commit and tag go to `release/vX.Y.Z`, to merge into dev afterwards; neither `main` nor `:dev` moves). `scripts/desplegar.sh` is
  the local fallback (no Actions minutes, no tests, amd64 only: a version published with it leaves `:latest` without
  arm64, so prefer `publicar.yml`, which builds amd64 and arm64 via QEMU) and pushes in the same order; `--solo-prueba` pushes only
  `:dev`. The image copies `uploadrr-emu/` from the same commit (`.dockerignore` keeps out its tests and any local
  `config.yaml` or cookies), so a feat/fix PR there counts towards a release like any other.
- Version manifest: the Diagnóstico card compares the image's `APP_COMMIT` with `latest.json` in the public
  repo `Kisnov/uploadrr-version` (a fork of `Rovaal-code/uploadrr-version`); to test against another
  manifest, set `UPLOADRR_VERSION_MANIFEST_URL`. It is a separate repo so the app reads one
  static JSON with no token, instead of asking GHCR's registry API (anonymous tokens weren't issued).
  `desplegar.sh` publishes it after `docker push` via `scripts/publicar_version.py --repo`; don't edit it by
  hand, run that script. The file is public: keep `--notas` neutral.
- Image Python deps: `constraints.txt` pins the `web` extra and what it pulls (Uploadrr-EMU pins its own in its
  `requirements.txt`); the Dockerfile and CI install with `-c constraints.txt`. Raising a minimum in the `web` extra
  means updating the pin too (`test_imagen_docker.py` checks they agree); the header of `constraints.txt` says how.
- Uploadrr-EMU's state in the image: `/opt/Uploadrr/data` is real and refreshes with each image; only `config.yaml`,
  `cookies/` and `tmp` are symlinks, baked at build into `/config/Uploadrr`. The entrypoint just
  creates the targets, and seeds `config.yaml` from the image's example the first time. Nothing under
  `/opt/Uploadrr` is writable except `data/` (1777), so don't make the entrypoint create links there.
- Building on Windows: `.gitattributes` keeps text files LF, so a new checkout builds with `docker build .`. A checkout
  made before it is still CRLF, and a CRLF `docker-entrypoint.sh` fails with "no such file or directory": build
  from `git -c core.autocrlf=false archive HEAD`.

## Plan

What is still pending (to decide, to test on the NAS) lives in `docs/PLAN.md`; what is done lives in the commit
history and the PRs, so delete an item from the plan when it is finished. Read it before starting plan work and
update it in the same PR that closes a point. The older detailed review notes are in `tmp/analisi/` (gitignored, on
Gabriel's PC only).
