---
name: bublik-migrate-run
description: Copy a run from one Bublik instance to another (e.g. from a local instance to a server one) given the run URL, publishing its logs where the target instance imports from if needed; or only publish run logs from local storage to external storage. Use when asked to migrate, copy, move or transfer a run or an import between Bublik instances, or to publish run logs for an import.
license: Apache-2.0
---

# Copying a run between Bublik instances

Run every command from the root of the Bublik repository, with the Python of
the Bublik virtualenv. Each command prints a JSON object to stdout.

## Steps

1. Run:

   ```
   python3 scripts/migrate_run.py migrate <run URL> --to <target Bublik URL>
   ```

   - `<run URL>`: the run page in the source instance, e.g.
     `http://localhost/bublik/v2/runs/42`;
   - `<target Bublik URL>`: any URL of the target instance, e.g.
     `https://bublik.example.com/bublik/`.

   If the user gave the project to import into, or where to publish the logs,
   add them as `--project <project>` or `--dest <destination> --dest-url <URL>`.

2. While the output has `next`, do exactly what it says, and nothing else:
   - `next.run`: run this command as it is;
   - `next.ask`: ask the user this question, then run `next.then_run` with the
     placeholders in angle brackets (`<PROJECT>`, `<DESTINATION>`, `<URL>`)
     replaced by the answer, quoted for the shell if needed.

3. When the output has no `next`, tell the user its `summary`.

## Rules

- Never guess the answers to `next.ask`: ask the user.
- Don't copy logs, call Bublik's API, or import runs by other means, and don't
  work around a failure of the script: if `status` is `error`, tell the user
  the `summary` and stop.
- Never import with force: a run that already exists in the target instance
  (`status` `skipped`) stays as it is.

## Only publishing logs

To publish run logs without importing them:

```
python3 scripts/migrate_run.py publish <logs URL or directory> \
    --dest <destination> --dest-url <URL> [--to <Bublik URL>]
```

then follow steps 2 and 3. `publish.url` in the output is the URL to import the
logs from.
