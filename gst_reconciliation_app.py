import streamlit as st

# ------------------ PASSWORD PROTECTION ------------------
if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False

if not st.session_state['authenticated']:
    password = st.text_input("Enter password to access the app:", type="password")
    if password == "mayur123":  # <-- Replace with your password
        st.session_state['authenticated'] = True
        st.experimental_rerun() if hasattr(st, 'experimental_rerun') else None
    else:
        if password:  # Only show warning if user typed something
            st.warning("Incorrect password!")
        st.stop()  # Stops app from running until correct password

import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
import altair as alt

st.set_page_config(page_title="GST Reconciliation Tool", layout="wide")
st.title("📊 GST Reconciliation Tool (Invoice-level)")

# ------------------------ Download Blank Template ------------------------
template_columns = [
    "GSTIN/UIN OF RECIPIENT",
    "RECEIVER NAME",
    "INVOICE NO",
    "INVOICE DATE",
    "INVOICE VALUE",
    "PLACE OF SUPPLY",
    "INVOICE TYPE",
    "TAXABLE VALUE",
    "INTEGRATED TAX",
    "CENTRAL TAX",
    "STATE/UT TAX",
    "IRN NUMBER"
]

template_df = pd.DataFrame(columns=template_columns)
template_bytes = BytesIO()
with pd.ExcelWriter(template_bytes, engine="openpyxl") as writer:
    template_df.to_excel(writer, sheet_name="Template", index=False)
template_bytes.seek(0)

