import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta, timezone

# =========================
# 定数
# =========================
CSV_URL = "https://mksoul-pro.com/showroom/file/sr-event-archive.csv"
ROOM_API = "https://www.showroom-live.com/api/event/room_list?event_id={event_id}"
JST = timezone(timedelta(hours=9))

# =========================
# 共通関数
# =========================
def unix_to_jst(ts):
    if pd.isna(ts):
        return ""
    return datetime.fromtimestamp(int(ts), JST).strftime("%Y/%m/%d %H:%M")

def get_room_count(event_id):
    try:
        r = requests.get(ROOM_API.format(event_id=event_id), timeout=10)
        r.raise_for_status()
        return r.json().get("total_entries", 0)
    except Exception:
        return ""

# =========================
# データ取得（固定CSV）
# =========================
@st.cache_data(ttl=300)
def load_event_data():
    df = pd.read_csv(CSV_URL)

    # 日時変換
    df["started_at_disp"] = df["started_at"].apply(unix_to_jst)
    df["ended_at_disp"] = df["ended_at"].apply(unix_to_jst)

    # 対象
    df["entry_scope"] = df["is_entry_scope_inner"].apply(
        lambda x: "対象者限定" if str(x).upper() == "TRUE" else "全ライバー"
    )

    # イベントURL
    df["event_url"] = df["event_url_key"].apply(
        lambda x: f"https://www.showroom-live.com/event/{x}"
    )

    # ステータス判定
    now = datetime.now(JST)
    df["started_at_dt"] = df["started_at"].apply(lambda x: datetime.fromtimestamp(int(x), JST))
    df["ended_at_dt"] = df["ended_at"].apply(lambda x: datetime.fromtimestamp(int(x), JST))

    def status(row):
        if row["started_at_dt"] > now:
            return "開催予定"
        elif row["ended_at_dt"] < now:
            return "終了"
        else:
            return "開催中"

    df["status"] = df.apply(status, axis=1)

    # 終了後2週間超を除外
    df = df[
        ~(
            (df["status"] == "終了")
            & (df["ended_at_dt"] < now - timedelta(days=14))
        )
    ]

    return df

# =========================
# UI
# =========================
st.set_page_config(page_title="SHOWROOM イベント一覧", layout="wide")
st.title("SHOWROOM イベント一覧")

# サイドバー（初期未チェック）
st.sidebar.header("表示条件")
use_on_going = st.sidebar.checkbox("開催中", value=False)
use_upcoming = st.sidebar.checkbox("開催予定", value=False)
use_finished = st.sidebar.checkbox("終了", value=False)

selected_status = []
if use_on_going:
    selected_status.append("開催中")
if use_upcoming:
    selected_status.append("開催予定")
if use_finished:
    selected_status.append("終了")

if not selected_status:
    st.warning("表示するステータスをサイドバーで1つ以上選択してください。")
    st.stop()

# =========================
# 表示処理
# =========================
df = load_event_data()
df = df[df["status"].isin(selected_status)]

# 参加ルーム数取得（API）
df["参加ルーム数"] = df["event_id"].apply(get_room_count)

# 表示用データ
view_df = pd.DataFrame({
    "イベント名": df["event_name"],
    "対象": df["entry_scope"],
    "開始": df["started_at_disp"],
    "終了": df["ended_at_disp"],
    "参加ルーム数": df["参加ルーム数"],
    "イベントURL": df["event_url"],
})

# 表示（リンク対応）
st.dataframe(
    view_df,
    use_container_width=True,
    column_config={
        "イベントURL": st.column_config.LinkColumn(
            label="イベントURL",
            display_text="リンクを開く"
        )
    }
)

# =========================
# CSVダウンロード
# =========================
csv = view_df.to_csv(index=False, encoding="utf-8-sig")
st.download_button(
    label="CSVダウンロード",
    data=csv,
    file_name="showroom_event_list.csv",
    mime="text/csv"
)
