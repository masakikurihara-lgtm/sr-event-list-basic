import streamlit as st
import requests
from datetime import datetime, timedelta
import time
import pytz
import pandas as pd
import io
import re
import ftplib
import concurrent.futures
import streamlit.components.v1 as components
import base64


# 日本時間(JST)
JST = pytz.timezone('Asia/Tokyo')

# --- 定数定義 ---
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

# ❌ event/search API は今後使わない
# API_EVENT_SEARCH_URL = "https://www.showroom-live.com/api/event/search"

API_EVENT_ROOM_LIST_URL = "https://www.showroom-live.com/api/event/room_list"
EVENT_PAGE_BASE_URL = "https://www.showroom-live.com/event/"

# ★ 固定CSV（イベント取得元）
EVENT_ARCHIVE_CSV_URL = "https://mksoul-pro.com/showroom/file/sr-event-archive.csv"


# ===============================
# 共通CSS（変更なし）
# ===============================
st.markdown("""<style>
table { width:100%; border-collapse:collapse; font-size:14px; }
.rank-btn-link { background:#0b57d0; color:white!important; padding:4px 8px;
 border-radius:4px; text-decoration:none; font-size:12px; }
.table-wrapper { overflow-x:auto; border:1px solid #ddd; border-radius:6px; }
@media screen and (max-width:1024px){
 table{font-size:12px!important;}
 .table-wrapper table{width:1080px!important;}
}
</style>""", unsafe_allow_html=True)


# ===============================
# event_id 正規化
# ===============================
def normalize_event_id_val(val):
    if val is None:
        return None
    try:
        if isinstance(val, int):
            return str(val)
        if isinstance(val, float):
            return str(int(val)) if val.is_integer() else str(val)
        s = str(val).strip()
        if re.match(r'^\d+(\.0+)?$', s):
            return str(int(float(s)))
        return s if s else None
    except Exception:
        return None


# ===============================
# FTP（変更なし）
# ===============================
def ftp_upload(file_path, content_bytes):
    ftp = ftplib.FTP(
        st.secrets["ftp"]["host"],
        st.secrets["ftp"]["user"],
        st.secrets["ftp"]["password"]
    )
    with ftp:
        with io.BytesIO(content_bytes) as f:
            ftp.storbinary(f"STOR {file_path}", f)


def ftp_download(file_path):
    ftp = ftplib.FTP(
        st.secrets["ftp"]["host"],
        st.secrets["ftp"]["user"],
        st.secrets["ftp"]["password"]
    )
    with ftp:
        buf = io.BytesIO()
        try:
            ftp.retrbinary(f"RETR {file_path}", buf.write)
            return buf.getvalue().decode("utf-8-sig")
        except Exception:
            return None


# ===============================
# ★ イベント取得（CSV駆動）
# ===============================
@st.cache_data(ttl=600)
def get_events(statuses):
    """
    API互換形式でイベントdictを返す（UI側無変更）
    statuses: [1=開催中,3=開催予定,4=終了]
    """
    df = pd.read_csv(EVENT_ARCHIVE_CSV_URL, dtype=str)

    df["event_id"] = df["event_id"].apply(normalize_event_id_val)
    df["started_at"] = pd.to_numeric(df["started_at"], errors="coerce")
    df["ended_at"] = pd.to_numeric(df["ended_at"], errors="coerce")
    df["is_entry_scope_inner"] = df["is_entry_scope_inner"].astype(str).str.lower() == "true"

    df.dropna(subset=["event_id", "started_at", "ended_at"], inplace=True)

    now_ts = int(datetime.now(JST).timestamp())
    events = []

    for _, r in df.iterrows():
        if r["started_at"] > now_ts:
            status = 3
        elif r["ended_at"] < now_ts:
            status = 4
        else:
            status = 1

        if status not in statuses:
            continue

        events.append({
            "event_id": r["event_id"],
            "event_name": r["event_name"],
            "event_url_key": r["event_url_key"],
            "is_entry_scope_inner": r["is_entry_scope_inner"],
            "started_at": int(r["started_at"]),
            "ended_at": int(r["ended_at"]),
            "is_event_block": r.get("is_event_block"),
            "image_m": r.get("image_m"),
            "show_ranking": r.get("show_ranking"),
            "_fetched_status": status
        })

    return events


