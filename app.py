import streamlit as st
import requests
import pandas as pd
import datetime
import base64
from io import StringIO

# =========================
# 定数・設定
# =========================

JST = datetime.timezone(datetime.timedelta(hours=9))

ARCHIVE_CSV_URL = "https://mksoul-pro.com/showroom/file/sr-event-archive.csv"
ROOM_LIST_API_URL = "https://www.showroom-live.com/api/event/room_list"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

FINISHED_LIMIT_DAYS = 14
EXCLUDE_EVENT_ID = "12151"

# =========================
# ユーティリティ
# =========================

def normalize_event_id_val(val):
    if pd.isna(val):
        return None
    try:
        return str(int(float(val)))
    except Exception:
        return str(val).strip()


def format_datetime(ts):
    try:
        return datetime.datetime.fromtimestamp(
            int(ts), JST
        ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


# =========================
# イベント取得（CSV）
# =========================

@st.cache_data(ttl=600)
def get_all_events_from_archive():
    df = pd.read_csv(ARCHIVE_CSV_URL, dtype=str)

    df["event_id"] = df["event_id"].apply(normalize_event_id_val)
    df["started_at"] = pd.to_numeric(df["started_at"], errors="coerce")
    df["ended_at"] = pd.to_numeric(df["ended_at"], errors="coerce")
    df["is_entry_scope_inner"] = (
        df["is_entry_scope_inner"].astype(str).str.lower() == "true"
    )

    df.dropna(subset=["event_id", "started_at", "ended_at"], inplace=True)
    df.drop_duplicates(subset=["event_id"], keep="last", inplace=True)

    return df.to_dict("records")


def get_event_status(event, now_ts):
    if event["started_at"] > now_ts:
        return "upcoming"
    elif event["ended_at"] < now_ts:
        return "finished"
    else:
        return "ongoing"


def is_within_finished_limit(event, now_ts):
    return (now_ts - event["ended_at"]) <= FINISHED_LIMIT_DAYS * 86400


# =========================
# 参加ルーム数取得
# =========================

@st.cache_data(ttl=300)
def get_total_entries(event_id):
    try:
        res = requests.get(
            ROOM_LIST_API_URL,
            params={"event_id": event_id},
            headers=HEADERS,
            timeout=10
        )
        if res.status_code == 404:
            return 0
        res.raise_for_status()
        data = res.json()
        return int(data.get("total_entries", 0))
    except Exception:
        return "N/A"


# =========================
# メイン
# =========================

def main():
    st.set_page_config(
        page_title="SHOWROOM イベント一覧",
        layout="wide"
    )

    st.title("SHOWROOM イベント一覧")

    # -------------------------
    # サイドバー
    # -------------------------

    st.sidebar.header("表示フィルタ")

    use_on_going = st.sidebar.checkbox("開催中", value=False)
    use_upcoming = st.sidebar.checkbox("開催予定", value=False)
    use_finished = st.sidebar.checkbox("終了（14日以内）", value=False)

    if not any([use_on_going, use_upcoming, use_finished]):
        st.warning("表示するステータスをサイドバーで1つ以上選択してください。")
        return

    # -------------------------
    # イベント取得・絞り込み
    # -------------------------

    now_ts = int(datetime.datetime.now(JST).timestamp())
    all_events = get_all_events_from_archive()

    filtered_events = []

    for e in all_events:
        if e["event_id"] == EXCLUDE_EVENT_ID:
            continue

        status = get_event_status(e, now_ts)

        if status == "ongoing" and use_on_going:
            filtered_events.append(e)

        elif status == "upcoming" and use_upcoming:
            filtered_events.append(e)

        elif status == "finished" and use_finished:
            if is_within_finished_limit(e, now_ts):
                filtered_events.append(e)

    # 並び順：開始日時降順
    filtered_events.sort(key=lambda x: x["started_at"], reverse=True)

    # -------------------------
    # 一覧データ作成
    # -------------------------

    table_rows = []
    csv_rows = []

    for e in filtered_events:
        entry_count = get_total_entries(e["event_id"])

        row = {
            "イベント名": e.get("event_name", ""),
            "対象": "公式" if e.get("is_entry_scope_inner") else "全体",
            "開始": format_datetime(e.get("started_at")),
            "終了": format_datetime(e.get("ended_at")),
            "参加ルーム数": entry_count,
            "URL": f"https://www.showroom-live.com/event/{e.get('event_url_key','')}"
        }

        table_rows.append(row)
        csv_rows.append(row)

    # -------------------------
    # HTML一覧表示
    # -------------------------

    if table_rows:
        html = """
        <style>
        .table-wrapper {
            overflow-x: auto;
        }
        table {
            border-collapse: collapse;
            width: 100%;
            min-width: 900px;
        }
        th, td {
            border: 1px solid #ddd;
            padding: 6px 8px;
            font-size: 14px;
            white-space: nowrap;
        }
        th {
            background-color: #f0f2f6;
            position: sticky;
            top: 0;
            z-index: 1;
        }
        </style>
        <div class="table-wrapper">
        <table>
        <thead>
        <tr>
            <th>イベント名</th>
            <th>対象</th>
            <th>開始</th>
            <th>終了</th>
            <th>参加ルーム数</th>
            <th>URL</th>
        </tr>
        </thead>
        <tbody>
        """

        for r in table_rows:
            html += f"""
            <tr>
                <td>{r["イベント名"]}</td>
                <td>{r["対象"]}</td>
                <td>{r["開始"]}</td>
                <td>{r["終了"]}</td>
                <td>{r["参加ルーム数"]}</td>
                <td><a href="{r["URL"]}" target="_blank">link</a></td>
            </tr>
            """

        html += """
        </tbody>
        </table>
        </div>
        """

        st.components.v1.html(html, height=600, scrolling=True)

    else:
        st.info("条件に一致するイベントはありません。")

    # -------------------------
    # CSVダウンロード
    # -------------------------

    if csv_rows:
        df_download = pd.DataFrame(csv_rows)
        csv_buf = StringIO()
        df_download.to_csv(csv_buf, index=False, encoding="utf-8-sig")

        b64 = base64.b64encode(csv_buf.getvalue().encode()).decode()
        href = f'<a href="data:file/csv;base64,{b64}" download="sr_event_list.csv">CSVダウンロード</a>'

        st.markdown("---")
        st.markdown(href, unsafe_allow_html=True)


# =========================
# 実行
# =========================

if __name__ == "__main__":
    main()
