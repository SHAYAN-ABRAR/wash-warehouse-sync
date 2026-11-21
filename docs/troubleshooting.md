# Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Plan was prepared for version N but the current version is M` | Another plan was applied or a rollback happened after this preview. Run the preview again. |
| `Plan changed after approval; approve it again` | A decision was recorded after approval. Re-approve to lock the decisions. |
| `Another operation is running` | Only one init/apply runs at a time. If a process crashed, `wash-sync recover` clears a stale lock. |
| `Verification failed; nothing was published` | The new workbook failed an integrity or preservation check. The current version is unchanged; keep the audit and report the failed checks. |
| `Result would need N rows; one worksheet holds at most 1,048,576` | The warehouse would exceed Excel's single-sheet limit. Nothing is written; the design requires one sheet. |
| `Value … has N characters; Excel's limit is 32,767` | Typically an overlong lithology profile. It is never truncated; decide it in review. |
| A file is not recognized | Check that the header row is present. Columns match by name — see `source_profiles.json`. |
| Bangla admin names flagged as unmapped | Mappings are learned only from verified warehouse rows. Add a validated alias in Settings. |
| `structural edits are refused` | The workbook contains formulas, merged cells, tables or conditional formats near the edit. The writer will not risk corrupting references; remove them from the data sheet. |
| Explorer shows "No warehouse registered" | Run `wash-sync init --warehouse <file>` (or use the same `--state-dir` as the server). |
| The web page says the interface is not built | `npm --prefix apps/web ci && npm --prefix apps/web run build:local`. |
| The browser preview differs from the engine | The preview uses simplified rules on synthetic data; the engine dry run is authoritative. |
| Index build after every new version | Expected: each version is indexed once (cached by SHA-256). Deleting `index/` is always safe. |
