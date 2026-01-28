import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta
import pytz
import io
import base64
import streamlit.components.v1 as components

JST = pytz.timezone("Asia/Tokyo")

EVENT_ARCHIVE_CSV_URL = "https://mksoul-pro.com/showroom/file/sr-event-archive.csv"
EVENT_PAGE_BASE_URL = "https://www.showroom-live.com/event/"
EVENT_ROOM_LIST_API = "https://www.showroom-live.com/api/event/room_list"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


# ===============================
# イベント一覧取得（CSV固定）
# ===============================
@st.cache_data(ttl=600)
def load_events_from_csv():
    df = pd.read_csv(EVENT_ARCHIVE_CSV_URL, dtype=str)

    df["started_at"] = pd.to_numeric(df["started_at"], errors="coerce")
    df["ended_at"] = pd.to_numeric(df["ended_at"], errors="coerce")
    df["is_entry_scope_inner"] = df["is_entry_scope_inner"].str.upper() == "TRUE"

    df.dropna(subset=["event_id", "started_at", "ended_at"], inplace=True)

    now = datetime.now(JST)
    two_weeks_ago_ts = int((now - timedelta(days=14)).timestamp())

    # 終了後2週間以内 or 開催中 or 開催予定のみ
    df = df[
        (df["ended_at"] >= two_weeks_ago_ts)
    ]

    return df


# ===============================
# 参加ルーム数
# ===============================
def get_total_entries(event_id):
    try:
        res = requests.get(
            EVENT_ROOM_LIST_API,
            params={"event_id": event_id},
            headers=HEADERS,
            timeout=10
        )
        if res.status_code != 200:
            return "N/A"
        return res.json().get("total_entries", 0)
    except Exception:
        return "N/A"


# ===============================
# メイン
# ===============================
def main():
    st.set_page_config(page_title="SHOWROOM イベント一覧", layout="wide")

    st.markdown("## 🎤 SHOWROOM イベント一覧")

    df = load_events_from_csv()

    if df.empty:
        st.info("表示可能なイベントがありません。")
        return

    rows = []

    for _, r in df.iterrows():
        rows.append({
            "イベント名": r["event_name"],
            "URL": f"{EVENT_PAGE_BASE_URL}{r['event_url_key']}",
            "対象": "対象者限定" if r["is_entry_scope_inner"] else "全ライバー",
            "開始": datetime.fromtimestamp(int(r["started_at"]), JST).strftime("%Y/%m/%d %H:%M"),
            "終了": datetime.fromtimestamp(int(r["ended_at"]), JST).strftime("%Y/%m/%d %H:%M"),
            "参加ルーム数": get_total_entries(r["event_id"])
        })

    df_view = pd.DataFrame(rows)

    # ===== CSV =====
    csv = df_view.drop(columns=["URL"]).to_csv(index=False, encoding="utf-8-sig")
    b64 = base64.b64encode(csv.encode()).decode()

    # ===== HTML表示（見え方維持）=====
    html = """
    <div class="table-wrapper">
    <table>
      <thead>
        <tr>
          <th>イベント名</th>
          <th>対象</th>
          <th>開始</th>
          <th>終了</th>
          <th>参加ルーム数</th>
        </tr>
      </thead>
      <tbody>
    """

    for r in rows:
        html += f"""
        <tr>
          <td><a href="{r['URL']}" target="_blank">{r['イベント名']}</a></td>
          <td>{r['対象']}</td>
          <td>{r['開始']}</td>
          <td>{r['終了']}</td>
          <td>{r['参加ルーム数']}</td>
        </tr>
        """

    html += f"""
      </tbody>
    </table>
    </div>
    <a href="data:text/csv;base64,{b64}" download="event_list.csv">
      📊 この内容をCSVでダウンロード
    </a>
    """

    components.html(html, height=800, scrolling=False)


if __name__ == "__main__":
    main()
