"""WageCheck web app: enter your shifts and gross pay, see what the award says you were owed."""

from __future__ import annotations

import sys
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))  # run from a plain checkout, no install needed

from wagecheck.check import (  # noqa: E402
    AWARDS,
    SUPPORTED_FROM,
    SUPPORTED_TO,
    ShiftInput,
    check_period,
    classification_label,
)
from wagecheck.rules.loader import load_pack  # noqa: E402

st.set_page_config(page_title="WageCheck", page_icon="💵")
st.title("WageCheck")
st.caption("Were you paid the award minimum? Enter your shifts and your gross pay for one pay period. "
           "Adults, NSW, 4 awards, shifts from 1 July 2026 to 30 June 2027.")
st.warning("Not legal advice. This is a portfolio project and can be wrong. "
           "For a real dispute, contact the Fair Work Ombudsman on 13 13 94.", icon="⚠️")

award = st.selectbox("Award", list(AWARDS), format_func=lambda a: f"{AWARDS[a]} [{a}]")
pack = load_pack(award, on=SUPPORTED_FROM)
classification = st.selectbox(
    "Classification (your level)", list(pack.classifications),
    format_func=lambda k: f"{classification_label(k)} (${pack.classifications[k].hourly}/hr base)")
employment = st.radio("Employment type", ["casual", "part_time", "full_time"], horizontal=True,
                      format_func=lambda e: e.replace("_", "-").capitalize())
shiftworker = extended = False
if award == "MA000004":
    shiftworker = st.checkbox("I was hired specifically to work night shifts (e.g. night fill)",
                              help="Retail award cl 23 shiftwork rates. Tick only if your contract says so.")
    extended = st.checkbox("My store trades past 9pm on weekdays or 6pm on weekends")
st.checkbox("I am 21 or older", value=True, key="adult",
            help="Junior rates are not modelled, so results for under-21s would be wrong.")

st.subheader("Your shifts")
st.caption("If the finish is earlier than the start, the shift ran past midnight. "
           "Unpaid break minutes are placed in the middle of the shift.")
default = pd.DataFrame([
    {"date": date(2026, 10, 20), "start": time(9, 0), "finish": time(17, 0), "unpaid break (min)": 30},
    {"date": date(2026, 10, 24), "start": time(9, 0), "finish": time(17, 0), "unpaid break (min)": 30},
])
shifts_df = st.data_editor(
    default, num_rows="dynamic", width="stretch",
    column_config={
        "date": st.column_config.DateColumn("date", min_value=SUPPORTED_FROM, max_value=SUPPORTED_TO,
                                            format="DD/MM/YYYY", required=True),
        "start": st.column_config.TimeColumn("start", format="HH:mm", step=60, required=True),
        "finish": st.column_config.TimeColumn("finish", format="HH:mm", step=60, required=True),
        "unpaid break (min)": st.column_config.NumberColumn("unpaid break (min)", min_value=0, max_value=240,
                                                            step=1, default=0),
    })
paid = st.number_input("Gross pay for these shifts ($, before tax, excluding super and allowances)",
                       min_value=0.0, value=500.0, step=1.0, format="%.2f")

if st.button("Check my pay", type="primary"):
    if not st.session_state.adult:
        st.error("WageCheck can't check junior rates yet.")
        st.stop()
    rows = shifts_df.dropna(subset=["date", "start", "finish"])
    try:
        shifts = [ShiftInput(pd.Timestamp(r["date"]).date(), r["start"], r["finish"],
                             int(r["unpaid break (min)"] or 0)) for _, r in rows.iterrows()]
        result = check_period(award, classification, employment, shifts, Decimal(str(paid)),
                              shiftworker=shiftworker, extended_trading=extended)
    except ValueError as e:
        st.error(str(e))
        st.stop()

    c1, c2, c3 = st.columns(3)
    c1.metric("Award minimum", f"${result.owed:,.2f}")
    c2.metric("You were paid", f"${result.paid:,.2f}")
    c3.metric("Difference", f"${abs(result.difference):,.2f}",
              "short" if result.verdict == "underpaid" else "over", delta_color="inverse")
    if result.verdict == "underpaid":
        st.error(f"You may have been underpaid by **${result.difference:,.2f}** for these shifts. Check the "
                 "breakdown, then raise it with your employer or the Fair Work Ombudsman (fairwork.gov.au).")
    elif result.verdict == "paid_above":
        st.success("You were paid at least the award minimum for these shifts. "
                   "(Many employers pay above the award, or under an enterprise agreement.)")
    else:
        st.success("Your pay matches the award minimum for these shifts (within $1).")

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
            "Enterprise agreements (e.g. Coles, Woolworths): if one covers you, these award rates may not apply.",
            "Allowances, superannuation, tax and leave.",
            "States other than NSW (public holidays differ).",
        ]))

st.divider()
st.caption("WageCheck · deterministic rules engine, rates checked against the Fair Work Commission's Modern Awards "
           "Pay Database. Nothing you enter is stored. Source: github.com/santhosh5113/wagecheck")
