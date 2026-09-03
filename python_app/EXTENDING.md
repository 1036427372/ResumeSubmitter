# Extending ResumeSubmitter

The application is intentionally split into small layers:

- `profile_schema.py`: add profile fields, resume section forms, page categories,
  application statuses, failure reasons, export scopes and formats here first.
- `storage.py`: owns the local JSON library, attachment copies, stable IDs and
  schema migrations. Keep migrations backward-compatible and bump
  `schemaVersion` when the stored shape changes.
- `resume_parser.py`: extracts facts from PDF/DOCX/TXT. Sensitive facts can be
  stored locally, but should not be enabled for automatic web filling by default.
- `browser_fill.py`: browser inspection, conservative field matching and the
  user-triggered fill script. Add aliases here only when the meaning is clear.
- `llm.py`: OpenAI-compatible calls. Model output must remain reviewable and
  must never directly submit a job application.
- `main.py`: Qt views and workflows. Prefer a worker plus a modal review dialog
  for any network or model operation so the browser and UI stay responsive.

## Adding a common profile field

1. Add the path to `profile_schema.py` and its default value in `storage.py`.
2. Add unambiguous aliases in `browser_fill.py`.
3. Add parsing only if the value can be extracted reliably from a resume.
4. Add the path to any model allow-list in `llm.py`.
5. Mark sensitive fields in the privacy policy and keep automatic filling off
   unless the user explicitly enables it.

## Future cloud sharing

The profile already has `profileId`, `schemaVersion`, `updatedAt`, `sync` and
item-level IDs/timestamps. A future sync adapter should be added as a separate
module. It must never upload API keys or personal data until the user explicitly
chooses a workspace and grants sharing permissions. Use per-item timestamps and
conflict review instead of silently overwriting local edits.
