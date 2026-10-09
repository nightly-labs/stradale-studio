# Work rules

- Work on `main`. Keep completed changes on `main`.
- Use simple, clear English in user messages.
- Keep all image processing local. Do not add cloud calls.
- Never overwrite source images. Write results into a separate batch folder.
- Keep queue progress in SQLite. Bound worker concurrency and recover interrupted jobs.
- Run `uv run python -m unittest discover -s tests -v` for queue or rendering changes.
- Run `scripts/build-app.sh` and test the app after changes to native integration.
