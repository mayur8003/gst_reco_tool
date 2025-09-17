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

st.set_page_config(page_title="GST Reconciliation Tool", layout="wide")
st.title("📊 GST Reconciliation Tool (Invoice-level)")

# ------------------------
# Download Blank Template
# ------------------------
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

# ------------------------
# Helper Functions
# ------------------------
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
        def format_date_safe(val):
            if pd.isna(val) or val == "":
                return ""
            try:
                dt = pd.to_datetime(val, errors="coerce", dayfirst=True, infer_datetime_format=True)
                if pd.isna(dt):
                    return str(val)
                return dt.strftime("%d-%m-%Y")
            except:
                return str(val)
        df_clean["INVOICE DATE"] = df_clean["INVOICE DATE"].apply(format_date_safe)
    return df_clean

def aggregate_books(df, key_cols, amount_cols):
    agg_dict = {col: "sum" for col in amount_cols if col in df.columns}
    df_agg = df.groupby(key_cols, as_index=False).agg(agg_dict)
    return df_agg

def clean_state(val):
    parts = str(val).upper().split("-")
    return parts[-1].strip() if len(parts) > 1 else str(val).upper().strip()

def reconcile_rows(books_df, gst_df, key_cols, amount_cols, numeric_tolerance=1.0):
    books_df = preprocess(books_df)
    gst_df = preprocess(gst_df)

    for col in amount_cols:
        if col in books_df.columns:
            books_df[col] = pd.to_numeric(books_df[col], errors="coerce").fillna(0)
        if col in gst_df.columns:
            gst_df[col] = pd.to_numeric(gst_df[col], errors="coerce").fillna(0)

    books_agg = aggregate_books(books_df, key_cols, amount_cols)

    full_result = []
    annexure = []
    gst_matched_indices = set()

    for idx_b, b_row in books_df.iterrows():
        if "INVOICE NO" not in gst_df.columns:
            st.error("Column 'INVOICE NO' not found in GST data")
            return pd.DataFrame(), pd.DataFrame()

        condition = gst_df["INVOICE NO"] == b_row["INVOICE NO"]
        matched_gst = gst_df[condition]

        if matched_gst.empty:
            status = "📄 Only in Books"
            full_result.append({**b_row.to_dict(), "Status": status})
        else:
            gst_idx = matched_gst.index[0]
            gst_matched_indices.add(gst_idx)
            mismatch_cols = []

            strict_cols = [
                "GSTIN/UIN OF RECIPIENT",
                "RECEIVER NAME",
                "INVOICE DATE",
                "IRN NUMBER",
                "INVOICE TYPE",
                "PLACE OF SUPPLY"
            ]

            for col in strict_cols:
                if col in books_df.columns and col in gst_df.columns:
                    val_books = b_row[col]
                    val_gst = gst_df.at[gst_idx, col]

                    if col == "INVOICE TYPE":
                        keywords = ["B2B", "REGULAR"]
                        if any(kw in str(val_books).upper() for kw in keywords) and any(kw in str(val_gst).upper() for kw in keywords):
                            continue

                    if col == "PLACE OF SUPPLY":
                        val_books = clean_state(val_books)
                        val_gst = clean_state(val_gst)

                    if col == "INVOICE DATE":
                        val_books = str(val_books)
                        val_gst = str(val_gst)

                    if val_books != val_gst:
                        mismatch_cols.append(f"{col} Mismatch")
                        annexure.append({
                            **{k: b_row[k] for k in key_cols},
                            "Column": col,
                            "Books Value": val_books,
                            "GST Value": val_gst,
                            "Difference": "N/A"
                        })

            for col in amount_cols:
                if col in books_agg.columns and col in gst_df.columns:
                    try:
                        b_val = books_agg.loc[books_agg["INVOICE NO"] == b_row["INVOICE NO"], col].values[0]
                        diff = float(b_val) - float(gst_df.at[gst_idx, col])
                        if abs(diff) > numeric_tolerance:
                            mismatch_cols.append(f"{col} (Diff: {diff:.2f})")
                            annexure.append({
                                **{k: b_row[k] for k in key_cols},
                                "Column": col,
                                "Books Value": b_val,
                                "GST Value": gst_df.at[gst_idx, col],
                                "Difference": diff
                            })
                    except:
                        pass

            status = "✅ Perfect Invoice Match" if not mismatch_cols else "⚠️ Mismatch"
            full_result.append({**b_row.to_dict(), "Status": status})

    for idx_g, g_row in gst_df.iterrows():
        if idx_g not in gst_matched_indices:
            full_result.append({**g_row.to_dict(), "Status": "📑 Only in GST"})

    return pd.DataFrame(full_result), pd.DataFrame(annexure)

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

        # Map columns to standard
        col_mapping = {
            "INVOICE NUMBER": "INVOICE NO",
            "INVOICE NO": "INVOICE NO",
            "GSTIN": "GSTIN/UIN OF RECIPIENT",
            "GSTIN/UIN OF RECIPIENT": "GSTIN/UIN OF RECIPIENT",
            "RECEIVER NAME": "RECEIVER NAME"
        }
        books.rename(columns=col_mapping, inplace=True)
        gst.rename(columns=col_mapping, inplace=True)

        # ------------------------
        # Input Data Summary
        # ------------------------
        def display_invoice_summary(gst_df, books_df):
            st.subheader("📊 Input Data Summary")
            gst_invoices = gst_df["INVOICE NO"].nunique() if "INVOICE NO" in gst_df.columns else 0
            books_invoices = books_df["INVOICE NO"].nunique() if "INVOICE NO" in books_df.columns else 0

            gst_total = pd.to_numeric(gst_df["INVOICE VALUE"], errors="coerce").sum() if "INVOICE VALUE" in gst_df.columns else 0
            books_total = pd.to_numeric(books_df["INVOICE VALUE"], errors="coerce").sum() if "INVOICE VALUE" in books_df.columns else 0

            summary_df = pd.DataFrame({
                "Metric": ["Number of Invoices", "Total Invoice Value"],
                "GST Data": [gst_invoices, gst_total],
                "Books Data": [books_invoices, books_total]
            })

            st.table(summary_df)

        display_invoice_summary(gst, books)

        # Fixed key columns & amount columns
        key_cols = ["INVOICE NO", "GSTIN/UIN OF RECIPIENT", "RECEIVER NAME"]
        amount_cols = ["INVOICE VALUE", "TAXABLE VALUE", "INTEGRATED TAX", "CENTRAL TAX", "STATE/UT TAX"]

        if st.button("Run Reconciliation"):
            st.info("🔎 Performing GST Reconciliation at Invoice Level...")
            full_reco, annexure = reconcile_rows(books, gst, key_cols, amount_cols, numeric_tolerance=1.0)

            # Summary
            summary_counts = {
                "✅ Perfect Invoice Match": (full_reco["Status"] == "✅ Perfect Invoice Match").sum(),
                "⚠️ Mismatch": full_reco["Status"].str.startswith("⚠️").sum(),
                "📄 Only in Books": (full_reco["Status"] == "📄 Only in Books").sum(),
                "📑 Only in GST": (full_reco["Status"] == "📑 Only in GST").sum()
            }
            summary_df = pd.DataFrame({
                "Result": list(summary_counts.keys()),
                "Count": list(summary_counts.values())
            })

            st.subheader("📌 Reconciliation Summary")
            st.dataframe(summary_df, use_container_width=True)

            st.subheader("📋 Full Reconciliation")
            st.dataframe(full_reco, use_container_width=True)

            if not annexure.empty:
                st.subheader("📑 Differences Annexure")
                st.dataframe(annexure, use_container_width=True)

            # Download buttons
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