st.download_button(
    "📥 Download GST Template",
    template_bytes,
    "gst_template.xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# ------------------------ Helper Functions ------------------------
def read_file(file):
    if file.name.endswith("csv"):
        return pd.read_csv(file, dtype=str)
    else:
        return pd.read_excel(file, dtype=str)

def preprocess(df):
    df_clean = df.fillna("").astype(str)
    for col in df_clean.columns:
        df_clean[col] = df_clean[col].str.strip()
        df_clean[col] = df_clean[col].str.upper()
        df_clean[col] = df_clean[col].str.replace(r"\s+", " ", regex=True)

    if "INVOICE DATE" in df_clean.columns:
        def normalize_date(val):
            val = str(val).strip()
            if val == "" or pd.isna(val):
                return ""
            val = val.split(" ")[0]
            for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%Y/%m/%d"):
                try:
                    dt = pd.to_datetime(val, format=fmt, errors="raise", dayfirst=True)
                    return dt.strftime("%d-%m-%Y")
                except:
                    continue
            return val
        df_clean["INVOICE DATE"] = df_clean["INVOICE DATE"].apply(normalize_date)

    return df_clean

def clean_state(val):
    parts = str(val).upper().split("-")
    return parts[-1].strip() if len(parts) > 1 else str(val).upper().strip()

# ------------------------ Reconciliation Function ------------------------
def reconcile_rows(books_df, gst_df, key_cols, amount_cols, numeric_tolerance=1.0):
    books_df = preprocess(books_df)
    gst_df = preprocess(gst_df)

    for col in amount_cols:
        if col in books_df.columns:
            books_df[col] = pd.to_numeric(books_df[col], errors="coerce").fillna(0)
        if col in gst_df.columns:
            gst_df[col] = pd.to_numeric(gst_df[col], errors="coerce").fillna(0)

    merged = pd.merge(
        books_df, gst_df,
        on="INVOICE NO", how="outer",
        suffixes=("_BOOKS", "_GST"), indicator=True
    )

    full_result = []
    annexure = []

    numeric_cols = amount_cols
    all_fields = [c for c in template_columns if c in books_df.columns and c in gst_df.columns]
    strict_cols = [c for c in all_fields if c not in numeric_cols + ["INVOICE NO"]]

    for _, row in merged.iterrows():
        status = ""
        mismatch_cols = []

        if row["_merge"] == "left_only":
            status = "📄 Only in Books"
        elif row["_merge"] == "right_only":
            status = "📑 Only in GST"
        else:
            for col in strict_cols:
                col_b, col_g = f"{col}_BOOKS", f"{col}_GST"
                if col_b in row and col_g in row:
                    val_b, val_g = str(row[col_b]), str(row[col_g])

                    if col == "PLACE OF SUPPLY":
                        val_b, val_g = clean_state(val_b), clean_state(val_g)

                    if col == "INVOICE TYPE":
                        keywords = ["B2B", "REGULAR", "EXPORT"]
                        if any(kw in val_b for kw in keywords) and any(kw in val_g for kw in keywords):
                            continue

                    if val_b != val_g:
                        mismatch_cols.append(f"{col} Mismatch")
                        annexure.append({
                            "INVOICE NO": row["INVOICE NO"],
                            "INVOICE DATE": row.get("INVOICE DATE_BOOKS", row.get("INVOICE DATE_GST", "")),
                            "Column": col,
                            "Books Value": val_b,
                            "GST Value": val_g,
                            "Difference": ""
                        })

            for col in numeric_cols:
                col_b, col_g = f"{col}_BOOKS", f"{col}_GST"
                if col_b in row and col_g in row:
                    diff = float(row[col_b]) - float(row[col_g])
                    if abs(diff) > numeric_tolerance:
                        mismatch_cols.append(f"{col} (Diff: {diff:.2f})")
                        annexure.append({
                            "INVOICE NO": row["INVOICE NO"],
                            "INVOICE DATE": row.get("INVOICE DATE_BOOKS", row.get("INVOICE DATE_GST", "")),
                            "Column": col,
                            "Books Value": row[col_b],
                            "GST Value": row[col_g],
                            "Difference": diff
                        })

            status = "✅ Perfect Invoice Match" if not mismatch_cols else "⚠️ Mismatch"

        row_dict = {c: row[c] for c in row.index if not c.endswith("_merge")}
        row_dict["Status"] = status
        full_result.append(row_dict)

    return pd.DataFrame(full_result), pd.DataFrame(annexure)

# ------------------------ Excel Output ------------------------
def to_excel_bytes(full_reco, annexure, summary):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        full_reco.to_excel(writer, sheet_name="Full_Reco", index=False)
        if annexure is not None and not annexure.empty:
            annexure.to_excel(writer, sheet_name="Annexure", index=False)
        if summary is not None and not summary.empty:
            summary.to_excel(writer, sheet_name="Summary", index=False)
    output.seek(0)
    return output.getvalue()

# ------------------------ File Upload ------------------------
books_file = st.file_uploader("📂 Upload Books Data (Excel/CSV)", type=["csv", "xlsx"])
gst_file = st.file_uploader("📂 Upload GST Data (Excel/CSV)", type=["csv", "xlsx"])

if books_file and gst_file:
    try:
        books = read_file(books_file)
        gst = read_file(gst_file)

        books.columns = books.columns.str.strip().str.upper()
        gst.columns = gst.columns.str.strip().str.upper()

        col_mapping = {
            "INVOICE NUMBER": "INVOICE NO",
            "GSTIN": "GSTIN/UIN OF RECIPIENT",
        }
        books.rename(columns=col_mapping, inplace=True)
        gst.rename(columns=col_mapping, inplace=True)

        # ------------------------ Default Aggregate by Invoice ------------------------
        books_proc = preprocess(books)
        gst_proc = preprocess(gst)

        key_cols_agg = ["INVOICE NO", "GSTIN/UIN OF RECIPIENT", "RECEIVER NAME"]
        numeric_cols = ["INVOICE VALUE", "TAXABLE VALUE", "INTEGRATED TAX", "CENTRAL TAX", "STATE/UT TAX"]
        non_numeric_cols = ["INVOICE DATE", "PLACE OF SUPPLY", "INVOICE TYPE", "IRN NUMBER"]

        for col in numeric_cols:
            if col in books_proc.columns:
                books_proc[col] = pd.to_numeric(books_proc[col].str.replace(",", ""), errors="coerce").fillna(0)
            if col in gst_proc.columns:
                gst_proc[col] = pd.to_numeric(gst_proc[col].str.replace(",", ""), errors="coerce").fillna(0)

        books_proc = books_proc.groupby(key_cols_agg, as_index=False).agg(
            {**{col: "sum" for col in numeric_cols}, **{col: "first" for col in non_numeric_cols}}
        )
        gst_proc = gst_proc.groupby(key_cols_agg, as_index=False).agg(
            {**{col: "sum" for col in numeric_cols}, **{col: "first" for col in non_numeric_cols}}
        )

        # ------------------------ Input Data Summary ------------------------
        def display_invoice_summary(gst_df, books_df):
            st.subheader("📊 Input Data Summary")
            gst_invoices = gst_df["INVOICE NO"].nunique() if "INVOICE NO" in gst_df.columns else 0
            books_invoices = books_df["INVOICE NO"].nunique() if "INVOICE NO" in books_df.columns else 0

            gst_total = pd.to_numeric(gst_df["INVOICE VALUE"], errors="coerce").sum() if "INVOICE VALUE" in gst_df.columns else 0
            books_total = pd.to_numeric(books_df["INVOICE VALUE"], errors="coerce").sum() if "INVOICE VALUE" in books_df.columns else 0

            gst_total = int(round(gst_total))
            books_total = int(round(books_total))

            summary_df = pd.DataFrame({
                "Metric": ["Number of Invoices", "Total Invoice Value"],
                "GST Data": [gst_invoices, gst_total],
                "Books Data": [books_invoices, books_total]
            })

            st.table(summary_df)

        display_invoice_summary(gst_proc, books_proc)

        key_cols = ["INVOICE NO", "GSTIN/UIN OF RECIPIENT", "RECEIVER NAME"]
        amount_cols = ["INVOICE VALUE", "TAXABLE VALUE", "INTEGRATED TAX", "CENTRAL TAX", "STATE/UT TAX"]

        tolerance = st.number_input(
            "Set Numeric Tolerance for Amount Comparison (₹)",
            min_value=0.0,
            max_value=10000000.0,
            value=1.0,
            step=0.5,
            format="%.2f"
        )

        if st.button("Run Reconciliation"):
            st.info("🔎 Performing GST Reconciliation at Invoice Level...")
            full_reco, annexure = reconcile_rows(books_proc, gst_proc, key_cols, amount_cols, numeric_tolerance=tolerance)

            summary_counts = {
                "✅ Perfect Invoice Match": (full_reco["Status"] == "✅ Perfect Invoice Match").sum(),
                "⚠️ Mismatch": (full_reco["Status"] == "⚠️ Mismatch").sum(),
                "📄 Only in Books": (full_reco["Status"] == "📄 Only in Books").sum(),
                "📑 Only in GST": (full_reco["Status"] == "📑 Only in GST").sum()
            }
            summary_df = pd.DataFrame({
                "Result": list(summary_counts.keys()),
                "Count": list(summary_counts.values())
            })

            st.subheader("📌 Reconciliation Summary")
            st.dataframe(summary_df, use_container_width=True)

            # ------------------------ Chart Representation ------------------------
            st.subheader("📈 Reconciliation Summary Chart")
            chart_df = summary_df.copy()
            chart_df.rename(columns={"Result": "Status", "Count": "Number of Invoices"}, inplace=True)
            chart = alt.Chart(chart_df).mark_bar().encode(
                x=alt.X('Status', sort=None),
                y='Number of Invoices',
                color='Status',
                tooltip=['Status', 'Number of Invoices']
            ).properties(
                width=700,
                height=400
            )
            st.altair_chart(chart, use_container_width=True)

            st.subheader("📋 Full Reconciliation")
            st.dataframe(full_reco, use_container_width=True)

            if not annexure.empty:
                st.subheader("📑 Differences Annexure")
                st.dataframe(annexure, use_container_width=True)

            st.download_button(
                "📥 Download Full Reconciliation (CSV)",
                full_reco.to_csv(index=False).encode("utf-8"),
                "gst_reconciliation_full.csv",
                "text/csv"
            )
            if not annexure.empty:
                st.download_button(
                    "📥 Download Differences Annexure (CSV)",
                    annexure.to_csv(index=False).encode("utf-8"),
                    "gst_differences_annexure.csv",
                    "text/csv"
                )

            excel_bytes = to_excel_bytes(full_reco, annexure, summary_df)
            st.download_button(
                "📥 Download Excel (All Sheets)",
                excel_bytes,
                "gst_reconciliation.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    except Exception as e:
        st.error(f"⚠️ Error during reconciliation: {e}")
