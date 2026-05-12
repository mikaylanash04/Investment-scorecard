#!/usr/bin/env python3
"""PE Investment Scorecard Agent — evaluates companies against a PE investment thesis."""

import csv
import json
import os
import sys
import textwrap

import anthropic

# ── ANSI colors ───────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"
CYAN   = "\033[96m"
WHITE  = "\033[97m"

# ── Criteria config ───────────────────────────────────────────────────────────
CRITERIA = ["tam_fit", "pe_activity", "growth_profile", "market_position"]
CRITERIA_LABELS = {
    "tam_fit":         "TAM Fit",
    "pe_activity":     "PE Activity",
    "growth_profile":  "Growth Profile",
    "market_position": "Market Position",
}
SCORE_COLOR  = {"green": GREEN,  "yellow": YELLOW, "red": RED}
SCORE_SYMBOL = {"green": "●",    "yellow": "◐",    "red": "○"}
SCORE_LABEL  = {"green": "STRONG FIT", "yellow": "MODERATE FIT", "red": "WEAK FIT"}

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


# ── Formatting helpers ────────────────────────────────────────────────────────
W = 68  # total display width


def wrap(text: str, width: int = 62, indent: str = "    ") -> str:
    lines = textwrap.wrap(text, width=width)
    return "\n".join(indent + line for line in lines)


def rule(char: str = "─") -> str:
    return char * W


def print_header() -> None:
    print(f"\n{BOLD}{CYAN}{'═' * W}{RESET}")
    print(f"{BOLD}{CYAN}  ■  PE INVESTMENT SCORECARD AGENT{RESET}")
    print(f"{BOLD}{CYAN}{'═' * W}{RESET}\n")


def print_scorecard(result: dict, num: int, total: int) -> None:
    company  = result["company"]
    criteria = result["criteria"]
    rec      = result["recommendation"]
    summary  = result["summary"]
    rec_color = GREEN if rec == "GO" else RED

    print(f"\n{BOLD}{rule()}{RESET}")
    print(f"{BOLD}  [{num}/{total}] {company.upper()}{RESET}")
    print(f"{BOLD}{rule()}{RESET}")

    print(f"\n  {BOLD}CRITERIA SCORES{RESET}\n")

    for key in CRITERIA:
        label     = CRITERIA_LABELS[key]
        c         = criteria[key]
        score     = c["score"]
        color     = SCORE_COLOR[score]
        symbol    = SCORE_SYMBOL[score]
        slabel    = SCORE_LABEL[score]
        rationale = c["rationale"]

        print(f"  {color}{BOLD}{symbol}  {label:<20}{RESET}  {color}{slabel}{RESET}")
        print(f"{DIM}{wrap(rationale, width=62, indent='     ')}{RESET}")
        print()

    print(f"  {BOLD}INVESTMENT SUMMARY{RESET}")
    print(wrap(summary, width=64, indent="  "))
    print()
    print(f"  {BOLD}RECOMMENDATION  {rec_color}{BOLD}▶  {rec}{RESET}")
    print(f"\n{rule()}")


def print_summary_table(results: list) -> None:
    print(f"\n\n{BOLD}{CYAN}{'═' * W}{RESET}")
    print(f"{BOLD}{CYAN}  PORTFOLIO SCORECARD SUMMARY{RESET}")
    print(f"{BOLD}{CYAN}{'═' * W}{RESET}\n")

    print(f"  {BOLD}{'Company':<24}  TAM    PE   Growth  Market   Result{RESET}")
    print(f"  {'─' * 24}  {'─' * 5}  {'─' * 4}  {'─' * 6}  {'─' * 6}   {'─' * 6}")

    for r in results:
        name      = r["company"][:22]
        c         = r["criteria"]
        rec       = r["recommendation"]
        rec_color = GREEN if rec == "GO" else RED

        def col(key: str) -> str:
            s = c[key]["score"]
            return f"{SCORE_COLOR[s]}{SCORE_SYMBOL[s]}{RESET}"

        tam    = col("tam_fit")
        pe     = col("pe_activity")
        growth = col("growth_profile")
        market = col("market_position")

        # Extra padding accounts for invisible ANSI bytes
        print(
            f"  {name:<24}  "
            f"  {tam}     "
            f"{pe}    "
            f"{growth}      "
            f"{market}   "
            f"  {rec_color}{BOLD}{rec}{RESET}"
        )

    go_count   = sum(1 for r in results if r["recommendation"] == "GO")
    nogo_count = len(results) - go_count

    print(f"\n  {DIM}● Strong fit  ◐ Moderate fit  ○ Weak fit{RESET}")
    print(
        f"\n  {BOLD}Tally:{RESET}  "
        f"{GREEN}{BOLD}{go_count} GO{RESET}   {RED}{BOLD}{nogo_count} NO-GO{RESET}"
    )
    print(f"\n{BOLD}{CYAN}{'═' * W}{RESET}\n")


