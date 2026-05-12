"""Investment Scorecard — Streamlit web app."""

import csv
import io
import json

import anthropic
import streamlit as st

# ── Criteria config ───────────────────────────────────────────────────────────
CRITERIA = ["tam_fit", "pe_activity", "growth_profile", "market_position"]
CRITERIA_LABELS = {
    "tam_fit":         "TAM Fit",
    "pe_activity":     "PE Activity",
    "growth_profile":  "Growth Profile",
    "market_position": "Market Position",
}
SCORE_LABEL = {"green": "Strong Fit", "yellow": "Moderate Fit", "red": "Weak Fit"}

# ── System prompt (cached across all company evaluations) ─────────────────────
SYSTEM_PROMPT = """\
You are a senior private equity analyst evaluating companies for investment.
Your assessments are sharp, specific, and grounded in publicly known information.

Evaluate each company across four criteria:

TAM FIT — Does the company operate in a large, growing, addressable market that
aligns with the thesis? Consider market size, growth trajectory, and defensibility.

PE ACTIVITY — What is the level of private equity interest and activity in this
company or sector? Consider existing PE ownership, recent deal flow, comparable
transactions, and sponsor appetite.

GROWTH PROFILE — How compelling are the company's growth characteristics?
Consider revenue growth rate, unit economics, scalability, and expansion vectors.

MARKET POSITION — How strong is the competitive moat? Consider market share,
brand strength, switching costs, pricing power, and differentiation.

Scoring:
- "green":  Strong fit   — compelling evidence clearly supporting the thesis
- "yellow": Moderate fit — partial alignment with notable gaps or uncertainties
- "red":    Weak fit     — significant concerns or clear misalignment with thesis

Return ONLY valid JSON with this exact structure (no markdown, no preamble):
{
  "company": "<company name>",
  "criteria": {
    "tam_fit":         {"score": "green|yellow|red", "rationale": "2-3 sentences."},
    "pe_activity":     {"score": "green|yellow|red", "rationale": "2-3 sentences."},
    "growth_profile":  {"score": "green|yellow|red", "rationale": "2-3 sentences."},
    "market_position": {"score": "green|yellow|red", "rationale": "2-3 sentences."}
  },
  "recommendation": "GO|NO-GO",
  "summary": "3-4 sentence overall investment case summary."
}\
"""

# ── CSS ───────────────────────────────────────────────────────────────────────
# Only data-testid selectors are safe here; class-based rules on injected HTML
# are stripped by Streamlit's sanitizer — all element styling uses inline styles.
PAGE_CSS = """
<style>
[data-testid="stAppViewContainer"] { background: #f8f9fb; }
[data-testid="stHeader"]           { background: transparent; }
[data-testid="stSidebar"]          { background: #ffffff; border-right: 1px solid #e5e7eb; }
</style>
"""

# ── Inline style constants ────────────────────────────────────────────────────
_BADGE_BASE = (
    "display:inline-block;padding:3px 12px;border-radius:999px;"
    "font-size:0.78rem;font-weight:600;letter-spacing:0.02em;"
)
_BADGE_STYLES = {
    "green":  _BADGE_BASE + "background:#dcfce7;color:#15803d;",
    "yellow": _BADGE_BASE + "background:#fef9c3;color:#854d0e;",
    "red":    _BADGE_BASE + "background:#fee2e2;color:#991b1b;",
}
_REC_BASE = (
    "display:inline-block;padding:5px 18px;border-radius:999px;"
    "font-size:1rem;font-weight:700;letter-spacing:0.05em;"
)
_REC_STYLES = {
    "GO":    _REC_BASE + "background:#dcfce7;color:#15803d;border:1.5px solid #86efac;",
    "NO-GO": _REC_BASE + "background:#fee2e2;color:#991b1b;border:1.5px solid #fca5a5;",
}

# ── HTML helpers ──────────────────────────────────────────────────────────────
def badge(score: str) -> str:
    return f'<span style="{_BADGE_STYLES[score]}">{SCORE_LABEL[score]}</span>'


def rec_chip(rec: str) -> str:
    return f'<span style="{_REC_STYLES[rec]}">{rec}</span>'


