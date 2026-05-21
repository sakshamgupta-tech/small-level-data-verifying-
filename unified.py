from pathlib import Path
import io
import json
import os
import re
from datetime import datetime
from collections import Counter
from decimal import Decimal, InvalidOperation
from io import BytesIO

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Unified Recon", layout="wide")

EXPIRY_OPTIONS = ["NF0DTE", "NF1DTE", "NF4DTE", "SX1DTE", "SX0DTE"]

def chk_normalize_sheet_key(v) -> str: return re.sub(r"[^a-z0-9]+", "", str(v or "").strip().lower())
def chk_clean_user_id(uid) -> str:
    if pd.isna(uid): return ""
    s = str(uid).strip()
    return s[:-2] if s.endswith('.0') else s
def chk_find_user_id_column(df):
    for c in df.columns:
        if chk_normalize_sheet_key(c) in {"userid", "clientid", "loginid", "user id", "client id"}: return c
    return None
def chk_find_column_by_name(df, t):
    tk = chk_normalize_sheet_key(t)
    for c in df.columns:
        if chk_normalize_sheet_key(c) == tk: return c
    return None
def chk_find_expiry_column(df):
    for c in df.columns:
        if chk_normalize_sheet_key(c) in {"expire", "expiry", "expirydte", "expiredte", "expirydate", "expiredate"}: return c
    for c in df.columns:
        if "expiry" in chk_normalize_sheet_key(c) or "expire" in chk_normalize_sheet_key(c): return c
    return None
def chk_clean_report_text(v) -> str:
    t = re.sub(r"(?:â|Ã¢)\S*\s*", "", str(v or "")).strip()
    return "OK" if t == "OK" else "Issue" if "Issue" in t else "(EOD) OK" if t == "(EOD)" else t
def chk_normalize_expiry_value(v) -> str: return re.sub(r"[^A-Z0-9]+", "", str(v or "").strip().upper())
def chk_extract_server(f: str) -> str:
    m = re.search(r"VS[\s_-]*(\d+)", str(f).upper())
    return m.group(1) if m else ""
