"""Page styling for the LinkedIn product demo.

System fonts only: the demo makes no network requests, so no web fonts are loaded.
"""

from __future__ import annotations

_BASE = """
<style>
:root {
  --bg: #FAFAF8;
  --surface: #FFFFFF;
  --ink: #111418;
  --ink-2: #3D434C;
  --muted: #6B7280;
  --line: #E4E4E0;
  --line-strong: #CFCFC9;
  --navy: #1B2A41;
  --accent: #0E7C66;
  --accent-soft: #E8F3F0;
  --radius: 10px;
  --font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
}

.stApp { background: var(--bg); color: var(--ink); font-family: var(--font); }
.stApp button, .stApp input, .stApp p, .stApp li { font-family: var(--font); }
[data-testid="stDecoration"] { display: none; }
header[data-testid="stHeader"] { background: transparent; }

[data-testid="stMainBlockContainer"], .block-container {
  max-width: 1040px;
  padding-top: 2.25rem;
  padding-bottom: 2rem;
}

/* ---------- Top navigation ---------- */
.bt-nav {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 0 14px 0; border-bottom: 1px solid var(--line); margin-bottom: 18px;
}
.bt-brand { display: flex; align-items: baseline; gap: 10px; }
.bt-brand-mark { font-weight: 700; font-size: 17px; letter-spacing: 0.06em; color: var(--navy); }
.bt-brand-name { font-size: 15px; color: var(--ink-2); }
.bt-badge {
  font-size: 11.5px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;
  color: var(--ink-2); border: 1px solid var(--line-strong); border-radius: 999px; padding: 3px 10px;
  background: var(--surface);
}

/* ---------- Progress ---------- */
.bt-progress { display: flex; align-items: center; gap: 0; margin: 2px 0; }
.bt-pstep { display: flex; align-items: center; gap: 8px; font-size: 13.5px; color: var(--muted); white-space: nowrap; }
.bt-pdot {
  width: 22px; height: 22px; border-radius: 50%; border: 1px solid var(--line-strong);
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 11.5px; font-weight: 600; background: var(--surface); color: var(--muted);
}
.bt-pstep.is-current { color: var(--ink); font-weight: 600; }
.bt-pstep.is-current .bt-pdot { border: 1.5px solid var(--accent); color: var(--accent); }
.bt-pstep.is-done { color: var(--ink-2); }
.bt-pstep.is-done .bt-pdot { background: var(--navy); border-color: var(--navy); color: #FFFFFF; }
/* Checkmark drawn in CSS: Streamlit's HTML sanitizer strips inline SVG. */
.bt-pstep.is-done .bt-pdot::after, .bt-check::after {
  content: ""; width: 5px; height: 10px; border: solid #FFFFFF; border-width: 0 2px 2px 0;
  transform: translateY(-1px) rotate(45deg);
}
.bt-pstep.is-done .bt-pdot::after { width: 4px; height: 8px; }
.bt-pline { flex: 0 0 36px; height: 1px; background: var(--line-strong); margin: 0 12px; }
.bt-pline.is-done { background: var(--navy); }

/* Restart: quiet text action aligned with the progress row */
.st-key-restart { display: flex; justify-content: flex-end; }
.stApp .st-key-restart button { color: var(--muted); font-size: 13px; padding: 0; min-height: 0; }
.stApp .st-key-restart button:hover { color: var(--ink); }
.stApp .st-key-restart button p { font-size: 13px; font-weight: 500; }

/* ---------- Step heading ---------- */
.bt-eyebrow {
  font-size: 12px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--accent); margin: 14px 0 6px 0;
}
.bt-headline { font-size: 30px; line-height: 1.2; font-weight: 650; color: var(--ink); letter-spacing: -0.015em; margin: 0 0 6px 0; }
.bt-sub { font-size: 15.5px; color: var(--ink-2); margin: 0 0 20px 0; max-width: 680px; line-height: 1.5; }

/* ---------- Cards ---------- */
.bt-card {
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 22px 24px; margin-bottom: 18px;
}
.bt-card-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.bt-account { font-size: 18px; font-weight: 650; color: var(--ink); }
.bt-role { font-size: 14px; color: var(--muted); margin-top: 2px; }
.bt-tag {
  font-size: 11.5px; color: var(--muted); border: 1px solid var(--line); border-radius: 6px;
  padding: 2px 8px; white-space: nowrap; background: var(--bg);
}
.bt-label {
  font-size: 11.5px; font-weight: 600; letter-spacing: 0.07em; text-transform: uppercase;
  color: var(--muted); margin-bottom: 6px;
}
.bt-quote {
  margin: 18px 0 0 0; padding: 2px 0 2px 16px; border-left: 2px solid var(--navy);
  font-size: 18px; line-height: 1.55; color: var(--ink);
}
.bt-quote mark {
  background: transparent; color: inherit; padding: 0;
  border-bottom: 2px solid var(--accent); box-decoration-break: clone;
}
.bt-fields { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0; margin-top: 20px; border-top: 1px solid var(--line); }
.bt-field { padding: 14px 16px 2px 0; }
.bt-field + .bt-field { padding-left: 18px; border-left: 1px solid var(--line); }
.bt-field-value { font-size: 17px; font-weight: 600; color: var(--navy); }

/* Evidence */
.bt-evidence-type { font-size: 12px; font-weight: 600; color: var(--accent); letter-spacing: 0.06em; text-transform: uppercase; }
.bt-evidence-title { font-size: 19px; font-weight: 650; margin-top: 4px; color: var(--ink); }
.bt-evidence-change { font-size: 15px; color: var(--ink-2); margin-top: 4px; }
.bt-evidence-change b { color: var(--ink); }

/* Threshold comparison */
.bt-compare { display: grid; grid-template-columns: 180px 180px 1fr; gap: 28px; align-items: center; }
.bt-stat-value { font-size: 30px; font-weight: 650; color: var(--navy); letter-spacing: -0.01em; }
.bt-stat-value.is-observed { color: var(--accent); }
.bt-scale { position: relative; height: 64px; }
.bt-scale-track { position: absolute; left: 0; right: 0; top: 30px; height: 6px; border-radius: 3px; background: #EEEEEA; }
.bt-scale-over { position: absolute; top: 30px; height: 6px; border-radius: 0 3px 3px 0; background: var(--accent-soft); }
.bt-scale-threshold { position: absolute; top: 18px; width: 2px; height: 30px; background: var(--navy); }
.bt-scale-threshold-label { position: absolute; top: 0; transform: translateX(-50%); font-size: 12px; color: var(--navy); font-weight: 600; white-space: nowrap; }
.bt-scale-point {
  position: absolute; top: 25px; width: 16px; height: 16px; margin-left: -8px; border-radius: 50%;
  background: var(--accent); border: 3px solid var(--surface); box-shadow: 0 0 0 1px var(--accent);
}
.bt-scale-point-label { position: absolute; top: 46px; transform: translateX(-50%); font-size: 12px; color: var(--accent); font-weight: 600; white-space: nowrap; }
.bt-scale-ends { position: absolute; top: 46px; font-size: 11.5px; color: var(--muted); }
.bt-note { font-size: 15px; color: var(--ink-2); margin: 16px 0 0 0; padding-top: 14px; border-top: 1px solid var(--line); }

/* Recognize */
.bt-side { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 18px; }
.bt-side .bt-card { margin-bottom: 0; }
.bt-side-text { font-size: 17px; line-height: 1.5; color: var(--ink); }
.bt-question { font-size: 18px; font-weight: 600; color: var(--ink); margin: 6px 0 12px 0; }
.bt-verified {
  display: flex; align-items: center; gap: 12px; background: var(--accent-soft);
  border: 1px solid #BFDDD5; border-radius: var(--radius); padding: 14px 18px; margin: 4px 0 14px 0;
}
.bt-verified-title { font-size: 16px; font-weight: 650; color: var(--accent); }
.bt-verified-note { font-size: 13.5px; color: var(--ink-2); margin-top: 2px; }
.bt-check {
  flex: 0 0 26px; width: 26px; height: 26px; border-radius: 50%; background: var(--accent);
  display: inline-flex; align-items: center; justify-content: center;
}
.bt-declined {
  background: var(--surface); border: 1px solid var(--line-strong); border-radius: var(--radius);
  padding: 14px 18px; margin: 4px 0 14px 0;
}
.bt-declined-title { font-size: 16px; font-weight: 650; color: var(--ink); }
.bt-declined-note { font-size: 13.5px; color: var(--ink-2); margin-top: 2px; }

/* Prepare */
.bt-output { font-size: 16.5px; line-height: 1.6; color: var(--ink); max-width: 760px; }
.bt-output p { margin: 0 0 12px 0; }
.bt-email-meta { font-size: 13.5px; color: var(--muted); border-bottom: 1px solid var(--line); padding-bottom: 10px; margin-bottom: 14px; }
.bt-email-meta b { color: var(--ink); font-weight: 600; }
.bt-draft-flag { font-size: 12px; color: var(--muted); margin-top: 6px; }
.stApp .bt-questions { margin: 0; padding: 0; list-style: none; counter-reset: q; }
.stApp .bt-questions li { margin: 0; counter-increment: q; display: flex; gap: 14px; padding: 10px 0; border-bottom: 1px solid var(--line); }
.bt-questions li:last-child { border-bottom: 0; }
.bt-questions li::before { content: counter(q); flex: 0 0 22px; font-size: 13px; font-weight: 600; color: var(--accent); padding-top: 2px; }

.bt-boundary {
  margin-top: 20px; background: var(--navy); color: #FFFFFF; border-radius: var(--radius);
  padding: 18px 24px; text-align: center;
}
.bt-boundary-main { font-size: 18px; font-weight: 600; letter-spacing: -0.005em; }
.bt-boundary-sub { font-size: 13px; color: #C9D1DC; margin-top: 6px; letter-spacing: 0.02em; }

.bt-footer { margin-top: 28px; padding-top: 12px; border-top: 1px solid var(--line); font-size: 12px; color: var(--muted); text-align: center; }

/* ---------- Streamlit widgets ---------- */
.stButton button {
  border-radius: 8px; font-weight: 600; padding: 0.55rem 1.1rem; box-shadow: none;
}
.stButton button p { font-size: 15px; font-weight: 600; }
.stButton button[kind="primary"], [data-testid="stBaseButton-primary"] {
  background: var(--navy); border: 1px solid var(--navy); color: #FFFFFF;
}
.stButton button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {
  background: #24375A; border-color: #24375A; color: #FFFFFF;
}
.stButton button[kind="secondary"], [data-testid="stBaseButton-secondary"] {
  background: var(--surface); border: 1px solid var(--line-strong); color: var(--ink);
}
.stButton button[kind="secondary"]:hover, [data-testid="stBaseButton-secondary"]:hover {
  border-color: var(--navy); color: var(--navy);
}
.stButton button:focus:not(:active) { box-shadow: none; }

[data-testid="stTabs"] [role="tablist"] { gap: 6px; border-bottom: 1px solid var(--line); }
[data-testid="stTab"] { padding: 10px 14px; }
[data-testid="stTab"] p { font-size: 14.5px; font-weight: 600; color: var(--muted); }
[data-testid="stTab"][aria-selected="true"] p { color: var(--ink); }
[data-testid="stTabs"] .react-aria-SelectionIndicator { background-color: var(--navy); height: 2px; }
[data-testid="stTabPanel"] {
  background: var(--surface); border: 1px solid var(--line); border-top: 0;
  border-radius: 0 0 var(--radius) var(--radius); padding: 22px 24px;
}

[data-testid="stExpander"] details { border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); }
[data-testid="stExpander"] summary p { font-size: 13.5px; color: var(--muted); }
[data-testid="stSidebar"] { background: var(--surface); border-right: 1px solid var(--line); }

@media (max-width: 760px) {
  .bt-fields, .bt-side, .bt-compare { grid-template-columns: 1fr; }
  .bt-field + .bt-field { padding-left: 0; border-left: 0; border-top: 1px solid var(--line); }
  .bt-progress .bt-plabel { display: none; }
  .bt-pline { flex-basis: 18px; margin: 0 6px; }
  .bt-headline { font-size: 25px; }
}
</style>
"""

# Recording mode: no Streamlit chrome, no sidebar, tighter vertical rhythm for a 16:9 capture.
_RECORDING = """
<style>
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stStatusWidget"],
[data-testid="stMainMenu"], [data-testid="stAppDeployButton"], #MainMenu, footer,
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stExpandSidebarButton"] {
  display: none !important;
}
[data-testid="stMainBlockContainer"], .block-container {
  max-width: 1120px; padding-top: 1.5rem; padding-bottom: 1rem;
}
.bt-eyebrow { margin-top: 8px; }
.bt-headline { font-size: 32px; }
.bt-card { padding: 20px 24px; margin-bottom: 14px; }
.bt-footer { margin-top: 18px; }
</style>
"""


def page_css(recording: bool) -> str:
    return _BASE + (_RECORDING if recording else "")