def company_card_html(result: dict) -> str:
    company  = result["company"]
    criteria = result["criteria"]
    rec      = result["recommendation"]
    summary  = result["summary"]

    rows = ""
    for i, key in enumerate(CRITERIA):
        label     = CRITERIA_LABELS[key]
        score     = criteria[key]["score"]
        rationale = criteria[key]["rationale"]
        border    = "" if i == len(CRITERIA) - 1 else "border-bottom:1px solid #f3f4f6;"
        rows += (
            f'<div style="display:flex;align-items:flex-start;gap:12px;'
            f'padding:10px 0;{border}">'
            f'<div style="font-weight:600;font-size:0.82rem;color:#374151;'
            f'width:130px;flex-shrink:0;padding-top:3px;">{label}</div>'
            f'<div style="padding-top:2px;">{badge(score)}</div>'
            f'<div style="font-size:0.85rem;color:#6b7280;line-height:1.55;flex:1;">'
            f'{rationale}</div>'
            f'</div>'
        )

    section_head = (
        "font-size:0.7rem;font-weight:700;letter-spacing:0.1em;"
        "text-transform:uppercase;color:#9ca3af;margin-bottom:6px;"
    )
    return (
        f'<div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;'
        f'padding:24px 28px;margin-bottom:20px;box-shadow:0 1px 4px rgba(0,0,0,0.05);">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">'
        f'<div style="font-size:1.15rem;font-weight:700;color:#111827;">{company}</div>'
        f'{rec_chip(rec)}'
        f'</div>'
        f'<div style="{section_head}">Criteria Scores</div>'
        f'{rows}'
        f'<div style="margin-top:16px;">'
        f'<div style="{section_head}">Investment Summary</div>'
        f'<div style="font-size:0.88rem;color:#374151;line-height:1.65;">{summary}</div>'
        f'</div>'
        f'</div>'
    )


def summary_table_html(results: list) -> str:
    th = (
        "text-align:left;padding:10px 14px;background:#f3f4f6;"
        "color:#374151;font-weight:600;border-bottom:2px solid #e5e7eb;"
    )
    td = "padding:10px 14px;border-bottom:1px solid #f3f4f6;color:#111827;vertical-align:middle;"

    header = (
        f'<tr>'
        f'<th style="{th}">Company</th>'
        f'<th style="{th}">TAM Fit</th>'
        f'<th style="{th}">PE Activity</th>'
        f'<th style="{th}">Growth Profile</th>'
        f'<th style="{th}">Market Position</th>'
        f'<th style="{th}">Recommendation</th>'
        f'</tr>'
    )
    rows = ""
    for r in results:
        c = r["criteria"]
        rows += (
            f'<tr>'
            f'<td style="{td}"><strong>{r["company"]}</strong></td>'
            f'<td style="{td}">{badge(c["tam_fit"]["score"])}</td>'
            f'<td style="{td}">{badge(c["pe_activity"]["score"])}</td>'
            f'<td style="{td}">{badge(c["growth_profile"]["score"])}</td>'
            f'<td style="{td}">{badge(c["market_position"]["score"])}</td>'
            f'<td style="{td}">{rec_chip(r["recommendation"])}</td>'
            f'</tr>'
        )
    return (
        f'<table style="width:100%;border-collapse:collapse;font-size:0.88rem;margin-top:8px;">'
        f'<thead>{header}</thead><tbody>{rows}</tbody></table>'
    )


# ── Claude evaluation ─────────────────────────────────────────────────────────
def evaluate_company(
    client: anthropic.Anthropic,
    company: str,
    thesis: str,
    cached_system: list,
) -> dict:
    user_msg = (
        f"Industry Thesis:\n{thesis}\n\n"
        f"Company to evaluate: {company}\n\n"
        "Analyze this company against the investment thesis and return JSON."
    )
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        system=cached_system,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0].strip()
    return json.loads(text)


