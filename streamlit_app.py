"""WageCheck web app: enter your shifts and gross pay, see what the award says you were owed."""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))  # run from a plain checkout, no install needed

from wagecheck.check import (  # noqa: E402
    AWARDS,
    SUPPORTED_FROM,
    SUPPORTED_TO,
    check_period,
    classification_label,
    rows_to_shifts,
)
from wagecheck.rules.loader import load_pack  # noqa: E402

st.set_page_config(page_title="WageCheck", page_icon="💵")
st.title("WageCheck")
st.caption("Were you paid the legal minimum? Enter your shifts and your gross pay for one pay period. "
           "Adults, NSW, 4 awards + the Coles agreement, shifts from 1 July 2026 to 30 June 2027.")
st.warning("Not legal advice. This is a portfolio project and can be wrong. "
           "For a real dispute, contact the Fair Work Ombudsman on 13 13 94.", icon="⚠️")

award = st.selectbox("Award or agreement", list(AWARDS), format_func=lambda a: f"{AWARDS[a]} [{a}]")
pack = load_pack(award, on=SUPPORTED_FROM)
classification = st.selectbox(
    "Classification (your level)", list(pack.classifications),
    format_func=lambda k: f"{classification_label(k)} (${pack.classifications[k].hourly}/hr base)")
employment = st.radio("Employment type", ["casual", "part_time", "full_time"], horizontal=True,
                      format_func=lambda e: e.replace("_", "-").capitalize())
shiftworker = extended = False
if pack.shiftwork is not None:
    shiftworker = st.checkbox(
        "My contract says I'm employed as a shiftworker (night shifts)",
        help=f"{pack.title}, cl {pack.shiftwork.definition_clause}: shiftwork rates apply only to people employed "
             "as shiftworkers, and their breaks are paid. If unsure, leave it unticked and read the warning below "
             "the result.")
if award == "MA000004":
    extended = st.checkbox("My store trades past 9pm on weekdays or 6pm on weekends")
if pack.instrument_type == "enterprise_agreement":
    st.info(f"{pack.title} applies instead of the award to the people it covers. If you're not employed as a "
            "shiftworker, work after 11pm is overtime: 150% for the first 3 hours, then 200% (cl 4.2.3, 4.9.2).")
st.checkbox("I am 21 or older", value=True, key="adult",
            help="Junior rates are not modelled, so results for under-21s would be wrong.")

st.subheader("Your shifts")
st.caption("Click a cell to edit it; add a shift in the empty row at the bottom. Type times like 22:00 or 10pm. "
           "If the finish is earlier than the start, the shift ran past midnight. "
           "Unpaid break minutes are placed in the middle of the shift.")
default = pd.DataFrame([
    {"date": date(2026, 10, 20), "start": "09:00", "finish": "17:00", "unpaid break (min)": 30},
    {"date": date(2026, 10, 24), "start": "09:00", "finish": "17:00", "unpaid break (min)": 30},
])
shifts_df = st.data_editor(
    default, num_rows="dynamic", width="stretch",
    column_config={
        "date": st.column_config.DateColumn("date", min_value=SUPPORTED_FROM, max_value=SUPPORTED_TO,
                                            format="DD/MM/YYYY", required=True),
        "start": st.column_config.TextColumn("start", help="Type it: 22:00, 2200 or 10pm", required=True),
        "finish": st.column_config.TextColumn("finish", help="Type it: 06:00, 0600 or 6am", required=True),
        "unpaid break (min)": st.column_config.NumberColumn("unpaid break (min)", min_value=0, max_value=240,
                                                            step=1, default=0),
    })
paid = st.number_input("Gross pay for these shifts ($, before tax, excluding super and allowances)",
                       min_value=0.0, value=500.0, step=1.0, format="%.2f")

if st.button("Check my pay", type="primary"):
    if not st.session_state.adult:
        st.error("WageCheck can't check junior rates yet.")
        st.stop()
    try:
        shifts = rows_to_shifts(shifts_df.to_dict("records"))
    except ValueError as e:
        st.error(str(e))
        st.stop()
    try:
        result = check_period(award, classification, employment, shifts, Decimal(str(paid)),
                              shiftworker=shiftworker, extended_trading=extended)
    except ValueError as e:
        st.error(str(e))
        st.stop()

    c1, c2, c3 = st.columns(3)
    kind = "agreement" if pack.instrument_type == "enterprise_agreement" else "award"
    c1.metric(f"{kind.capitalize()} minimum", f"${result.owed:,.2f}")
    c2.metric("You were paid", f"${result.paid:,.2f}")
    c3.metric("Difference", f"${abs(result.difference):,.2f}",
              "short" if result.verdict == "underpaid" else "over", delta_color="inverse")
    if result.verdict == "underpaid":
        st.error(f"You may have been underpaid by **${result.difference:,.2f}** for these shifts. Check the "
                 "breakdown, then raise it with your employer or the Fair Work Ombudsman (fairwork.gov.au).")
    elif result.verdict == "paid_above":
        st.success(f"You were paid at least the {kind} minimum for these shifts."
                   + (" (Many employers pay above the award, or under an enterprise agreement.)"
                      if kind == "award" else ""))
    else:
        st.success(f"Your pay matches the {kind} minimum for these shifts (within $1).")

    st.subheader("How it was worked out")
    lines = [{"shift": f"{r.shift.start:%a %d/%m %H:%M}", "band": li.band.replace("_", " "),
              "hours": float(li.hours), "rate": float(li.rate), "amount": float(li.amount),
              "clauses": ", ".join(li.clauses)}
             for r in result.results for li in r.lines]
    table = pd.DataFrame(lines)
    st.dataframe(table, width="stretch", hide_index=True,
                 column_config={"rate": st.column_config.NumberColumn(format="$%.2f"),
                                "amount": st.column_config.NumberColumn(format="$%.2f")})
    st.caption(f"{result.award_title}, {classification_label(classification)}: base ${result.hourly_base}/hr. "
               + (f"[Read the award]({result.award_url})" if result.award_url else ""))
    st.download_button("Download this breakdown (CSV)", table.to_csv(index=False), "wagecheck.csv", "text/csv")

    for w in result.warnings:
        if "overstate" in w:
            st.warning(w)
        else:
            st.info(w)
    with st.expander("What this check does NOT cover"):
        st.markdown("\n".join(f"- {g}" for g in [
            *result.known_gaps,
            "Enterprise agreements other than Coles (e.g. Woolworths): if one covers you, award rates may not "
            "apply." if result.award_id.startswith("MA") else
            "Whether this agreement covers your role and store: check its coverage clause.",
            "Allowances, superannuation, tax and leave.",
            "States other than NSW (public holidays differ).",
        ]))

st.divider()
st.caption("WageCheck · deterministic rules engine, rates checked against the Fair Work Commission's Modern Awards "
           "Pay Database. Nothing you enter is stored. Source: github.com/santhosh5113/wagecheck")
