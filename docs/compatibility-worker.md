# Independent compatibility worker

Browsing reads the local compatibility index. It never opens theme archives or starts
compatibility scan/retry jobs. Unknown themes stay visible with “Compatibility unverified”;
only known incompatible results are hidden. Installed-file checks and install/apply
validation still follow the desktop and window manager in use.

Run archive inspection explicitly in a separate process:

```sh
python3 -m drape.compatibility_worker 1166289 1405756
python3 -m drape.compatibility_worker --kind desktop --query purple --pages 2
```

The worker needs Python 3.12+ and Requests. It runs without GTK, PyGObject, Pillow or
a desktop session. It processes items sequentially and defaults to a one-second pause
between items and a 512 MiB address-space limit. ZIP reads require range support;
tar reads consume a bounded prefix even when a host ignores Range, and decompression
is bounded. HTTP 429 and failed inspections save no verdict; rerun later to retry them.
Exit status is 0 for success, 2 for deferred/failed file checks, and 1 for job errors.

By default it updates the local `compatibility.sqlite3` and writes `compatibility.json`
in the working directory. Close and reopen a catalog section to read updated evidence.
Each record is keyed by item, filename and checksum/modification date. Already fresh
results are reused, and unchanged observations do not advance the diff cursor.
It records archive formats and stylesheet markers, with status `unknown` and headless
scanner context. The app evaluates that evidence against its own desktop and versions;
the worker does not claim a theme works on the worker machine.

For a separate backend workspace:

```sh
python3 -m drape.compatibility_worker --kind gtk --pages 3 \
  --database /path/to/backend/compatibility.sqlite3 \
  --output /path/to/backend/compatibility.json

./bin/drape compatibility import /path/to/backend/compatibility.json
```

The portable snapshot omits signed download URLs, tokens and local theme paths.
Snapshots use the existing validated import format, so a later hosted service can
publish the same JSON through Supabase Storage, object storage or a static endpoint.
The app can explicitly import a reviewed public snapshot with:

```sh
./bin/drape compatibility fetch https://your-host.example/compatibility.json
```

Downloads require HTTPS and are limited to 16 MiB. Imports validate the complete document
before writing; malformed snapshots leave existing evidence intact. Existing local
evidence takes precedence over imported evidence.

No hosted project, automatic upload, service credentials or scheduled worker is configured.
Choose hosting after validating the local worker and its index output. Community
contributions should enter a reviewed ingestion path rather than overwrite the published
snapshot directly.
