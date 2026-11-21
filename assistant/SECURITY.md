# Security notes (9 November 2026)

What was checked, what was fixed, and what is still open. Run `python -m pytest -q` (140 tests) and
`python evals/run_red_flags.py` (62/62 urgent, 27/27 routine) after any change.

## Checked with tools
- `bandit` static scan: the only findings are SQL strings built from column and table names that are
  constants in the code. Every value that comes from a person is passed as a parameter.
- `pip-audit` on requirements.txt: no known vulnerabilities on 9 November 2026. Re-run before each release.

## Fixed in this pass
| Area | Problem | Fix | Test |
| --- | --- | --- | --- |
| Safety | A voice note sent after the hourly limit got "slow down" and never reached the emergency check. Same in the app. | Voice notes are transcribed and checked before the limit applies, with a hard stop at three times the limit. The app sends the emergency card, then the limit message. | `test_voice_note_emergency_gets_the_card_even_when_rate_limited`, `test_runaway_voice_notes_hit_a_hard_stop`, `test_app_emergency_gets_the_card_even_when_rate_limited` |
| Privacy | FORGET ME left app sign-in sessions and codes in place. | Both are deleted with everything else. | `test_forget_me_signs_the_app_out_too` |
| Browser | No security headers; interactive API docs were public. | Content-Security-Policy, HSTS, nosniff, no framing, no-referrer, Permissions-Policy on every response; `/docs`, `/redoc`, `/openapi.json` removed. | `test_security_headers_and_no_public_api_docs` |
| Denial of service | Uploads were read fully into memory before the size check. | Requests over 12 MB are refused before they are read, including chunked ones. | `test_oversized_uploads_are_refused_before_they_are_read` |
| Uploads | A photo's type was taken on trust. | The data must be valid base64 and start with the right file signature. Text fields have length limits. | `test_uploads_must_be_what_they_claim` |
| Token leak | The Meta token was sent to whatever address a media lookup returned. | Media ids are validated and downloads only go to Meta's own hosts over HTTPS. | `test_media_is_only_fetched_from_meta_hosts` |
| Sign-in | Throttle table could grow without limit; codes compared with `!=`. | Table is pruned and capped; per-number limit on code checks; constant-time compare. | covered by sign-in tests |
| Files | Database and usage log were created with default permissions. | Both are set to owner-only (0600). | |

## New, and off by default
- **Twi and Ewe danger phrases** (`safety_local.json`): drafts, shadow mode only. A match is counted in the
  usage log (`emergency_shadow`), never quoted, and no card is sent until `SAFETY_LOCAL=on`. Do not turn
  this on before native-speaking clinicians have reviewed the phrases.
- **SMS fallback** (`sms.py`): off unless `SMS_PROVIDER`, `AT_USERNAME`, `AT_API_KEY` and `SMS_INBOUND_KEY`
  are all set. The callback address carries the key, and a wrong key looks like a 404. SMS is not
  end-to-end encrypted, so the privacy notice must be updated before it is used for real.

## Still open (be honest about these)
- The dashboard key travels in the address. Fine for a prototype; move to a signed-in session before a pilot.
- Sign-in throttles count by network address, which is the tunnel's address today, so they are shared by everyone.
- The app keeps its session token in the browser's local storage. The strict Content-Security-Policy is the
  main protection; consider a secure cookie when hosting moves.
- SQLite on one machine, no encryption at rest, no backups policy. To be settled in the Ghana hosting plan.
- No independent penetration test yet. Budgeted before any real patient data.
- The Africa's Talking send and callback formats were written from its documentation and are untested
  against the live service.