# ── CSV export ───────────────────────────────────────────────────────────────
SCORE_TEXT = {"green": "Strong Fit", "yellow": "Moderate Fit", "red": "Weak Fit"}
CSV_PATH   = "scorecard_output.csv"


def export_to_csv(results: list) -> None:
    fieldnames = [
        "Company",
        "TAM Fit",
        "PE Activity",
        "Growth Profile",
        "Market Position",
        "Recommendation",
        "Summary",
    ]
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            c = r["criteria"]
            writer.writerow({
                "Company":         r["company"],
                "TAM Fit":         SCORE_TEXT[c["tam_fit"]["score"]],
                "PE Activity":     SCORE_TEXT[c["pe_activity"]["score"]],
                "Growth Profile":  SCORE_TEXT[c["growth_profile"]["score"]],
                "Market Position": SCORE_TEXT[c["market_position"]["score"]],
                "Recommendation":  r["recommendation"],
                "Summary":         r["summary"],
            })
    print(f"\n  {GREEN}{BOLD}Exported →{RESET}  {CSV_PATH}")


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

    # Strip markdown code fences if Claude wraps the JSON
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0].strip()

    return json.loads(text)


# ── Input helpers ─────────────────────────────────────────────────────────────
def prompt_multiline(label: str, hint: str) -> str:
    print(f"{BOLD}{label}{RESET}")
    print(f"{DIM}{hint}{RESET}")
    print(f"{DIM}Press Enter twice to continue.{RESET}\n")
    lines: list[str] = []
    while True:
        try:
            line = input(f"{DIM}  {RESET}")
        except EOFError:
            break
        if line == "":
            if lines:
                break
        else:
            lines.append(line)
    return " ".join(lines).strip()


def prompt_list(label: str, hint: str) -> list[str]:
    print(f"\n{BOLD}{label}{RESET}")
    print(f"{DIM}{hint}{RESET}")
    print(f"{DIM}Press Enter twice when done.{RESET}\n")
    items: list[str] = []
    while True:
        try:
            line = input(f"{DIM}  {RESET}").strip()
        except EOFError:
            break
        if line == "":
            if items:
                break
        else:
            items.append(line)
    return items


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(f"{RED}Error: ANTHROPIC_API_KEY environment variable not set.{RESET}")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print_header()

    thesis = prompt_multiline(
        "Industry Thesis",
        "Describe the sector opportunity and key investment criteria.",
    )
    if not thesis:
        print(f"{RED}Error: Thesis cannot be empty.{RESET}")
        sys.exit(1)

    companies = prompt_list(
        "Companies to Evaluate",
        "Enter one company name per line.",
    )
    if not companies:
        print(f"{RED}Error: Provide at least one company name.{RESET}")
        sys.exit(1)

    # System prompt cached with ephemeral cache_control — reused for every
    # company evaluation in this session, saving tokens on repeated calls.
    cached_system = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    n = len(companies)
    noun = "company" if n == 1 else "companies"
    print(f"\n{BOLD}{CYAN}Evaluating {n} {noun} against your thesis...{RESET}\n")

    results: list[dict] = []

    for i, company in enumerate(companies, 1):
        print(f"  {DIM}[{i}/{n}] Analyzing {company}...{RESET}", end="", flush=True)
        try:
            result = evaluate_company(client, company, thesis, cached_system)
            result.setdefault("company", company)
            results.append(result)
            rec   = result.get("recommendation", "?")
            color = GREEN if rec == "GO" else RED
            print(f"  {color}{BOLD}{rec}{RESET}")
            print_scorecard(result, i, n)
        except json.JSONDecodeError:
            print(f"  {RED}parse error — skipping{RESET}")
        except anthropic.APIError as e:
            print(f"  {RED}API error: {e}{RESET}")
        except Exception as e:  # noqa: BLE001
            print(f"  {RED}error: {e}{RESET}")

    if results:
        print_summary_table(results)
        try:
            answer = input(f"  {BOLD}Export results to {CSV_PATH}? [y/N]{RESET}  ").strip().lower()
        except EOFError:
            answer = ""
        if answer in ("y", "yes"):
            export_to_csv(results)
        else:
            print(f"  {DIM}Export skipped.{RESET}\n")


if __name__ == "__main__":
    main()