# ===============================
# BU用（既存ロジックそのまま）
# ===============================
@st.cache_data(ttl=600)
def get_past_events_from_files():
    df = pd.read_csv(EVENT_ARCHIVE_CSV_URL, dtype=str)

    df["event_id"] = df["event_id"].apply(normalize_event_id_val)
    df["started_at"] = pd.to_numeric(df["started_at"], errors="coerce")
    df["ended_at"] = pd.to_numeric(df["ended_at"], errors="coerce")
    df["is_entry_scope_inner"] = df["is_entry_scope_inner"].astype(str).str.lower() == "true"

    df.dropna(subset=["event_id", "started_at", "ended_at"], inplace=True)

    now_ts = int(datetime.now(JST).timestamp())
    df = df[df["ended_at"] < now_ts]
    df.sort_values("ended_at", ascending=False, inplace=True)

    return df.to_dict("records")


# ===============================
# 参加ルーム数（変更なし）
# ===============================
def get_total_entries(event_id):
    try:
        res = requests.get(
            API_EVENT_ROOM_LIST_URL,
            headers=HEADERS,
            params={"event_id": event_id},
            timeout=10
        )
        if res.status_code == 404:
            return 0
        return res.json().get("total_entries", 0)
    except Exception:
        return "N/A"


# ===============================
# メイン（UI完全維持）
# ===============================
def main():
    st.set_page_config(
        page_title="SHOWROOM イベント一覧",
        page_icon="🎤",
        layout="wide"
    )

    st.markdown("<h1 style='font-size:28px;'>🎤 SHOWROOM イベント一覧</h1>", unsafe_allow_html=True)

    # --- フィルタ ---
    st.sidebar.header("表示フィルタ")
    use_on_going = st.sidebar.checkbox("開催中", value=False)
    use_upcoming = st.sidebar.checkbox("開催予定", value=False)
    use_finished = st.sidebar.checkbox("終了", value=False)
    use_past_bu = st.sidebar.checkbox("終了(BU)", value=False)

    statuses = []
    if use_on_going: statuses.append(1)
    if use_upcoming: statuses.append(3)
    if use_finished: statuses.append(4)

    unique_events = {}

    if statuses:
        for e in get_events(statuses):
            unique_events[e["event_id"]] = e

    if use_past_bu:
        for e in get_past_events_from_files():
            eid = e["event_id"]
            if eid not in unique_events:
                unique_events[eid] = e

    # 特定イベント除外（変更なし）
    events = [e for e in unique_events.values() if e["event_id"] != "12151"]

    if not events:
        st.info("該当イベントはありません。")
        return

    # ===============================
    # 一覧表示 & CSV（完全そのまま）
    # ===============================
    st.markdown("##### 📋 一覧表示")

    download_rows = []
    for e in events:
        download_rows.append({
            "イベント名": e["event_name"],
            "対象": "対象者限定" if e.get("is_entry_scope_inner") else "全ライバー",
            "開始": datetime.fromtimestamp(e["started_at"], JST).strftime("%Y/%m/%d %H:%M"),
            "終了": datetime.fromtimestamp(e["ended_at"], JST).strftime("%Y/%m/%d %H:%M"),
            "参加ルーム数": get_total_entries(e["event_id"])
        })

    df_dl = pd.DataFrame(download_rows)
    csv = df_dl.to_csv(index=False, encoding="utf-8-sig")
    b64 = base64.b64encode(csv.encode()).decode()

    html = f"""
    <div class="table-wrapper">
    <table>
      <thead>
        <tr>
          <th>イベント名</th><th>対象</th><th>開始</th><th>終了</th><th>参加ルーム数</th>
        </tr>
      </thead>
      <tbody>
    """

    for e in events:
        html += f"""
        <tr>
          <td><a href="{EVENT_PAGE_BASE_URL}{e['event_url_key']}" target="_blank">{e['event_name']}</a></td>
          <td>{"対象者限定" if e.get("is_entry_scope_inner") else "全ライバー"}</td>
          <td>{datetime.fromtimestamp(e["started_at"], JST).strftime("%Y/%m/%d %H:%M")}</td>
          <td>{datetime.fromtimestamp(e["ended_at"], JST).strftime("%Y/%m/%d %H:%M")}</td>
          <td>{get_total_entries(e["event_id"])}</td>
        </tr>
        """

    html += f"""
      </tbody>
    </table>
    </div>
    <a class="rank-btn-link" href="data:text/csv;base64,{b64}" download="event_list.csv">
      📊 この内容をCSVでダウンロード
    </a>
    """

    components.html(html, height=800, scrolling=False)


if __name__ == "__main__":
    main()