# ── CSV builder ───────────────────────────────────────────────────────────────
def build_csv_bytes(results: list) -> bytes:
    fieldnames = [
        "Company", "TAM Fit", "PE Activity",
        "Growth Profile", "Market Position", "Recommendation", "Summary",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in results:
        c = r["criteria"]
        writer.writerow({
            "Company":         r["company"],
            "TAM Fit":         SCORE_LABEL[c["tam_fit"]["score"]],
            "PE Activity":     SCORE_LABEL[c["pe_activity"]["score"]],
            "Growth Profile":  SCORE_LABEL[c["growth_profile"]["score"]],
            "Market Position": SCORE_LABEL[c["market_position"]["score"]],
            "Recommendation":  r["recommendation"],
            "Summary":         r["summary"],
        })
    return buf.getvalue().encode("utf-8")


# ── App ───────────────────────────────────────────────────────────────────────
def main() -> None:
    st.set_page_config(
        page_title="Investment Scorecard",
        page_icon="■",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(PAGE_CSS, unsafe_allow_html=True)

    # ── Sidebar: inputs ───────────────────────────────────────────────────────
    api_key = st.secrets["ANTHROPIC_API_KEY"]

    with st.sidebar:
        st.markdown("### ■ Investment Scorecard")
        st.caption("Evaluate companies against your investment thesis using AI analysis.")
        st.divider()

        st.markdown("**Industry Thesis**")
        thesis = st.text_area(
            "thesis",
            label_visibility="collapsed",
            placeholder=(
                "Describe the sector opportunity and key investment criteria.\n\n"
                "Example: We target vertical SaaS companies serving SMBs in "
                "fragmented industries with $50B+ TAM, recurring revenue models, "
                "and strong net retention above 110%."
            ),
            height=180,
        )

        st.markdown("**Companies to Evaluate**")
        companies_raw = st.text_area(
            "companies",
            label_visibility="collapsed",
            placeholder="One company per line:\nServiceTitan\nJobber\nHouseCall Pro",
            height=140,
        )

        run = st.button("Run Analysis", type="primary", use_container_width=True)
        st.divider()
        st.caption("Powered by Claude claude-sonnet-4-6 · Prompt caching enabled")

    # ── Main area ─────────────────────────────────────────────────────────────
    if "results" not in st.session_state:
        st.session_state.results = []

    if run:
        # Validate inputs
        errors = []
        if not thesis.strip():
            errors.append("Industry thesis cannot be empty.")
        companies = [c.strip() for c in companies_raw.splitlines() if c.strip()]
        if not companies:
            errors.append("Enter at least one company name.")

        if errors:
            for e in errors:
                st.error(e)
        else:
            client = anthropic.Anthropic(api_key=api_key)
            cached_system = [
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

            results: list[dict] = []
            progress = st.progress(0, text="Starting analysis…")
            status   = st.empty()

            for i, company in enumerate(companies):
                status.markdown(
                    f"Analyzing **{company}** ({i + 1} of {len(companies)})…"
                )
                try:
                    result = evaluate_company(client, company, thesis, cached_system)
                    result.setdefault("company", company)
                    results.append(result)
                except json.JSONDecodeError:
                    st.warning(f"Could not parse response for **{company}** — skipped.")
                except anthropic.APIError as e:
                    st.error(f"API error for **{company}**: {e}")
                except Exception as e:  # noqa: BLE001
                    st.error(f"Unexpected error for **{company}**: {e}")

                progress.progress((i + 1) / len(companies))

            status.empty()
            progress.empty()
            st.session_state.results = results

    results = st.session_state.results

    if not results:
        # Empty state
        st.markdown(
            "<div style='text-align:center; padding: 80px 0; color: #9ca3af;'>"
            "<div style='font-size:2.5rem; margin-bottom:12px;'>■</div>"
            "<div style='font-size:1.1rem; font-weight:600; color:#374151;'>"
            "Investment Scorecard</div>"
            "<div style='margin-top:8px; font-size:0.9rem;'>"
            "Enter your thesis and companies in the sidebar, then click "
            "<strong>Run Analysis</strong>.</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        return

    # ── Results header ────────────────────────────────────────────────────────
    go_count   = sum(1 for r in results if r["recommendation"] == "GO")
    nogo_count = len(results) - go_count

    col_title, col_tally, col_dl = st.columns([3, 2, 1.2])
    with col_title:
        st.markdown(
            f"<div style='font-size:1.4rem;font-weight:700;color:#111827;"
            f"padding-top:6px;'>Analysis Results</div>",
            unsafe_allow_html=True,
        )
    with col_tally:
        st.markdown(
            f"<div style='padding-top:8px;'>"
            f"<span style='background:#dcfce7;color:#15803d;padding:4px 16px;"
            f"border-radius:999px;font-weight:700;display:inline-block;"
            f"margin-right:8px;'>{go_count} GO</span>"
            f"<span style='background:#fee2e2;color:#991b1b;padding:4px 16px;"
            f"border-radius:999px;font-weight:700;display:inline-block;'>"
            f"{nogo_count} NO-GO</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with col_dl:
        st.download_button(
            label="⬇ Download CSV",
            data=build_csv_bytes(results),
            file_name="scorecard_output.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Company cards ─────────────────────────────────────────────────────────
    tab_cards, tab_table = st.tabs(["Scorecards", "Summary Table"])

    with tab_cards:
        for result in results:
            st.markdown(company_card_html(result), unsafe_allow_html=True)

    with tab_table:
        st.markdown(
            "<div style='margin-top:8px;'>"
            + summary_table_html(results)
            + "</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<div style='margin-top:14px; font-size:0.8rem; color:#9ca3af;'>"
            "● Strong Fit &nbsp;|&nbsp; ◐ Moderate Fit &nbsp;|&nbsp; ○ Weak Fit"
            "</div>",
            unsafe_allow_html=True,
        )


if __name__ == "__main__":
    main()