def chk_format_date_parts(d, m, y) -> str:
    mos = ["", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    try: dt = datetime(int(y), int(m), int(d))
    except: return ""
    return f"{dt.day} {mos[dt.month]} {dt.year}"
def chk_extract_date(f: str) -> str:
    n = str(f).upper()
    m = re.search(r"\b([0-3]?\d)[\s\-_/]+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[\s\-_/]+(\d{4})\b", n)
    if m: return chk_format_date_parts(m.group(1), {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,"JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}[m.group(2)], m.group(3))
    m2 = re.search(r"\b([0-3]?\d)[\s\-_/]+(0?[1-9]|1[0-2])[\s\-_/]+(\d{4})\b", n)
    return chk_format_date_parts(m2.group(1), m2.group(2), m2.group(3)) if m2 else ""
def chk_extract_correctable_date(f: str) -> str:
    n = str(f).upper()
    ml = {"JAN":1,"JANUARY":1,"FEB":2,"FEBRUARY":2,"MAR":3,"MARCH":3,"APR":4,"APRIL":4,"MAY":5,"JUN":6,"JUNE":6,"JUL":7,"JULY":7,"AUG":8,"AUGUST":8,"SEP":9,"SEPT":9,"SEPTEMBER":9,"OCT":10,"OCTOBER":10,"NOV":11,"NOVEMBER":11,"DEC":12,"DECEMBER":12}
    m = re.search(r"\b([0-3]?\d)[\s\-_/]*(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|SEPT|OCTOBER|NOVEMBER|DECEMBER|JAN|FEB|MAR|APR|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[\s\-_/]*(\d{4})\b", n)
    if m: return chk_format_date_parts(m.group(1), ml[m.group(2)], m.group(3))
    m = re.search(r"\b(\d{4})[\s\-_/]+(0?[1-9]|1[0-2])[\s\-_/]+([0-3]?\d)\b", n)
    if m: return chk_format_date_parts(m.group(3), m.group(2), m.group(1))
    m = re.search(r"\b([0-3]?\d)[\s\-_/]+(0?[1-9]|1[0-2])[\s\-_/]+(\d{4})\b", n)
    if m: return chk_format_date_parts(m.group(1), m.group(2), m.group(3))
    m = re.search(r"\b([0-3]\d)(0[1-9]|1[0-2])(\d{4})\b", n)
    return chk_format_date_parts(m.group(1), m.group(2), m.group(3)) if m else ""
def chk_build_corrected_filename(f: str, ft: str, srv: str, dt: str) -> str:
    ext = "." + f.rsplit(".", 1)[1] if "." in f else ""
    sp = f"VS{srv}" if srv else "VSxx"
    dp = dt or "DD MON YYYY"
    return f"{sp} {dp} {('GridLog' if ft=='GRIDLOG' else ft.title() if ft=='MTM' else ft)}{' (EOD)' if ft=='POSITION' else ''}{ext.lower()}"
def chk_pick_expected_value(c, fb="") -> str:
    if not c: return fb
    mc = c.most_common()
    return mc[0][0] if len(mc)==1 or mc[0][1]>mc[1][1] else fb
def chk_extract_position_suffix(f: str) -> str:
    m = re.search(r"\((BOD|EOD)\)$", f.rsplit(".", 1)[0].upper())
    return m.group(1) if m else ""
def chk_validate_position_filename(f: str) -> tuple[bool, str]:
    return (True, "(EOD) ✅") if chk_extract_position_suffix(f) == "EOD" else (False, "Missing (EOD) ❌")
def chk_format_filename_status_df(fs) -> pd.DataFrame:
    df = pd.DataFrame(fs)
    if df.empty: return df
    if "Corrected Filename" not in df.columns: df["Corrected Filename"] = df.get("Correct Name", "")
    df["Corrected Filename"] = df.apply(lambda r: r.get("Corrected Filename", "") if r.get("Status") == "Issue" else "-", axis=1)
    return df[[c for c in ["File Type", "Filename", "Corrected Filename", "Server", "Date", "Format", "Status", "Issues", "BOD Check"] if c in df.columns]]
def chk_load_csv_from_upload(u):
    try: return pd.read_csv(io.BytesIO(u.getvalue() if hasattr(u, "getvalue") else u.read()))
    except: return None
def chk_validate_all_filenames(sf, pos, ob, gl, mtm):
    fs, srvs, dts, sc, dc, iss = [], set(), set(), Counter(), Counter(), []
    for ft, fl in [("SUMMARY", sf), ("POSITION", [pos] if pos else []), ("ORDERBOOK", [ob] if ob else []), ("GRIDLOG", [gl] if gl else []), ("MTM", [mtm] if mtm else [])]:
        for f in fl:
            if not f: continue
            fname = getattr(f, 'name', str(f))
            s, d = chk_extract_server(fname), chk_extract_date(fname)
            if s: srvs.add(s); sc[s] += 1
            if d: dts.add(d); dc[d] += 1
            status, f_iss = "✅ OK", []
            if not s: f_iss.append("Missing VSxx"); status = "❌ Issue"
            if not d: f_iss.append("Missing Date"); status = "❌ Issue"
            if ft == "POSITION":
                val, msg = chk_validate_position_filename(fname)
                f_iss.append(msg)
                if not val: status = "❌ Issue"
            fs.append({
                "File Type": ft, "Filename": fname, "Corrected Filename": "",
                "Server": f"VS{s}" if s else "Missing", "Date": d or "Missing",
                "BOD Check" if ft=="POSITION" else "Format": f_iss[-1] if ft=="POSITION" else (", ".join(f_iss) if f_iss else "OK"),
                "Status": "OK" if "OK" in status else "Issue", "Issues": ", ".join(f_iss) if f_iss else "None"
            })
    if len(srvs) > 1: iss.append(f"Multiple servers: {', '.join(srvs)}")
    if len(dts) > 1: iss.append(f"Multiple dates: {', '.join(dts)}")
    ssrv = next((x["Server"].replace("VS","") for x in fs if x["File Type"]=="SUMMARY" and x["Server"]!="Missing"), "")
    sdt = next((x["Date"] for x in fs if x["File Type"]=="SUMMARY" and x["Date"]!="Missing"), "")
    csrv, cdt = chk_pick_expected_value(sc, ssrv), chk_pick_expected_value(dc, sdt)
    for x in fs:
        i_iss = [i.strip() for i in x["Issues"].split(",") if i.strip()] if x["Status"]=="Issue" and x["Issues"]!="None" else []
        cur_s = x["Server"].replace("VS","") if x["Server"]!="Missing" else ""
        cur_d = x["Date"] if x["Date"]!="Missing" else ""
        item_s = cur_s or csrv
        item_d = cur_d or chk_extract_correctable_date(x["Filename"]) or cdt
        if csrv and cur_s and cur_s != csrv: i_iss.append(f"Expected Server: VS{csrv}"); item_s = csrv
        if cdt and cur_d and cur_d != cdt: i_iss.append(f"Expected Date: {cdt}"); item_d = cdt
        corr = chk_build_corrected_filename(x["Filename"], x["File Type"], item_s, item_d)
        x["Corrected Filename"] = corr; x["Correct Name"] = corr; x["Suggested Filename"] = corr
        if i_iss: x["Status"] = "Issue"; x["Issues"] = ", ".join(dict.fromkeys(i_iss))
    return {"file_status": fs, "issues": iss}
def chk_parse_reference_file(u) -> dict:
    rl = {}
    try:
        content = u.getvalue() if hasattr(u, "getvalue") else u.read() if hasattr(u, "read") else None
        df = pd.read_csv(io.BytesIO(content)) if content else pd.read_csv(u)
    except:
        try: df = pd.read_excel(io.BytesIO(content), engine="openpyxl")
        except: return rl
    cols = [chk_normalize_sheet_key(c) for c in df.columns]
    if "userid" not in cols and "user id" not in cols: return rl
    uc = df.columns[cols.index("userid" if "userid" in cols else "user id")]
    sc = df.columns[cols.index("server")] if "server" in cols else None
    if sc is None:
        rl["ALL"] = [chk_clean_user_id(uid) for uid in df[uc].dropna()]
        return rl
    for srv, grp in df.groupby(sc):
        s_code = chk_extract_server(str(srv))
        if s_code: rl[s_code] = [chk_clean_user_id(uid) for uid in grp[uc].dropna() if chk_clean_user_id(uid)]
    return rl
def chk_get_reference_users_for_server(rl: dict, sc: str) -> set:
    t = chk_extract_server(sc)
    return set(rl[t]) if t in rl else set(rl.get("ALL", []))
def chk_get_all_unique_users(xl: dict) -> set:
    u = set()
    for df in xl.values():
        col = chk_find_user_id_column(df)
        if col: u.update({chk_clean_user_id(x) for x in df[col].dropna() if chk_clean_user_id(x)})
    return u
def chk_get_summary_users(xl: dict) -> set:
    for sn, df in xl.items():
        if chk_normalize_sheet_key(sn) == "users":
            col = chk_find_user_id_column(df)
            return {chk_clean_user_id(x) for x in df[col].dropna() if chk_clean_user_id(x)} if col else set()
    return set()
def chk_get_summary_algos(xl: dict) -> list:
    for sn, df in xl.items():
        if chk_normalize_sheet_key(sn) == "users":
            col = chk_find_column_by_name(df, "ALGO")
            if not col: return []
            alg = []
            for v in df[col].dropna():
                t = str(v).strip()
                if t.endswith(".0"): t = t[:-2]
                if t and t.lower() not in {"nan", "none"} and t not in alg: alg.append(t)
            return alg
    return []
def chk_validate_summary_sheets(xl: dict, rus: set):
    res, f_iss, skip = {}, [], []
    all_u = chk_get_all_unique_users(xl) if not rus else set(rus)
    sum_u = chk_get_summary_users(xl)
    for sn, df in xl.items():
        col = chk_find_user_id_column(df)
        if col is None:
            skip.append(sn); res[sn] = {"status":"No User ID Col","missing":[],"extra":[]}
            continue
        pres = {chk_clean_user_id(x) for x in df[col].dropna() if chk_clean_user_id(x)}
        comp = rus if chk_normalize_sheet_key(sn) == "users" and rus else sum_u if sum_u else rus if rus else all_u
        m, e = sorted(comp - pres), sorted(pres - comp)
        status = "All match" if not (m or e) else "Issue"
        if m or e: f_iss.append(f"{sn}: Missing {len(m)} | Extra {len(e)}")
        res[sn] = {"status": status, "missing": m, "extra": e}
    return res, f_iss, skip
def chk_validate_expiry_sheets(xl: dict, se: str = None):
    if not se: return {}, []
    exp_val, res, f_iss, found = chk_normalize_expiry_value(se), {}, [], False
    for sn, df in xl.items():
        col = chk_find_expiry_column(df)
        if not col: continue
        found = True
        vals = [str(v).strip() for v in df[col].dropna() if str(v).strip() and str(v).strip().lower() not in {"nan","none"}]
        mism = sorted({v for v in vals if chk_normalize_expiry_value(v) != exp_val})
        if mism: f_iss.append(f"{sn}: Expire expected {se}, found {', '.join(mism[:10])}" + ("..." if len(mism)>10 else ""))
        res[sn] = {"status": "All match" if not mism else "Issue", "expected": se, "mismatches": mism, "column": col}
    if not found: f_iss.append(f"Expire column not found in Summary Excel. Checked sheets: {', '.join(xl.keys())}")
    return res, f_iss
def chk_extract_algo(f: str) -> str:
    n = f.rsplit(".", 1)[0]
    return n.split("_")[0] if "_" in n else n
def chk_check_summary_self_contained(sf, rl, ss=None, se=None):
    res = []
    for uf in sf:
        try:
            xl = pd.read_excel(uf, sheet_name=None, engine="openpyxl")
            fn = uf.name
            fs = chk_extract_server(fn)
            salg = chk_get_summary_algos(xl)
            algo = ", ".join(salg) if salg else chk_extract_algo(fn)
            ssrv = ss if ss and ss != "Auto Detect" else fs
            rus = chk_get_reference_users_for_server(rl, ssrv)
            all_u = chk_get_all_unique_users(xl)
            sum_u = chk_get_summary_users(xl)
            s_res, f_iss, skip = chk_validate_summary_sheets(xl, rus)
            e_res, e_iss = chk_validate_expiry_sheets(xl, se)
            for sn, ed in e_res.items():
                s_res.setdefault(sn, {"status":"All match","missing":[],"extra":[]})
                if ed["status"] == "Issue": s_res[sn]["status"] = "Issue"
                s_res[sn].update({"expiry_status":ed["status"],"expiry_expected":ed["expected"],"expiry_mismatches":ed["mismatches"],"expiry_column":ed["column"]})
            m_sum = sorted(rus - sum_u) if rus else []
            e_sum = sorted(sum_u - rus) if rus else []
            tot = len(f_iss) + len(e_iss) + bool(m_sum) + bool(e_sum)
            res.append({
                "File": fn, "Server": fs, "Algo": algo, "ALGO": algo, "Summary Users ALGO": algo,
                "Sheets": len(xl), "Total Unique Users": len(all_u), "Running Users (Same Server)": len(rus),
                "Total Issues": tot, "Selected Expiry": se or "", "Expiry Issues": e_iss,
                "sheet_results": s_res, "skipped_sheets": skip, "missing_in_summary": m_sum, "extra_in_summary": e_sum
            })
        except Exception as e: st.error(f"Failed to process {uf.name}: {e}")
    return res
def chk_build_validation_report_csv(nc: dict, ar: list) -> bytes:
    rows = []
    for item in nc.get("file_status", []):
        rows.append({
            "Section": "Filename Check", "File": item.get("Filename", ""), "Sheet": "",
            "Status": chk_clean_report_text(item.get("Status", "")), "Server": item.get("Server", ""), "Date": item.get("Date", ""),
            "Missing": "", "Extra": "", "Issues": chk_clean_report_text(item.get("Issues", "")),
            "Details": chk_clean_report_text(item.get("BOD Check", item.get("Format", ""))),
            "Corrected Filename": item.get("Corrected Filename", ""), "Correct Name": item.get("Correct Name", ""), "Suggested Filename": item.get("Suggested Filename", "")
        })
    for res in ar:
        rows.append({
            "Section": "Summary Overall", "File": res["File"], "Sheet": "", "Status": "OK" if res["Total Issues"]==0 else "Issues",
            "Server": res["Server"], "Date": "", "Missing": ", ".join(res["missing_in_summary"]), "Extra": ", ".join(res["extra_in_summary"]),
            "Issues": res["Total Issues"], "Details": f"Sheets={res['Sheets']}; Users in Summary={res['Total Unique Users']}; Running Users={res['Running Users (Same Server)']}; ALGO={res.get('ALGO','N/A')}; Expiry={res.get('Selected Expiry','')}",
            "ALGO": res.get("ALGO", ""), "Expiry": res.get("Selected Expiry", "")
        })
        for sn, data in res["sheet_results"].items():
            xm = data.get("expiry_mismatches", [])
            ed = f"Expire expected {data.get('expiry_expected')}; found {', '.join(xm)}" if xm else f"Expire matches {data.get('expiry_expected')}" if data.get("expiry_expected") else ""
            rows.append({
                "Section": "Per-Sheet Validation", "File": res["File"], "Sheet": sn, "Status": data["status"], "Server": res["Server"], "Date": "",
                "Missing": ", ".join(data["missing"]), "Extra": ", ".join(data["extra"]), "Issues": "", "Details": ed,
                "Expiry": data.get("expiry_expected", ""), "Expiry Status": data.get("expiry_status", "")
            })
    return pd.DataFrame(rows).to_csv(index=False).encode("utf-8-sig")
def chk_get_google_sheets_service(cf=None):
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except Exception as e: raise RuntimeError("Google API packages are not installed. Run: pip install -r requirements.txt") from e
    if cf:
        cf.seek(0)
        return build("sheets", "v4", credentials=service_account.Credentials.from_service_account_info(json.loads(cf.getvalue().decode("utf-8")), scopes=scopes), cache_discovery=False)
    if "gcp_service_account" in st.secrets:
        return build("sheets", "v4", credentials=service_account.Credentials.from_service_account_info(dict(st.secrets["gcp_service_account"]), scopes=scopes), cache_discovery=False)
    cp = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if cp and os.path.exists(cp):
        return build("sheets", "v4", credentials=service_account.Credentials.from_service_account_file(cp, scopes=scopes), cache_discovery=False)
    raise RuntimeError("Google Sheets credentials not configured.")
def chk_get_first_sheet_title(srv, sid: str) -> str:
    s = srv.spreadsheets().get(spreadsheetId=sid).execute().get("sheets", [])
    if not s: raise RuntimeError("No worksheet tabs found.")
    return s[0]["properties"]["title"]
def chk_save_validation_report_to_google_sheet(rc, sid: str = "1XIQ9gx8YWqXCgYmHdftafzHPSvFPaAVl0tpdaWEQGKE", cf=None) -> str:
    df = pd.read_csv(io.BytesIO(rc)).fillna("")
    vals = [df.columns.tolist()] + df.astype(str).values.tolist()
    srv = chk_get_google_sheets_service(cf)
    stitle = chk_get_first_sheet_title(srv, sid).replace("'", "''")
    srv.spreadsheets().values().clear(spreadsheetId=sid, range=f"'{stitle}'!A:ZZ", body={}).execute()
    srv.spreadsheets().values().update(spreadsheetId=sid, range=f"'{stitle}'!A1", valueInputOption="USER_ENTERED", body={"values":vals}).execute()
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit"

def ord_extract_server_code(f: str) -> str:
    n = re.sub(r"\s*(ORDERBOOK|SUMMARY|SAVE\s*MTM|SAVEMTM|MTM|DB|\(\d+\))", "", f.upper())
    n = re.sub(r"\.CSV|\.XLSX?|\.XLS", "", n).strip()
    m = re.search(r"VS\s*\d+", n)
    if m: return m.group(0).replace(" ", "")
    for w in n.split():
        if w.startswith("VS") and any(c.isdigit() for c in w): return w.replace(" ", "")
    return "UNKNOWN"
def ord_clean_column_names(df) -> pd.DataFrame:
    c = df.columns.tolist()
    if len(c) > 1: df.columns = c[1:] + ["last"]
    return df
def ord_normalize_col_name(v) -> str: return re.sub(r"[^a-z0-9]+", "", str(v).strip().lower())
def ord_normalize_user_id(v) -> str:
    if pd.isna(v): return ""
    t = str(v).strip()
    return (t[:-2] if t.endswith(".0") else t).upper()
def ord_normalize_order_id(v) -> str:
    if pd.isna(v): return ""
    t = str(v).strip().replace(",", "")
    if not t or t.lower() in {"nan", "none", "nat"}: return ""
    if re.match(r"^[HVSMhvsm]\d", t): t = t[1:]
    if not re.search(r"\d", t): return ""
    try:
        dv = Decimal(t)
        if dv == dv.to_integral_value(): return format(dv.quantize(Decimal("1")), "f")
    except: pass
    return (t[:-2] if t.endswith(".0") else t).upper()
def ord_to_number_series(s: pd.Series) -> pd.Series:
    cl = s.astype(str).str.replace(",", "", regex=False).str.strip().replace({"": None, "NAN": None, "None": None, "none": None})
    return pd.to_numeric(cl, errors="coerce")
def ord_find_column(df, cands, conts=None):
    n = {ord_normalize_col_name(c): c for c in df.columns}
    for c in cands:
        if ord_normalize_col_name(c) in n: return n[ord_normalize_col_name(c)]
    if conts:
        conts = [ord_normalize_col_name(x) for x in conts]
        for c in df.columns:
            if any(x in ord_normalize_col_name(c) for x in conts): return c
    return None
def ord_find_user_column(df): return ord_find_column(df, ["UserID", "User ID", "Client ID", "ClientID", "Login ID", "LoginID"], ["userid", "user", "clientid", "loginid"])
def ord_find_alias_column(df): return ord_find_column(df, ["Alias", "User Alias", "Client Name"], ["alias"])
def ord_find_mtm_column(df): return ord_find_column(df, ["All MTM", "MTM", "Save MTM", "Total MTM", "Net MTM", "MTM Value", "PNL", "P&L"], ["allmtm", "savemtm", "totalmtm", "netmtm", "mtm", "pnl"])
def ord_find_max_loss_column(df): return ord_find_column(df, ["MAX_LOSS", "Max Loss", "max_loss", "MaxLoss", "Maximum Loss"], ["maxloss", "maximumloss"])
def ord_find_orderbook_user_column(df): return ord_find_column(df, ["User ID", "UserID"], ["userid", "user"])
def ord_find_orderbook_status_column(df): return ord_find_column(df, ["Status", "Order Status"], ["status"])
def ord_find_status_column(df): return ord_find_column(df, ["Status", "Order Status"], ["status"])
def ord_find_transaction_column(df): return ord_find_column(df, ["Transaction", "Side", "Transaction Type"], ["transaction", "side"])
def ord_find_quantity_column(df): return ord_find_column(df, ["Quantity", "Qty"], ["quantity", "qty"])
def ord_find_avg_price_column(df): return ord_find_column(df, ["Avg Price", "Average Price", "AvgPrice", "Executed Price", "Price"], ["avgprice", "averageprice", "executedprice", "price"])
def ord_find_symbol_column(df): return ord_find_column(df, ["Symbol"], ["symbol"])
def ord_find_order_id_column(df): return ord_find_column(df, ["Order ID", "OrderID"], ["orderid"])
def ord_find_orderbook_tag_column(df, excl=None):
    excl = set(excl or [])
    tc = ord_find_column(df, ["Tag", "Order Tag", "Order ID", "OrderID"], ["tag", "orderid"])
    if tc and tc not in excl:
        if df[tc].dropna().astype(str).str.strip().apply(ord_normalize_order_id).str.fullmatch(r"\d{7,}").fillna(False).sum() > 0: return tc
    def score(col):
        s = df[col].dropna().astype(str).str.strip()
        s = s[s.ne("")]
        if s.empty: return 0
        norm = s.apply(ord_normalize_order_id)
        return norm[norm.ne("")].str.fullmatch(r"\d{7,}").fillna(False).sum()
    bc, bs = None, 0
    for c in df.columns:
        if c in excl: continue
        ck = ord_normalize_col_name(c)
        if ck and not ck.startswith("unnamed") and ck != "last": continue
        sc = score(c)
        if sc > bs: bc, bs = c, sc
    if bc: return bc
    for c in df.columns:
        if c in excl: continue
        sc = score(c)
        if sc > bs: bc, bs = c, sc
    return bc
def ord_has_valid_order_ids(df, col) -> bool:
    if not col or col not in df.columns: return False
    return df[col].dropna().astype(str).str.strip().apply(ord_normalize_order_id).str.fullmatch(r"\d{7,}").fillna(False).sum() > 0
def ord_extract_order_ids_from_frame(df, excl=None, excl_v=None) -> set:
    excl, excl_v = set(excl or []), set(excl_v or [])
    ids = set()
    for c in df.columns:
        if c in excl: continue
        norm = df[c].dropna().astype(str).str.strip().apply(ord_normalize_order_id)
        v = norm[norm.str.fullmatch(r"\d{7,}").fillna(False)]
        ids.update(v[~v.isin(excl_v)].tolist())
    return ids
def ord_count_orderbook_orders_for_users(df, suids) -> pd.Series:
    suids = set(suids or [])
    cnts = {u: 0 for u in suids}
    if not suids: return pd.Series(dtype="int64", name="Total_orders_in_orderbook")
    for _, row in df.iterrows():
        vals = [str(x).strip() for x in row.tolist() if str(x).strip()]
        ru = [ord_normalize_user_id(x) for x in vals]
        mu = next((x for x in ru if x in suids), "")
        if not mu: continue
        roids = [ord_normalize_order_id(x) for x in vals]
        roids = [x for x in roids if re.fullmatch(r"\d{7,}", x or "") and x not in suids]
        if roids: cnts[mu] += 1
    return pd.Series(cnts, name="Total_orders_in_orderbook")
def ord_find_order_time_column(df): return ord_find_column(df, ["Order Time", "Order Tim", "Time"], ["ordertime", "time"])
def ord_find_total_orders_column(df): return ord_find_column(df, ["Total Orders", "Total Ord"], ["totalorders", "totalord"])
def ord_find_server_column(df): return ord_find_column(df, ["SERVER", "Server"], ["server"])
def ord_find_algo_column(df): return ord_find_column(df, ["ALGO", "Algo"], ["algo"])
def ord_find_portfolio_column(df): return ord_find_column(df, ["Portfolio Name", "Portfolio", "Portfolio_Name", "PORTFOLIO NAME"], ["portfolioname", "portfolio"])
def ord_find_allocation_column(df): return ord_find_column(df, ["ALLOCATION", "Allocation", "ALLOCATI"], ["allocation", "allocati"])
def ord_find_total_lots_column(df): return ord_find_column(df, ["Total Lots"], ["totallots", "lots"])
def ord_find_date_column(df): return ord_find_column(df, ["DATE", "Date"], ["date"])
def ord_read_save_mtm_file(smf) -> pd.DataFrame:
    smf.seek(0)
    fn = smf.name.lower()
    frms = [pd.read_csv(smf)] if fn.endswith(".csv") else list(pd.read_excel(smf, sheet_name=None).values())
    mf, lf = [], []
    for frm in frms:
        uc, mc, lc = ord_find_user_column(frm), ord_find_mtm_column(frm), ord_find_max_loss_column(frm)
        if not uc: continue
        if mc:
            tmp = frm[[uc, mc]].copy()
            tmp.columns = ["UserID_Key", "Save_MTM"]
            tmp["UserID_Key"] = tmp["UserID_Key"].apply(ord_normalize_user_id)
            tmp["Save_MTM"] = ord_to_number_series(tmp["Save_MTM"]).fillna(0)
            mf.append(tmp[tmp["UserID_Key"] != ""])
        if lc:
            tmp = frm[[uc, lc]].copy()
            tmp.columns = ["UserID_Key", "Save_Max_Loss"]
            tmp["UserID_Key"] = tmp["UserID_Key"].apply(ord_normalize_user_id)
            tmp["Save_Max_Loss"] = ord_to_number_series(tmp["Save_Max_Loss"])
            lf.append(tmp[tmp["UserID_Key"] != ""])
    if not mf: raise ValueError("Could not find UserID and MTM columns in Save MTM file.")
    res = pd.concat(mf, ignore_index=True).groupby("UserID_Key", as_index=False)["Save_MTM"].sum()
    if lf:
        l_df = pd.concat(lf, ignore_index=True).groupby("UserID_Key", as_index=False)["Save_Max_Loss"].first()
        res = res.merge(l_df, on="UserID_Key", how="left")
    else: res["Save_Max_Loss"] = pd.NA
    return res
def ord_read_summary_mtm_from_sheets(sf) -> pd.DataFrame:
    sf.seek(0)
    shs = pd.read_excel(sf, sheet_name=None)
    frms = []
    for sn, frm in shs.items():
        if sn.strip().lower() == "users": continue
        uc, mc = ord_find_user_column(frm), ord_find_mtm_column(frm)
        if uc and mc:
            tmp = frm[[uc, mc]].copy()
            tmp.columns = ["UserID_Key", "Summary_MTM"]
            tmp["UserID_Key"] = tmp["UserID_Key"].apply(ord_normalize_user_id)
            tmp["Summary_MTM"] = ord_to_number_series(tmp["Summary_MTM"]).fillna(0)
            frms.append(tmp[tmp["UserID_Key"] != ""])
    if not frms: return pd.DataFrame(columns=["UserID_Key", "Summary_MTM"])
    return pd.concat(frms, ignore_index=True).groupby("UserID_Key", as_index=False)["Summary_MTM"].sum()
def ord_read_summary_multileg_complete_orders(sf, sc: str) -> pd.DataFrame:
    sf.seek(0)
    try: df_m = pd.read_excel(sf, sheet_name="MultiLeg Orders")
    except: return pd.DataFrame()
    stc, uc, ac, pc = ord_find_status_column(df_m), ord_find_user_column(df_m), ord_find_alias_column(df_m), ord_find_portfolio_column(df_m)
    oid = ord_find_order_id_column(df_m) or ord_find_orderbook_tag_column(df_m, [stc, uc, ac, pc])
    if not oid or not stc: raise ValueError('Could not find "Order ID" and "Status" columns in Summary MultiLeg Orders.')
    df = pd.DataFrame()
    df["Detected_Server"] = [sc]*len(df_m)
    df["Source_Summary"] = [sf.name]*len(df_m)
    df["Summary_Order_ID"] = df_m[oid].astype(str).str.strip()
    df["Order_ID_Key"] = df["Summary_Order_ID"].apply(ord_normalize_order_id)
    df["Summary_Status"] = df_m[stc].astype(str).str.strip().str.upper()
    df["UserID"] = df_m[uc].astype(str).str.strip() if uc else ""
    df["UserID_Key"] = df["UserID"].apply(ord_normalize_user_id)
    df["Alias"] = df_m[ac].astype(str).str.strip() if ac else ""
    df["Portfolio_Name"] = df_m[pc].astype(str).str.strip() if pc else ""
    df = df[df["Summary_Status"].eq("COMPLETE") & df["Order_ID_Key"].ne("")]
    return df.drop_duplicates(subset=["Order_ID_Key"]).copy()
def ord_build_portfolio_count_df(smo: pd.DataFrame) -> pd.DataFrame:
    if smo.empty or "Portfolio_Name" not in smo.columns: return pd.DataFrame()
    df = smo.copy()
    df["Portfolio_Name"] = df["Portfolio_Name"].fillna("").astype(str).str.strip()
    df = df[df["Portfolio_Name"].ne("")]
    return df.groupby(["Detected_Server", "Source_Summary", "Portfolio_Name"], as_index=False).size().rename(columns={"size": "Complete_MultiLeg_Count"}) if not df.empty else pd.DataFrame()
def ord_build_multileg_user_count_df(smo: pd.DataFrame) -> pd.DataFrame:
    if smo.empty or "UserID_Key" not in smo.columns: return pd.DataFrame(columns=["UserID_Key", "Complete_MultiLeg_Count"])
    df = smo[smo["UserID_Key"].ne("")].copy()
    return df.groupby("UserID_Key", as_index=False).size().rename(columns={"size": "Complete_MultiLeg_Count"}) if not df.empty else pd.DataFrame(columns=["UserID_Key", "Complete_MultiLeg_Count"])
def ord_build_multileg_check_summary(smo: pd.DataFrame, mmo: pd.DataFrame, sc: str, sn: str, obn: str) -> pd.DataFrame:
    tc, mc = len(smo), len(mmo)
    return pd.DataFrame([{"Detected_Server": sc, "Source_Summary": sn, "Source_Orderbook": obn, "Complete_MultiLeg_Order_IDs": tc, "Matched_In_Orderbook": tc - mc, "Missing_In_Orderbook": mc}])
def ord_read_orderbook_rows(obf, sc: str, suids=None) -> pd.DataFrame:
    obf.seek(0)
    df_ob = ord_clean_column_names(pd.read_csv(obf, dtype=str, keep_default_na=False))
    suids = set(suids or [])
    uc, stc, tc, qc, apc = ord_find_orderbook_user_column(df_ob), ord_find_orderbook_status_column(df_ob), ord_find_transaction_column(df_ob), ord_find_quantity_column(df_ob), ord_find_avg_price_column(df_ob)
    if not (uc and stc and tc and qc and apc): raise ValueError("Could not find required Orderbook columns.")
    ac, smc, oid, otc = ord_find_alias_column(df_ob), ord_find_symbol_column(df_ob), ord_find_order_id_column(df_ob), ord_find_order_time_column(df_ob)
    ids = ord_extract_order_ids_from_frame(df_ob, [uc, stc, qc, apc, ac, smc, otc], suids)
    cnts = ord_count_orderbook_orders_for_users(df_ob, suids)
    if not ord_has_valid_order_ids(df_ob, oid): oid = ord_find_orderbook_tag_column(df_ob, [uc, stc, qc, apc, ac, smc, otc, oid])
    oid = oid or ord_find_orderbook_tag_column(df_ob, [uc, stc, qc, apc, ac, smc, otc])
    df = pd.DataFrame()
    df["Detected_Server"] = [sc]*len(df_ob)
    df["Source_Orderbook"] = [obf.name]*len(df_ob)
    df["UserID"] = df_ob[uc].astype(str).str.strip()
    df["UserID_Key"] = df["UserID"].apply(ord_normalize_user_id)
    df["User_Alias"] = df_ob[ac].astype(str).str.strip() if ac else ""
    df["Symbol"] = df_ob[smc].astype(str).str.strip() if smc else ""
    df["Order_ID"] = df_ob[oid].astype(str).str.strip() if oid else ""
    df["Order_ID_Key"] = df["Order_ID"].apply(ord_normalize_order_id)
    df["Order_Time"] = df_ob[otc].astype(str).str.strip() if otc else ""
    df["Status"] = df_ob[stc].astype(str).str.strip().str.upper()
    side = df_ob[tc].astype(str).str.strip().str.upper().replace({"B": "BUY", "S": "SELL"})
    df["Transaction_Type"] = side
    df["Quantity"] = ord_to_number_series(df_ob[qc]).fillna(0)
    df["Avg_Price"] = ord_to_number_series(df_ob[apc]).fillna(0)
    mask = df["Status"].eq("COMPLETE")
    if not mask.any() and df["Order_ID_Key"].ne("").any(): mask = df["Order_ID_Key"].ne("")
    df["Is_Complete"] = mask
    df["Buy_Quantity"] = df["Quantity"].where(df["Is_Complete"] & df["Transaction_Type"].eq("BUY"), 0)
    df["Sell_Quantity"] = df["Quantity"].where(df["Is_Complete"] & df["Transaction_Type"].eq("SELL"), 0)
    df["Buy_Value"] = (df["Avg_Price"] * df["Quantity"]).where(df["Is_Complete"] & df["Transaction_Type"].eq("BUY"), 0)
    df["Sell_Value"] = (df["Avg_Price"] * df["Quantity"]).where(df["Is_Complete"] & df["Transaction_Type"].eq("SELL"), 0)
    df = df[df["UserID_Key"] != ""].copy()
    df.attrs["orderbook_order_id_set"] = ids
    df.attrs["orderbook_user_order_counts"] = cnts
    return df
def ord_read_summary_users(smf, sc: str) -> pd.DataFrame:
    smf.seek(0)
    df_sum = pd.read_excel(smf, sheet_name="Users")
    uc = ord_find_user_column(df_sum)
    if not uc: raise ValueError('Could not find "UserID" in Summary Users sheet.')
    ac, srv, alg, toc, mc, lcc, alc, lts, dtc = ord_find_alias_column(df_sum), ord_find_server_column(df_sum), ord_find_algo_column(df_sum), ord_find_total_orders_column(df_sum), ord_find_mtm_column(df_sum), ord_find_max_loss_column(df_sum), ord_find_allocation_column(df_sum), ord_find_total_lots_column(df_sum), ord_find_date_column(df_sum)
    df = pd.DataFrame()
    df["Detected_Server"] = [sc]*len(df_sum)
    df["Source_Summary"] = [smf.name]*len(df_sum)
    df["UserID"] = df_sum[uc].astype(str).str.strip()
    df["UserID_Key"] = df["UserID"].apply(ord_normalize_user_id)
    df["Alias"] = df_sum[ac].astype(str).str.strip() if ac else ""
    df["SERVER"] = df_sum[srv].astype(str).str.strip() if srv else sc
    df["ALGO"] = df_sum[alg].astype(str).str.strip() if alg else ""
    df["Total Orders"] = ord_to_number_series(df_sum[toc]).fillna(0) if toc else 0
    df["Summary_MTM"] = ord_to_number_series(df_sum[mc]) if mc else pd.NA
    df["Summary_Max_Loss"] = ord_to_number_series(df_sum[lcc]) if lcc else pd.NA
    df["Allocation"] = ord_to_number_series(df_sum[alc]).fillna(0) if alc else 0
    df["Allocation_Percent"] = df["Allocation"].fillna(0) * 100
    df["Total_Lots"] = ord_to_number_series(df_sum[lts]).fillna(0) if lts else 0
    df["Summary_Date"] = df_sum[dtc].astype(str).str.strip() if dtc else ""
    df = df[df["UserID_Key"] != ""].copy()
    if "Summary_MTM" in df.columns and df["Summary_MTM"].notna().any(): return df
    fallback = ord_read_summary_mtm_from_sheets(smf)
    if not fallback.empty:
        df = df.drop(columns=["Summary_MTM"], errors="ignore").merge(fallback, on="UserID_Key", how="left")
    else: df["Summary_MTM"] = pd.NA
    return df
def ord_apply_mtm_status(d):
    if pd.isna(d): return "Missing"
    return "OK" if abs(d)==0 else "V.Not OK" if abs(d)>2000 else "Not OK"
def ord_values_match_by_abs(l, r) -> bool:
    if pd.isna(l) or pd.isna(r): return False
    return abs(abs(float(l)) - abs(float(r))) < 0.01
def ord_prepare_output_df(df) -> pd.DataFrame:
    if df.empty: return df.copy()
    output = df.copy()
    if {"Allocation", "Allocation_Percent"}.issubset(output.columns):
        alloc = pd.to_numeric(output["Allocation"], errors="coerce")
        ap = pd.to_numeric(output["Allocation_Percent"], errors="coerce")
        output["Allocation_Percent"] = (ap / alloc.replace(0, pd.NA)) / 100
    return output.drop(columns=["Source_Orderbook", "Source_Summary", "Source_Save_MTM", "UserID_Key", "Allocation"], errors="ignore")
DOWNLOAD_EXCLUDED_COLS = ["Summary_Max_Loss", "Save_Max_Loss", "Max_Loss_Diff", "Max_Loss_Match"]
def ord_prepare_download_df(df) -> pd.DataFrame: return df.drop(columns=DOWNLOAD_EXCLUDED_COLS, errors="ignore").copy()
def ord_append_missing_multileg_flags(f_df, mmo_df) -> pd.DataFrame:
    out = f_df.copy()
    out["Issue"] = ""
    if mmo_df.empty: return out
    flag = mmo_df.copy()
    flag["ALGO"] = "MultiLeg Orders"
    flag["Orders_Match"] = False; flag["Orders_Diff"] = pd.NA; flag["userid_in_orderbook"] = False
    for c in out.columns:
        if c not in flag.columns: flag[c] = pd.NA
    ec = [c for c in ["Portfolio_Name", "Summary_Order_ID", "Summary_Status"] if c in flag.columns and c not in out.columns]
    return pd.concat([out, flag[list(out.columns) + ec]], ignore_index=True)
def ord_build_analysis_df(obr, sumr, reconr, sc: str, obn: str, sumn: str) -> pd.DataFrame:
    agg = obr.groupby("UserID_Key", as_index=False).agg(
        Orderbook_UserID=("UserID", "first"), Orderbook_Alias=("User_Alias", "first"),
        Buy_Quantity=("Buy_Quantity", "sum"), Sell_Quantity=("Sell_Quantity", "sum"),
        Buy_Value=("Buy_Value", "sum"), Sell_Value=("Sell_Value", "sum"), Completed_Order_Count=("Is_Complete", "sum")
    )
    df = sumr.merge(agg, on="UserID_Key", how="outer")
    df["UserID"] = df["UserID"].fillna(df["Orderbook_UserID"])
    df["Alias"] = df["Alias"].fillna(df["Orderbook_Alias"])
    df["SERVER"] = df["SERVER"].fillna(sc)
    df["ALGO"] = df["ALGO"].fillna("")
    df["Total Orders"] = pd.to_numeric(df["Total Orders"], errors="coerce").fillna(0)
    df["Allocation"] = pd.to_numeric(df["Allocation"], errors="coerce").fillna(0)
    df["Allocation_Percent"] = pd.to_numeric(df["Allocation_Percent"], errors="coerce").fillna(df["Allocation"] * 100)
    df["Buy_Quantity"] = pd.to_numeric(df["Buy_Quantity"], errors="coerce").fillna(0)
    df["Sell_Quantity"] = pd.to_numeric(df["Sell_Quantity"], errors="coerce").fillna(0)
    df["Buy_Value"] = pd.to_numeric(df["Buy_Value"], errors="coerce").fillna(0)
    df["Sell_Value"] = pd.to_numeric(df["Sell_Value"], errors="coerce").fillna(0)
    df["Completed_Order_Count"] = pd.to_numeric(df["Completed_Order_Count"], errors="coerce").fillna(0)
    df["PnL"] = df["Sell_Value"] - df["Buy_Value"]
    df["PnL_Percent"] = df["Allocation_Percent"]
    df["Detected_Server"] = sc; df["Source_Orderbook"] = obn; df["Source_Summary"] = sumn
    if not reconr.empty:
        df = df.merge(reconr[["UserID_Key", "Save_MTM", "MTM_Diff", "MTM_Status"]].drop_duplicates(subset=["UserID_Key"]), on="UserID_Key", how="left")
    else: df["Save_MTM"] = pd.NA; df["MTM_Diff"] = pd.NA; df["MTM_Status"] = ""
    cols = ["Detected_Server", "Source_Orderbook", "Source_Summary", "SERVER", "UserID", "Alias", "ALGO", "Buy_Quantity", "Sell_Quantity", "Buy_Value", "Sell_Value", "PnL", "Allocation", "PnL_Percent", "Completed_Order_Count", "Total Orders", "Summary_MTM", "Summary_Max_Loss", "Save_MTM", "Save_Max_Loss", "Max_Loss_Diff", "Max_Loss_Match", "MTM_Diff", "MTM_Status"]
    return df[[c for c in cols if c in df.columns]].copy()
def ord_process_pair(obf, sumf, smf, sc: str):
    sumr = ord_read_summary_users(sumf, sc)
    suids = set(sumr["UserID_Key"].dropna().astype(str))
    obr = ord_read_orderbook_rows(obf, sc, suids)
    smo = ord_read_summary_multileg_complete_orders(sumf, sc)
    p_cnt = ord_build_portfolio_count_df(smo)
    m_cnt = ord_build_multileg_user_count_df(smo)
    coc = obr.attrs.get("orderbook_user_order_counts")
    if coc is None or coc.empty: coc = obr[obr["Is_Complete"]].groupby("UserID_Key").size().rename("Total_orders_in_orderbook")
    recon = sumr.copy().merge(coc, left_on="UserID_Key", right_index=True, how="left").merge(m_cnt, on="UserID_Key", how="left")
    recon["Total_orders_in_orderbook"] = recon["Total_orders_in_orderbook"].fillna(0).astype(int)
    recon["Complete_MultiLeg_Count"] = recon["Complete_MultiLeg_Count"].fillna(0).astype(int)
    recon["Orders_Match"] = recon["Total Orders"] == recon["Total_orders_in_orderbook"]
    recon["Orders_Diff"] = recon["Total Orders"] - recon["Total_orders_in_orderbook"]
    recon["userid_in_orderbook"] = recon["UserID_Key"].isin(obr["UserID_Key"].unique())
    if smf:
        mtm = ord_read_save_mtm_file(smf)
        recon = recon.merge(mtm, on="UserID_Key", how="left")
        recon["MTM_Diff"] = recon["Summary_MTM"] - recon["Save_MTM"]
        recon["MTM_Status"] = recon["MTM_Diff"].apply(ord_apply_mtm_status)
        recon["userid_in_save_mtm"] = recon["Save_MTM"].notna()
        recon["Max_Loss_Match"] = recon.apply(lambda r: ord_values_match_by_abs(r.get("Summary_Max_Loss"), r.get("Save_Max_Loss")), axis=1)
        recon["Max_Loss_Diff"] = recon["Summary_Max_Loss"].abs() - recon["Save_Max_Loss"].abs()
        recon["Source_Save_MTM"] = smf.name
    else:
        recon["Save_MTM"] = pd.NA; recon["Save_Max_Loss"] = pd.NA; recon["Max_Loss_Diff"] = pd.NA; recon["Max_Loss_Match"] = pd.NA
        recon["MTM_Diff"] = pd.NA; recon["MTM_Status"] = "Save MTM not uploaded"; recon["userid_in_save_mtm"] = False; recon["Source_Save_MTM"] = ""
    recon["Source_Orderbook"] = obf.name; recon["Source_Summary"] = sumf.name; recon["Detected_Server"] = sc
    adf = ord_build_analysis_df(obr, sumr, recon, sc, obf.name, sumf.name)
    ob_oids = obr.attrs.get("orderbook_order_id_set", set())
    if not ob_oids: ob_oids = set(obr["Order_ID_Key"].dropna().unique())
    mmo = pd.DataFrame()
    if not smo.empty:
        mmo = smo[~smo["Order_ID_Key"].isin(ob_oids)].copy()
        mmo["Source_Orderbook"] = obf.name; mmo["Issue"] = "Complete MultiLeg Order ID missing in Orderbook"
        mmo = mmo[["Detected_Server", "Source_Summary", "Source_Orderbook", "UserID", "Alias", "Portfolio_Name", "Summary_Order_ID", "Summary_Status", "Issue"]]
    mmo_sum = ord_build_multileg_check_summary(smo, mmo, sc, sumf.name, obf.name)
    return {"reconciliation": recon, "analysis": adf, "missing_multileg_orders": mmo, "multileg_check_summary": mmo_sum, "portfolio_counts": p_cnt, "orderbook_rows": obr, "summary_rows": sumr}
def ord_build_processing_bundle(ob_files, sum_files, sm_files):
    if not ob_files or not sum_files: raise ValueError("Please upload both Orderbook and Summary files.")
    ob_m = {ord_extract_server_code(f.name): f for f in ob_files}
    sum_m = {ord_extract_server_code(f.name): f for f in sum_files}
    sm_m = {ord_extract_server_code(f.name): f for f in sm_files} if sm_files else {}
    c_mtm = sm_m.get("UNKNOWN")
    codes = (set(ob_m.keys()) | set(sum_m.keys())) - {"UNKNOWN"}
    pairs, u_ob, u_sum = [], [], []
    for c in sorted(codes):
        obf, sumf, smf = ob_m.get(c), sum_m.get(c), sm_m.get(c) or c_mtm
        if obf and sumf: pairs.append((obf, sumf, smf, c))
        elif obf: u_ob.append(obf.name)
        elif sumf: u_sum.append(sumf.name)
    if not pairs: raise ValueError("No matches found. Check that file names contain VS1, VS8, VS19 and similar server codes.")
    rf, af, mlf, mmf, mcf, pcf, errors = [], [], [], [], [], [], []
    for obf, sumf, smf, c in pairs:
        try:
            res = ord_process_pair(obf, sumf, smf, c)
            rf.append(res["reconciliation"]); af.append(res["analysis"]); mlf.append(res["reconciliation"])
            mmf.append(res["missing_multileg_orders"]); mcf.append(res["multileg_check_summary"]); pcf.append(res["portfolio_counts"])
        except Exception as e: errors.append(f"{c}: {e}")
    if not rf: raise ValueError("No files could be processed.")
    f_df = pd.concat(rf, ignore_index=True)
    a_df = pd.concat(af, ignore_index=True) if af else pd.DataFrame()
    ml_df = pd.concat(mlf, ignore_index=True) if mlf else pd.DataFrame()
    mmo_df = pd.concat(mmf, ignore_index=True) if mmf else pd.DataFrame()
    p_df = pd.concat(pcf, ignore_index=True) if pcf else pd.DataFrame()
    mc_df = pd.concat(mcf, ignore_index=True) if mcf else pd.DataFrame()
    cols = ["Detected_Server", "Source_Orderbook", "Source_Summary", "Source_Save_MTM", "SERVER", "UserID", "Alias", "ALGO", "Total Orders", "Complete_MultiLeg_Count", "Total_orders_in_orderbook", "Orders_Match", "Orders_Diff", "userid_in_orderbook", "Summary_MTM", "Save_MTM", "Summary_Max_Loss", "Save_Max_Loss", "Max_Loss_Diff", "Max_Loss_Match", "MTM_Diff", "MTM_Status", "userid_in_save_mtm", "Allocation", "Allocation_Percent"]
    f_df = f_df[[col for col in cols if col in f_df.columns] + [col for col in f_df.columns if col not in cols]]
    if "Max_Loss_Match" in ml_df.columns: ml_df = ml_df[ml_df["Max_Loss_Match"].eq(False)].copy()
    return {"matched_pairs": pairs, "unmatched_orderbooks": u_ob, "unmatched_summaries": u_sum, "errors": errors, "reconciliation_df": ord_prepare_output_df(f_df), "analysis_df": ord_prepare_output_df(a_df), "max_loss_mismatch_df": ord_prepare_output_df(ml_df), "missing_multileg_orders_df": mmo_df, "multileg_check_summary_df": mc_df, "portfolio_counts_df": p_df}
def ord_render_processing_bundle(bundle):
    st.write(f"**Found {len(bundle['matched_pairs'])} matched server(s)**")
    for obf, sumf, smf, c in bundle["matched_pairs"]:
        st.success(f"{c}: {obf.name} <-> {sumf.name} <-> {smf.name if smf else 'No Save MTM file'}")
    if bundle["unmatched_orderbooks"]: st.warning(f"No Summary found for: {', '.join(bundle['unmatched_orderbooks'])}")
    if bundle["unmatched_summaries"]: st.warning(f"No Orderbook found for: {', '.join(bundle['unmatched_summaries'])}")
    if bundle["errors"]:
        for err in bundle["errors"]: st.error(f"Error processing {err}")
    f_df, a_df, ml_df, mmo_df, mc_df, p_df = bundle["reconciliation_df"], bundle["analysis_df"], bundle["max_loss_mismatch_df"], bundle["missing_multileg_orders_df"], bundle["multileg_check_summary_df"], bundle["portfolio_counts_df"]
    st.success(f"Processed {len(bundle['matched_pairs'])} servers and {len(f_df):,} reconciliation rows")
    diff = f_df.copy()
    mask = pd.Series(False, index=diff.index)
    if "Orders_Diff" in diff: mask |= pd.to_numeric(diff["Orders_Diff"], errors="coerce").fillna(0).ne(0)
    if "MTM_Status" in diff: mask |= ~diff["MTM_Status"].fillna("").isin(["", "OK", "Save MTM not uploaded"])
    if "Max_Loss_Match" in diff: mask |= diff["Max_Loss_Match"].eq(False)
    diff = diff[mask].copy()
    dcols = ["Detected_Server", "SERVER", "UserID", "Alias", "ALGO", "Total Orders", "Complete_MultiLeg_Count", "Total_orders_in_orderbook", "Orders_Diff", "Summary_MTM", "Save_MTM", "MTM_Diff", "MTM_Status"]
    st.subheader("Differences Only")
    if diff.empty: st.success("No differences found.")
    else: st.write(f"**Found {len(diff):,} difference row(s)**"); st.dataframe(ord_prepare_download_df(diff[[c for c in dcols if c in diff.columns]]), width="stretch")
    if not mc_df.empty: st.subheader("MultiLeg Order ID Check"); st.dataframe(mc_df, width="stretch")
    if not mmo_df.empty:
        st.subheader("MultiLeg Order IDs Missing In Orderbook")
        st.write(f"**Found {len(mmo_df):,} missing complete MultiLeg order(s)**"); st.dataframe(mmo_df, width="stretch")
    elif not mc_df.empty and mc_df["Complete_MultiLeg_Order_IDs"].sum() > 0:
        st.subheader("MultiLeg Order IDs Missing In Orderbook"); st.success("All complete MultiLeg order IDs were found in the orderbook.")

    if not ml_df.empty:
        st.subheader("Max Loss Not Matched")
        st.write(f"**Found {len(ml_df):,} Max Loss mismatch row(s)**"); st.dataframe(ord_prepare_download_df(ml_df), width="stretch")
    df_dl = ord_append_missing_multileg_flags(ord_prepare_download_df(f_df), mmo_df)
    a_dl, ml_dl = ord_prepare_download_df(a_df), ord_prepare_download_df(ml_df)
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as wr:
        df_dl.to_excel(wr, sheet_name="Reconciliation", index=False)
        if not a_dl.empty: a_dl.to_excel(wr, sheet_name="View_Analysis", index=False)
        if not ml_dl.empty: ml_dl.to_excel(wr, sheet_name="Max_Loss_Not_Matched", index=False)
        if not mmo_df.empty: mmo_df.to_excel(wr, sheet_name="Missing_MultiLeg_Orders", index=False)
        if not mc_df.empty: mc_df.to_excel(wr, sheet_name="MultiLeg_Order_ID_Check", index=False)
        if not p_df.empty: p_df.to_excel(wr, sheet_name="Portfolio_Counts", index=False)
        for s in f_df["Detected_Server"].dropna().astype(str).unique():
            df_dl[df_dl["Detected_Server"] == s].to_excel(wr, sheet_name=str(s)[:31], index=False)
    out.seek(0)
    st.download_button("Download Excel Report", out.getvalue(), f"Reconciliation_Report_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="download_excel")
    st.download_button("Download Reconciliation CSV", df_dl.to_csv(index=False).encode(), "reconciliation_data.csv", "text/csv", key="download_csv")

class CheckerNamespace:
    EXPIRY_OPTIONS = EXPIRY_OPTIONS
    GOOGLE_SHEET_ID = "1XIQ9gx8YWqXCgYmHdftafzHPSvFPaAVl0tpdaWEQGKE"
    GOOGLE_SHEET_URL = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/edit"
    normalize_sheet_key = staticmethod(chk_normalize_sheet_key)
    clean_user_id = staticmethod(chk_clean_user_id)
    find_user_id_column = staticmethod(chk_find_user_id_column)
    find_column_by_name = staticmethod(chk_find_column_by_name)
    find_expiry_column = staticmethod(chk_find_expiry_column)
    clean_report_text = staticmethod(chk_clean_report_text)
    normalize_expiry_value = staticmethod(chk_normalize_expiry_value)
    load_csv_from_upload = staticmethod(chk_load_csv_from_upload)
    get_all_unique_users = staticmethod(chk_get_all_unique_users)
    get_summary_users = staticmethod(chk_get_summary_users)
    get_summary_algos = staticmethod(chk_get_summary_algos)
    extract_server = staticmethod(chk_extract_server)
    extract_date = staticmethod(chk_extract_date)
    format_date_parts = staticmethod(chk_format_date_parts)
    extract_correctable_date = staticmethod(chk_extract_correctable_date)
    build_corrected_filename = staticmethod(chk_build_corrected_filename)
    pick_expected_value = staticmethod(chk_pick_expected_value)
    format_filename_status_df = staticmethod(chk_format_filename_status_df)
    extract_position_suffix = staticmethod(chk_extract_position_suffix)
    validate_position_filename = staticmethod(chk_validate_position_filename)
    validate_all_filenames = staticmethod(chk_validate_all_filenames)
    parse_reference_file = staticmethod(chk_parse_reference_file)
    get_reference_users_for_server = staticmethod(chk_get_reference_users_for_server)
    validate_summary_sheets = staticmethod(chk_validate_summary_sheets)
    validate_expiry_sheets = staticmethod(chk_validate_expiry_sheets)
    extract_algo = staticmethod(chk_extract_algo)
    check_summary_self_contained = staticmethod(chk_check_summary_self_contained)
    build_validation_report_csv = staticmethod(chk_build_validation_report_csv)
    get_google_sheets_service = staticmethod(chk_get_google_sheets_service)
    get_first_sheet_title = staticmethod(chk_get_first_sheet_title)
    save_validation_report_to_google_sheet = staticmethod(chk_save_validation_report_to_google_sheet)

class OrderMatchNamespace:
    extract_server_code = staticmethod(ord_extract_server_code)
    clean_column_names = staticmethod(ord_clean_column_names)
    normalize_col_name = staticmethod(ord_normalize_col_name)
    normalize_user_id = staticmethod(ord_normalize_user_id)
    normalize_order_id = staticmethod(ord_normalize_order_id)
    to_number_series = staticmethod(ord_to_number_series)
    find_column = staticmethod(ord_find_column)
    find_user_column = staticmethod(ord_find_user_column)
    find_alias_column = staticmethod(ord_find_alias_column)
    find_mtm_column = staticmethod(ord_find_mtm_column)
    find_max_loss_column = staticmethod(ord_find_max_loss_column)
    find_orderbook_user_column = staticmethod(ord_find_orderbook_user_column)
    find_orderbook_status_column = staticmethod(ord_find_orderbook_status_column)
    find_status_column = staticmethod(ord_find_status_column)
    find_transaction_column = staticmethod(ord_find_transaction_column)
    find_quantity_column = staticmethod(ord_find_quantity_column)
    find_avg_price_column = staticmethod(ord_find_avg_price_column)
    find_symbol_column = staticmethod(ord_find_symbol_column)
    find_order_id_column = staticmethod(ord_find_order_id_column)
    find_orderbook_tag_column = staticmethod(ord_find_orderbook_tag_column)
    has_valid_order_ids = staticmethod(ord_has_valid_order_ids)
    extract_order_ids_from_frame = staticmethod(ord_extract_order_ids_from_frame)
    count_orderbook_orders_for_users = staticmethod(ord_count_orderbook_orders_for_users)
    find_order_time_column = staticmethod(ord_find_order_time_column)
    find_total_orders_column = staticmethod(ord_find_total_orders_column)
    find_server_column = staticmethod(ord_find_server_column)
    find_algo_column = staticmethod(ord_find_algo_column)
    find_portfolio_column = staticmethod(ord_find_portfolio_column)
    find_allocation_column = staticmethod(ord_find_allocation_column)
    find_total_lots_column = staticmethod(ord_find_total_lots_column)
    find_date_column = staticmethod(ord_find_date_column)
    read_save_mtm_file = staticmethod(ord_read_save_mtm_file)
    read_summary_mtm_from_sheets = staticmethod(ord_read_summary_mtm_from_sheets)
    read_summary_multileg_complete_orders = staticmethod(ord_read_summary_multileg_complete_orders)
    build_portfolio_count_df = staticmethod(ord_build_portfolio_count_df)
    build_multileg_user_count_df = staticmethod(ord_build_multileg_user_count_df)
    build_multileg_check_summary = staticmethod(ord_build_multileg_check_summary)
    read_orderbook_rows = staticmethod(ord_read_orderbook_rows)
    read_summary_users = staticmethod(ord_read_summary_users)
    apply_mtm_status = staticmethod(ord_apply_mtm_status)
    values_match_by_abs = staticmethod(ord_values_match_by_abs)
    prepare_output_df = staticmethod(ord_prepare_output_df)
    prepare_download_df = staticmethod(ord_prepare_download_df)
    append_missing_multileg_flags = staticmethod(ord_append_missing_multileg_flags)
    build_analysis_df = staticmethod(ord_build_analysis_df)
    process_pair = staticmethod(ord_process_pair)
    build_processing_bundle = staticmethod(ord_build_processing_bundle)
    render_processing_bundle = staticmethod(ord_render_processing_bundle)

checker = CheckerNamespace()
ordermatch = OrderMatchNamespace()



st.markdown(
    """
<style>
    section[data-testid="stSidebar"] {display: none !important;}
    .main .block-container {padding: 2rem !important; max-width: 100% !important;}
    .upload-summary {
        padding: 1rem;
        border: 1px solid rgba(148, 163, 184, 0.35);
        border-radius: 8px;
        background: rgba(248, 250, 252, 0.75);
        margin: 1rem 0;
    }
</style>
""",
    unsafe_allow_html=True,
)


def normalized_name(uploaded_file) -> str:
    return getattr(uploaded_file, "name", "").lower()


def is_excel(uploaded_file) -> bool:
    return normalized_name(uploaded_file).endswith((".xlsx", ".xls"))


def is_csv(uploaded_file) -> bool:
    return normalized_name(uploaded_file).endswith(".csv")


def is_image(uploaded_file) -> bool:
    return normalized_name(uploaded_file).endswith((".png", ".jpg", ".jpeg"))


def classify_files(uploaded_files):
    files = {
        "summary_files": [],
        "running_file": None,
        "position_file": None,
        "orderbook_files": [],
        "gridlog_file": None,
        "mtm_file": None,
        "save_mtm_files": [],
        "unclassified": [],
    }

    for uploaded_file in uploaded_files or []:
        name = normalized_name(uploaded_file)
        compact_name = name.replace(" ", "").replace("_", "").replace("-", "")

        if is_image(uploaded_file):
            if "mtm" in compact_name and files["mtm_file"] is None:
                files["mtm_file"] = uploaded_file
            else:
                files["unclassified"].append(uploaded_file)
            continue

        if "summary" in compact_name and is_excel(uploaded_file):
            files["summary_files"].append(uploaded_file)
        elif "running" in compact_name and is_csv(uploaded_file):
            files["running_file"] = files["running_file"] or uploaded_file
        elif "position" in compact_name and is_csv(uploaded_file):
            files["position_file"] = files["position_file"] or uploaded_file
        elif "orderbook" in compact_name and is_csv(uploaded_file):
            files["orderbook_files"].append(uploaded_file)
        elif "gridlog" in compact_name and is_csv(uploaded_file):
            files["gridlog_file"] = files["gridlog_file"] or uploaded_file
        elif "savemtm" in compact_name or ("mtm" in compact_name and not is_image(uploaded_file)):
            files["save_mtm_files"].append(uploaded_file)
        else:
            files["unclassified"].append(uploaded_file)

    if files["mtm_file"] is None and files["save_mtm_files"]:
        files["mtm_file"] = files["save_mtm_files"][0]

    return files


def rewind_uploads(*file_groups):
    for file_group in file_groups:
        if not file_group:
            continue
        if not isinstance(file_group, list):
            file_group = [file_group]
        for uploaded_file in file_group:
            if uploaded_file and hasattr(uploaded_file, "seek"):
                uploaded_file.seek(0)


def render_file_summary(files):
    rows = [
        ("Summary Excel", [file.name for file in files["summary_files"]]),
        ("Running Users CSV", [files["running_file"].name] if files["running_file"] else []),
        ("Position CSV", [files["position_file"].name] if files["position_file"] else []),
        ("Orderbook CSV", [file.name for file in files["orderbook_files"]]),
        ("Gridlog CSV", [files["gridlog_file"].name] if files["gridlog_file"] else []),
        ("MTM Filename Check", [files["mtm_file"].name] if files["mtm_file"] else []),
        ("Save MTM", [file.name for file in files["save_mtm_files"]]),
        ("Unclassified", [file.name for file in files["unclassified"]]),
    ]
    summary_df = pd.DataFrame(
        [{"Input": label, "Detected Files": ", ".join(names) if names else "-"} for label, names in rows]
    )
    st.markdown('<div class="upload-summary">', unsafe_allow_html=True)
    st.dataframe(summary_df, width="stretch", hide_index=True)
    st.markdown("</div>", unsafe_allow_html=True)


def format_filename_status_df(file_status):
    if hasattr(checker, "format_filename_status_df"):
        return checker.format_filename_status_df(file_status)

    df = pd.DataFrame(file_status)
    if df.empty:
        return df
    if "Corrected Filename" not in df.columns:
        df["Corrected Filename"] = df.get("Correct Name", "")
    df["Corrected Filename"] = df.apply(
        lambda row: row.get("Corrected Filename", "") if row.get("Status") == "Issue" else "-",
        axis=1,
    )
    preferred_cols = [
        "File Type",
        "Filename",
        "Corrected Filename",
        "Server",
        "Date",
        "Format",
        "Status",
        "Issues",
        "EOD Check",
    ]
    return df[[col for col in preferred_cols if col in df.columns]]


def render_summary_checker(files, selected_server, selected_expiry):
    st.header("Summary Checker Result")

    if not files["summary_files"]:
        st.warning("Summary Checker needs at least one Summary Excel file.")
        return
    if not files["running_file"]:
        st.warning("Summary Checker needs the Running Users CSV.")
        return

    rewind_uploads(
        files["summary_files"],
        files["running_file"],
        files["position_file"],
        files["orderbook_files"],
        files["gridlog_file"],
        files["mtm_file"],
    )

    name_check = checker.validate_all_filenames(
        files["summary_files"],
        files["position_file"],
        files["orderbook_files"][0] if files["orderbook_files"] else None,
        files["gridlog_file"],
        files["mtm_file"],
    )

    st.subheader("Filename Format Check")
    st.dataframe(format_filename_status_df(name_check["file_status"]), width="stretch", hide_index=True)

    if name_check["issues"]:
        for issue in name_check["issues"]:
            st.error(issue)
        return

    st.success("All filename checks passed.")

    rewind_uploads(files["running_file"], files["summary_files"])
    ref_lookup = checker.parse_reference_file(files["running_file"])
    if not ref_lookup:
        st.error("Running Users file does not contain a valid user ID column or server data.")
        return

    rewind_uploads(files["summary_files"])
    all_results = checker.check_summary_self_contained(
        files["summary_files"],
        ref_lookup,
        selected_server,
        selected_expiry,
    )
    report_csv = checker.build_validation_report_csv(name_check, all_results)

    st.download_button(
        label="Download Summary Validation CSV",
        data=report_csv,
        file_name="summary_validation_report.csv",
        mime="text/csv",
        key="unified_download_summary_validation_csv",
    )
    google_credentials_file = st.file_uploader(
        "Google service account JSON",
        type=["json"],
        key="unified_google_sheet_credentials_json",
        help="Share the target Google Sheet with the service account email, then upload its JSON key here.",
    )
    if st.button("Save Summary Validation CSV to Google Sheet", type="secondary", width="stretch"):
        try:
            with st.spinner("Saving report to Google Sheet..."):
                sheet_url = checker.save_validation_report_to_google_sheet(
                    report_csv,
                    credentials_file=google_credentials_file,
                )
            st.success("Summary validation CSV saved to Google Sheet.")
            st.link_button("Open Google Sheet", sheet_url)
        except Exception as exc:
            st.error(str(exc))

    for result in all_results:
        with st.expander(f"{result['File']} | {result['Total Issues']} Issues", expanded=True):
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Sheets", result["Sheets"])
            c2.metric("Users in Summary", result["Total Unique Users"])
            c3.metric("Users in Running", result["Running Users (Same Server)"])
            c4.metric("ALGO", result.get("ALGO", result.get("Algo", "N/A")))
            c5.metric("Status", "OK" if result["Total Issues"] == 0 else "Issues")
            st.caption(f"Server: {result['Server']} | Expiry selected: {result.get('Selected Expiry', '')}")

            for issue in result.get("Expiry Issues", []):
                st.error(issue)

            if result["missing_in_summary"]:
                st.error(
                    "Missing in Summary: "
                    + ", ".join(result["missing_in_summary"][:100])
                    + ("..." if len(result["missing_in_summary"]) > 100 else "")
                )
            if result["extra_in_summary"]:
                st.warning(
                    "Extra in Summary: "
                    + ", ".join(result["extra_in_summary"][:100])
                    + ("..." if len(result["extra_in_summary"]) > 100 else "")
                )

            sheet_data = []
            for sheet, data in result["sheet_results"].items():
                sheet_data.append(
                    {
                        "Sheet": sheet,
                        "Status": data["status"],
                        "Expiry Status": data.get("expiry_status", "-"),
                        "Expiry Mismatch": ", ".join(data.get("expiry_mismatches", [])[:15])
                        + ("..." if len(data.get("expiry_mismatches", [])) > 15 else "")
                        if data.get("expiry_mismatches")
                        else "-",
                        "Missing": ", ".join(data["missing"][:15])
                        + ("..." if len(data["missing"]) > 15 else "")
                        if data["missing"]
                        else "-",
                        "Extra": ", ".join(data["extra"][:15])
                        + ("..." if len(data["extra"]) > 15 else "")
                        if data["extra"]
                        else "-",
                    }
                )
            st.dataframe(pd.DataFrame(sheet_data), width="stretch", hide_index=True)


def render_orderbook_matcher(files):
    st.header("Orderbook Matcher Result")

    if not files["orderbook_files"] or not files["summary_files"]:
        st.warning("Orderbook Matcher needs Orderbook CSV file(s) and Summary Excel file(s).")
        return

    rewind_uploads(files["orderbook_files"], files["summary_files"], files["save_mtm_files"])
    bundle = ordermatch.build_processing_bundle(
        files["orderbook_files"],
        files["summary_files"],
        files["save_mtm_files"],
    )
    ordermatch.render_processing_bundle(bundle)


st.title("Unified Recon")
st.caption("Upload all required files once. The app detects each file and runs Summary Checker plus Orderbook Matcher.")

col_back, col_space = st.columns([1, 5])
with col_back:
    if st.button("<- Dashboard", key="unified_back_to_home"):
        st.switch_page("streamlit_app.py")

uploaded_files = st.file_uploader(
    "Upload Summary, Running Users, Position, Orderbook, Gridlog, MTM image, and Save MTM files",
    type=["csv", "xlsx", "xls", "png", "jpg", "jpeg"],
    accept_multiple_files=True,
    key="unified_all_files",
)

files = classify_files(uploaded_files)
if uploaded_files:
    render_file_summary(files)

selected_server = None
selected_expiry_default = st.session_state.get("selected_expiry", checker.EXPIRY_OPTIONS[0])
if selected_expiry_default not in checker.EXPIRY_OPTIONS:
    selected_expiry_default = checker.EXPIRY_OPTIONS[0]

selected_expiry = st.selectbox(
    "Choose expiry to validate",
    checker.EXPIRY_OPTIONS,
    index=checker.EXPIRY_OPTIONS.index(selected_expiry_default),
    key="selected_expiry",
)
if files["running_file"]:
    rewind_uploads(files["running_file"])
    running_df = checker.load_csv_from_upload(files["running_file"])
    if running_df is None:
        st.error("Unable to read the Running Users CSV.")
    else:
        server_col = checker.find_column_by_name(running_df, "server")
        if server_col:
            server_options = sorted(running_df[server_col].dropna().astype(str).unique().tolist())
            if server_options:
                selected_server = st.selectbox(
                    "Choose server to validate against",
                    ["Auto Detect"] + server_options,
                    index=0,
                    key="unified_selected_server",
                )
        else:
            selected_server = st.text_input(
                "Enter server code for validation",
                value="VS01",
                key="unified_selected_server_text",
            )

run_all = st.button("Run Summary + Order Match", type="primary", width="stretch", key="unified_run_all")

if run_all:
    with st.spinner("Running unified recon..."):
        try:
            render_summary_checker(files, selected_server, selected_expiry)
        except Exception as exc:
            st.error(f"Summary Checker failed: {exc}")

        st.divider()

        try:
            render_orderbook_matcher(files)
        except Exception as exc:
            st.error(f"Orderbook Matcher failed: {exc}")
elif not uploaded_files:
    st.info("Upload the files once, then run the unified result.")
