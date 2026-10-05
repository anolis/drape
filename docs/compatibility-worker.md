# Independent compatibility worker

Cards load immediately from catalog data and the local compatibility index. Unknown
cards remain visible with “Compatibility unverified”; known format mismatches are filtered.
Missing Cinnamon dialog styles remain visible as cosmetic warnings with an explicit
Install anyway choice. Inspection caches retain the original stylesheet findings.
After the visible view stops scrolling for two seconds, Drape checks missing evidence
for visible cards using this separate worker. Archive work never runs in the GTK process.
Hovering anywhere on a card gives it immediate queue priority, without waiting for
scrolling to settle. A running check finishes first; request pacing, job gaps and server
cooldowns still apply. Cards with useful cached contents do not need another check.

Only one file is checked at a time, with a ten-second pause between requests and a
ten-second gap between jobs. Each process has a 384 MiB address-space limit and a
90-second deadline. Leaving the viewport, switching sections or closing a profile
cancels its check. Losing window focus does not pause or cancel checks; hidden views
remain excluded. Cancelled and failed checks save no verdict.
Once a card has useful cached contents, automatic checks move on instead of inspecting
every distro/color variant under that card. Unrecognized downloads get at most three
attempts per card per session, yielding to other visible cards. Cancelled checks and
rate-limit responses do not consume that budget. Use the explicit worker for exhaustive
variant inspection. Uninspected variants stay unknown and cannot prove incompatibility.
HTTP 429 pauses the queue for the reported cooldown. Disable automatic checks through
**☰ → Inspect visible themes when scrolling stops**; existing index evidence still works.

Each archive inspection resolves its download gateway once, then reuses the storage
URL for subsequent ZIP ranges or larger tar prefixes. The resolved signed URL stays
in memory for that inspection and is never saved in the index or exported.
Server cooldowns persist locally across worker exits and app restarts, independently
of compatibility evidence. Cached results and offline inspections remain available;
new remote checks wait, and the app also defers downloads from the same blocked host.

The scanner status row is visible in the main window and uploader profiles. Active
archive checks show the theme/download name and a spinner on the card. Waiting for
scrolling, cancellation, failures and rate-limit countdowns are described explicitly.
The saved-theme backfill shows the current theme and a count-based progress bar;
it reaches 100% only after all cache writes finish and the worker exits successfully.
Interrupted or failed backfills retain their partial progress and report pending checks.

Downloaded archives are inspected locally during installation, before unsupported
components are filtered out. Their evidence goes straight into the same index, even
when the archive turns out to be incompatible. No additional download is needed.
At startup a bounded offline worker also backfills existing installations immediately,
without waiting for scrolling or visible cards. Installed files supply partial evidence,
because earlier installs may have discarded other archive components. This local-only
evidence is not exported as a complete archive inspection or community contribution.
Later launches reuse installed evidence after a small metadata fingerprint check of
component roots, engine files and Cinnamon styles. Matching downloads, fingerprints
and inspection rules skip the tree inspection and cache write. Changes, missing cache
rows or older schemas trigger a local rescan; old rows need one fingerprint backfill.
The completion summary distinguishes reused evidence from newly inspected themes.


Complete inspections persist across launches until the checksum/modification date or
inspection rules change. Incomplete listings are cached for a day before another attempt.
The index is stored at `~/.local/share/drape/compatibility.sqlite3` (or under
`$XDG_DATA_HOME`). Evidence is evaluated against the current desktop, so changing
sessions does not require downloading the same archives again.

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
in the working directory. Visible cards refresh their evidence during idle checks; with automatic checks disabled,
close and reopen a catalog section to read updated evidence.
Each record is keyed by item, filename and checksum/modification date. Complete and recently incomplete
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
