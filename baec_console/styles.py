"""Page styling for BAEC Console. System fonts only: the console makes no network requests.

The visual language follows demo_linkedin (off-white, navy, one restrained accent), re-declared here so the
console never imports the demo.
"""

CSS = """
<style>
:root {
  --bg: #FAFAF8; --surface: #FFFFFF; --ink: #111418; --ink-2: #3D434C; --muted: #6B7280;
  --line: #E4E4E0; --line-strong: #CFCFC9; --navy: #1B2A41; --accent: #0E7C66; --accent-soft: #E8F3F0;
  --warn: #8A5A00; --warn-soft: #FBF3E2; --stop: #9B2C2C; --stop-soft: #FBECEC; --radius: 10px;
  --font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
}
.stApp { background: var(--bg); color: var(--ink); font-family: var(--font); }
.stApp button, .stApp input, .stApp textarea, .stApp p, .stApp li, .stApp label { font-family: var(--font); }
[data-testid="stDecoration"] { display: none; }
header[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainBlockContainer"], .block-container { max-width: 1180px; padding-top: 2rem; padding-bottom: 3rem; }

/* Sidebar */
[data-testid="stSidebar"] { background: var(--surface); border-right: 1px solid var(--line); }
[data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding-top: 0.5rem; }
.bc-brand { padding: 4px 4px 18px 4px; border-bottom: 1px solid var(--line); margin-bottom: 14px; }
.bc-brand-mark { font-weight: 750; font-size: 19px; letter-spacing: 0.09em; color: var(--navy); }
.bc-brand-sub { font-size: 13px; color: var(--muted); margin-top: 1px; }
[data-testid="stSidebar"] .stButton button {
  width: 100%; justify-content: flex-start; border: 0; background: transparent; color: var(--ink-2);
  padding: 0.45rem 0.7rem; border-radius: 8px; box-shadow: none;
}
[data-testid="stSidebar"] .stButton button p { font-size: 14.5px; font-weight: 550; }
[data-testid="stSidebar"] .stButton button:hover { background: #F2F2EE; color: var(--ink); }
[data-testid="stSidebar"] .stButton button[kind="primary"] { background: var(--accent-soft); color: var(--navy); }
[data-testid="stSidebar"] .stButton button[kind="primary"] p { font-weight: 650; }
.bc-side-label { font-size: 11px; font-weight: 650; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted);
  margin: 18px 4px 6px 4px; }
.bc-side-note { font-size: 12.5px; color: var(--muted); margin: 0 4px; line-height: 1.45; }

/* Page heading */
.bc-eyebrow { font-size: 12px; font-weight: 650; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent);
  margin: 0 0 4px 0; }
.bc-title { font-size: 28px; line-height: 1.2; font-weight: 680; color: var(--ink); letter-spacing: -0.015em; margin: 0 0 4px 0; }
.bc-sub { font-size: 15px; color: var(--ink-2); margin: 0 0 20px 0; max-width: 760px; line-height: 1.5; }
.bc-h { font-size: 17px; font-weight: 650; color: var(--ink); margin: 6px 0 8px 0; }

/* Cards and metrics */
.bc-card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 18px 20px;
  margin-bottom: 14px; }
.bc-metrics { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin: 4px 0 22px 0; }
.bc-metric { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 14px 16px; }
.bc-metric-value { font-size: 28px; font-weight: 680; color: var(--navy); letter-spacing: -0.01em; }
.bc-metric-label { font-size: 12.5px; color: var(--muted); margin-top: 2px; }
.bc-label { font-size: 11.5px; font-weight: 650; letter-spacing: 0.07em; text-transform: uppercase; color: var(--muted);
  margin-bottom: 6px; }
.bc-quote { padding: 2px 0 2px 14px; border-left: 2px solid var(--navy); font-size: 16.5px; line-height: 1.55;
  color: var(--ink); margin: 4px 0 8px 0; }
.bc-muted { color: var(--muted); font-size: 13.5px; }
.bc-kv { display: grid; grid-template-columns: 170px 1fr; gap: 6px 14px; font-size: 14.5px; margin-top: 6px; }
.bc-kv div:nth-child(odd) { color: var(--muted); }

/* Research overview */
.bc-system-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin: 0 0 18px 0;
}
.bc-system-card {
  margin-bottom: 0;
  height: 100%;
  min-height: 255px;
}
.bc-system-card-engine {
  font-size: 15px;
  font-weight: 600;
  color: var(--navy);
  margin-bottom: 8px;
}
.bc-system-card-title {
  font-size: 17px;
  font-weight: 650;
  line-height: 1.35;
  color: var(--ink);
  min-height: 48px;
  margin-bottom: 10px;
}
.bc-system-card-body {
  font-size: 14px;
  line-height: 1.6;
  color: var(--muted);
}

.bc-metrics-four {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.bc-release-copy {
  min-height: 154px;
}
.bc-release-title {
  font-size: 17px;
  line-height: 1.35;
  font-weight: 650;
  color: var(--ink);
  min-height: 46px;
  margin-bottom: 10px;
}
.bc-release-desc {
  font-size: 14px;
  line-height: 1.6;
  color: var(--muted);
}

/* Account header */
.bc-head { display: grid; grid-template-columns: 2fr 1fr 1fr 1fr; gap: 0; background: var(--surface);
  border: 1px solid var(--line); border-radius: var(--radius); margin-bottom: 16px; }
.bc-head > div { padding: 14px 18px; }
.bc-head > div + div { border-left: 1px solid var(--line); }
.bc-head-name { font-size: 21px; font-weight: 680; color: var(--ink); }
.bc-head-value { font-size: 15px; font-weight: 600; color: var(--navy); }

/* Badges */
.bc-badge { display: inline-block; font-size: 11.5px; font-weight: 650; letter-spacing: 0.03em; padding: 2px 9px;
  border-radius: 999px; border: 1px solid var(--line-strong); color: var(--ink-2); background: var(--surface);
  white-space: nowrap; vertical-align: middle; }
.bc-badge.ok { background: var(--accent-soft); border-color: #BFDDD5; color: var(--accent); }
.bc-badge.warn { background: var(--warn-soft); border-color: #EBD3A0; color: var(--warn); }
.bc-badge.stop { background: var(--stop-soft); border-color: #E8BDBD; color: var(--stop); }
.bc-badge.navy { background: var(--navy); border-color: var(--navy); color: #FFFFFF; }
.bc-badge.syn { background: #F2F2EE; color: var(--muted); }

/* Workflow strip */
.bc-flow { display: flex; align-items: center; gap: 0; margin: 2px 0 14px 0; flex-wrap: wrap; }
.bc-step { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; letter-spacing: 0.06em;
  color: var(--muted); white-space: nowrap; }
.bc-dot { width: 22px; height: 22px; border-radius: 50%; border: 1px solid var(--line-strong); display: inline-flex;
  align-items: center; justify-content: center; font-size: 11.5px; font-weight: 650; background: var(--surface); }
.bc-step.current { color: var(--ink); }
.bc-step.current .bc-dot { border: 1.5px solid var(--accent); color: var(--accent); }
.bc-step.done { color: var(--ink-2); }
.bc-step.done .bc-dot { background: var(--navy); border-color: var(--navy); color: #FFFFFF; }
/* Checkmark drawn in CSS: Streamlit's HTML sanitizer strips inline SVG. */
.bc-step.done .bc-dot::after { content: ""; width: 4px; height: 8px; border: solid #FFFFFF; border-width: 0 2px 2px 0;
  transform: translateY(-1px) rotate(45deg); }
.bc-line { flex: 0 0 34px; height: 1px; background: var(--line-strong); margin: 0 10px; }
.bc-line.done { background: var(--navy); }

/* Notices */
.bc-notice { border-radius: var(--radius); padding: 12px 16px; margin: 6px 0 12px 0; border: 1px solid var(--line);
  background: var(--surface); }
.bc-notice-title { font-size: 15px; font-weight: 650; color: var(--ink); }
.bc-notice-text { font-size: 13.5px; color: var(--ink-2); margin-top: 2px; line-height: 1.5; }
.bc-notice.ok { background: var(--accent-soft); border-color: #BFDDD5; }
.bc-notice.ok .bc-notice-title { color: var(--accent); }
.bc-notice.warn { background: var(--warn-soft); border-color: #EBD3A0; }
.bc-notice.warn .bc-notice-title { color: var(--warn); }
.bc-notice.stop { background: var(--stop-soft); border-color: #E8BDBD; }
.bc-notice.stop .bc-notice-title { color: var(--stop); }
.bc-boundary-line { font-size: 12.5px; color: var(--muted); margin: 4px 0 10px 0; }

/* Timeline */
.bc-tl { border-left: 1px solid var(--line-strong); margin: 6px 0 0 8px; padding-left: 18px; }
.bc-tl-item { position: relative; padding: 0 0 14px 0; }
.bc-tl-item::before { content: ""; position: absolute; left: -23px; top: 6px; width: 9px; height: 9px; border-radius: 50%;
  background: var(--surface); border: 1.5px solid var(--navy); }
.bc-tl-when { font-size: 12px; color: var(--muted); }
.bc-tl-title { font-size: 14.5px; font-weight: 600; color: var(--ink); }
.bc-tl-detail { font-size: 13px; color: var(--ink-2); }
.bc-prov { font-size: 11px; font-weight: 650; color: var(--muted); border: 1px solid var(--line); border-radius: 5px;
  padding: 0 6px; margin-left: 6px; background: var(--bg); }


/* CRM account summary */
.bc-account-metrics {
  display: grid;
  grid-template-columns: 1.5fr 1fr .7fr .7fr;
  gap: 12px;
  margin: 4px 0 18px 0;
}
.bc-account-metric {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 13px 16px;
  min-width: 0;
}
.bc-account-metric-label {
  font-size: 11.5px;
  font-weight: 650;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 5px;
}
.bc-account-metric-value {
  font-size: 18px;
  line-height: 1.25;
  font-weight: 650;
  color: var(--navy);
  overflow-wrap: anywhere;
}

/* Prepared outputs */
.bc-output { font-size: 16px; line-height: 1.6; color: var(--ink); max-width: 780px; }
.bc-output p { margin: 0 0 10px 0; }
.bc-seg-refused { background: var(--stop-soft); border-bottom: 1px solid #E8BDBD; }
.bc-questions { margin: 0; padding: 0; list-style: none; counter-reset: q; }
.bc-questions li { counter-increment: q; display: flex; gap: 14px; padding: 9px 0; border-bottom: 1px solid var(--line);
  font-size: 15.5px; }
.bc-questions li:last-child { border-bottom: 0; }
.bc-questions li::before { content: counter(q); flex: 0 0 20px; font-size: 13px; font-weight: 650; color: var(--accent);
  padding-top: 2px; }
.bc-final { margin-top: 18px; background: var(--navy); color: #FFFFFF; border-radius: var(--radius); padding: 16px 22px;
  text-align: center; }
.bc-final-main { font-size: 17px; font-weight: 600; }
.bc-final-sub { font-size: 12.5px; color: #C9D1DC; margin-top: 4px; }

/* Widgets */
.stButton button { border-radius: 8px; font-weight: 600; box-shadow: none; }
.stButton button[kind="primary"], [data-testid="stBaseButton-primary"],
[data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"] {
  background: var(--navy); border: 1px solid var(--navy); color: #FFFFFF; }
.stButton button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover { background: #24375A; border-color: #24375A; }
[data-testid="stExpander"] details { border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); }
[data-testid="stExpander"] summary p { font-size: 13.5px; color: var(--ink-2); }
[data-testid="stForm"] { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); }
[data-testid="stVerticalBlockBorderWrapper"] { background: var(--surface); }

@media (max-width: 860px) {
  .bc-metrics { grid-template-columns: repeat(2, 1fr); }
  .bc-metrics-four { grid-template-columns: repeat(2, 1fr); }
  .bc-system-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .bc-system-card { min-height: 0; }
  .bc-release-copy { min-height: 0; }
  .bc-head { grid-template-columns: 1fr; }
  .bc-head > div + div { border-left: 0; border-top: 1px solid var(--line); }
  .bc-kv { grid-template-columns: 1fr; }
}
</style>
"""
