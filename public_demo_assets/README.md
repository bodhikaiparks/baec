# Public demo assets

Purpose: authentic prerecorded synthetic AI output for public demonstration (BAEC Engine 1).

This directory holds exactly three files:

- `README.md` (this file);
- `baec-engine1-harbor-recording.json`, the one recording (format `baec-public-demo-recording/v1`). It is not seed
  data, not a test fixture, and not a live-evaluation report;
- `BAEC_Engine_1_Research_and_Technical_Brief.pdf`, the approved Research and Technical Brief, offered unchanged as
  the landing page's Research Brief download.

- **Source.** The canonical synthetic interaction `INT-HARBOR-001` (account `ACC-HARBOR`) from `data/demo`. No real
  customer, company, contact, pricing, or health information.
- **Content.** The exact persisted Phase 6 rows of one successful extraction: run, terminal result, raw returned
  text, artifact, and excerpts. Nothing is summarized.
- **Producer.** Only `scripts/record_public_demo_artifact.py`, run manually, making exactly one Anthropic request
  with retries disabled. The recording does not exist until that command is run and succeeds.
- **Loader.** Only `baec_app.application.public_demo_recording.load_public_demo_database`, which reads this fixed
  path, verifies the package digest, refuses fixture or fake-provider provenance, and replays the rows into a fresh
  in-memory database. No model is called while the demo runs.
- **Integrity.** The package digest gives repository-asset integrity only. It does not prove that a file was never
  copied between systems.

The full BAEC working paper is unpublished and is not packaged with the public demo. Readers interested in reviewing
the manuscript or providing feedback may contact the author directly.

This recording is one successful synthetic demonstration artifact. It does not change Phase 6 behavioral
eligibility, does not qualify a default model, and does not validate the BAEC theory. The model it names is
historical provenance, not a recommendation.
