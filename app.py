import streamlit as st
import requests
from datetime import datetime, timedelta
import time
import pytz
import pandas as pd
import io
import re
import ftplib  # ✅ FTPアップロード機能用
import concurrent.futures
import streamlit.components.v1 as components


# 日本時間(JST)のタイムゾーンを設定
JST = pytz.timezone('Asia/Tokyo')

# --- 定数定義 ---
# APIリクエスト時に使用するヘッダー
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"}
# イベント検索APIのURL
API_EVENT_SEARCH_URL = "https://www.showroom-live.com/api/event/search"
# イベントルームリストAPIのURL（参加ルーム数取得用）
API_EVENT_ROOM_LIST_URL = "https://www.showroom-live.com/api/event/room_list"
# SHOWROOMのイベントページのベースURL
EVENT_PAGE_BASE_URL = "https://www.showroom-live.com/event/"
# MKsoulルームリスト
ROOM_LIST_URL = "https://mksoul-pro.com/showroom/file/room_list.csv"
# 過去イベントデータファイルのURLを格納しているインデックスファイルのURL
PAST_EVENT_INDEX_URL = "https://mksoul-pro.com/showroom/file/sr-event-archive-list-index.txt"


# ===============================
# 📱 共通レスポンシブCSS（スマホ／タブレット対応）
# ===============================
st.markdown("""
<style>
/* ---------- テーブル共通 ---------- */
table {
    width: 100%;
    border-collapse: collapse;
    font-size: 14px;
}

/* ---------- ボタンリンク ---------- */
.rank-btn-link {
    background: #0b57d0;
    color: white !important;
    border: none;
    padding: 4px 8px;
    border-radius: 4px;
    cursor: pointer;
    text-decoration: none;
    display: inline-block;
    font-size: 12px;
}
.rank-btn-link:hover {
    background: #0949a8;
}

/* ---------- 横スクロール対応 ---------- */
.table-wrapper {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    border: 1px solid #ddd;
    border-radius: 6px;
    width: 100%;
}

/*
.room-name-ellipsis {
    max-width: 250px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    display: inline-block;
}
*/

/* ---------- スマホ・タブレット対応 ---------- */
@media screen and (max-width: 1024px) {
    table {
        font-size: 12px !important;
    }
    th, td {
        padding: 6px !important;
    }
    .rank-btn-link {
        padding: 6px 8px !important;
        font-size: 13px !important;
    }
    .table-wrapper {
        overflow-x: auto !important;
        display: block !important;
    }
    /* 固定幅で横スクロール可能にする */
    .table-wrapper table {
        width: 1080px !important;
    }
}
</style>
""", unsafe_allow_html=True)



# --- ヘルパー: event_id 正規化関数（変更点） ---
def normalize_event_id_val(val):
    """
    event_id の型ゆれ（数値、文字列、'123.0' など）を吸収して
    一貫した文字列キーを返す。
    戻り値: 正規化された文字列 (例: "123")、無効なら None を返す
    """
    if val is None:
        return None
    try:
        # numpy / pandas の数値型も扱えるよう float にして判定
        # ただし 'abc' のような文字列はそのまま文字列化して返す
        if isinstance(val, (int,)):
            return str(val)
        if isinstance(val, float):
            if val.is_integer():
                return str(int(val))
            return str(val).strip()
        s = str(val).strip()
        # もし "123.0" のような表記なら整数に変換して整数表記で返す
        if re.match(r'^\d+(\.0+)?$', s):
            return str(int(float(s)))
        # 普通の数字文字列やキー文字列はトリムしたものを返す
        if s == "":
            return None
        return s
    except Exception:
        try:
            return str(val).strip()
        except Exception:
            return None

# --- データ取得関数 ---



# --- FTPヘルパー関数群 ---
def ftp_upload(file_path, content_bytes):
    """FTPサーバーにファイルをアップロード"""
    ftp_host = st.secrets["ftp"]["host"]
    ftp_user = st.secrets["ftp"]["user"]
    ftp_pass = st.secrets["ftp"]["password"]
    with ftplib.FTP(ftp_host) as ftp:
        ftp.login(ftp_user, ftp_pass)
        with io.BytesIO(content_bytes) as f:
            ftp.storbinary(f"STOR {file_path}", f)


def ftp_download(file_path):
    """FTPサーバーからファイルをダウンロード（存在しない場合はNone）"""
    ftp_host = st.secrets["ftp"]["host"]
    ftp_user = st.secrets["ftp"]["user"]
    ftp_pass = st.secrets["ftp"]["password"]
    with ftplib.FTP(ftp_host) as ftp:
        ftp.login(ftp_user, ftp_pass)
        buffer = io.BytesIO()
        try:
            ftp.retrbinary(f"RETR {file_path}", buffer.write)
            buffer.seek(0)
            return buffer.getvalue().decode('utf-8-sig')
        except Exception:
            return None


def update_archive_file():
    """全イベントを取得→必要項目を抽出→重複除外→sr-event-archive.csvを上書き→ログ追記＋DL"""
    JST = pytz.timezone('Asia/Tokyo')
    now_str = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")

    st.info("📡 イベントデータを取得中...")
    statuses = [1, 3, 4]
    new_events = get_events(statuses)

    # ✅ 必要な9項目だけ抽出
    filtered_events = []
    for e in new_events:
        try:
            filtered_events.append({
                "event_id": e.get("event_id"),
                "is_event_block": e.get("is_event_block"),
                "is_entry_scope_inner": e.get("is_entry_scope_inner"),
                "event_name": e.get("event_name"),
                "image_m": e.get("image_m"),
                "started_at": e.get("started_at"),
                "ended_at": e.get("ended_at"),
                "event_url_key": e.get("event_url_key"),
                "show_ranking": e.get("show_ranking")
            })
        except Exception:
            continue

    new_df = pd.DataFrame(filtered_events)
    if new_df.empty:
        st.warning("有効なイベントデータが取得できませんでした。")
        return

    # event_id正規化
    new_df["event_id"] = new_df["event_id"].apply(normalize_event_id_val)
    new_df.dropna(subset=["event_id"], inplace=True)
    new_df.drop_duplicates(subset=["event_id"], inplace=True)

    # 既存バックアップを取得
    st.info("💾 FTPサーバー上の既存バックアップを取得中...")
    existing_csv = ftp_download("/mksoul-pro.com/showroom/file/sr-event-archive.csv")
    if existing_csv:
        old_df = pd.read_csv(io.StringIO(existing_csv), dtype=str)
        old_df["event_id"] = old_df["event_id"].apply(normalize_event_id_val)
    else:
        old_df = pd.DataFrame(columns=new_df.columns)

    # 結合＋重複除外
    merged_df = pd.concat([old_df, new_df], ignore_index=True)
    before_count = len(old_df)
    merged_df.drop_duplicates(subset=["event_id"], keep="last", inplace=True)
    after_count = len(merged_df)
    added_count = after_count - before_count  # ←このままでOK（マイナスも許容）

    # 上書きアップロード
    st.info("☁️ FTPサーバーへアップロード中...")
    csv_bytes = merged_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    ftp_upload("/mksoul-pro.com/showroom/file/sr-event-archive.csv", csv_bytes)

    # ログ追記
    log_text = f"[{now_str}] 更新完了: {added_count}件追加 / 合計 {after_count}件\n"
    existing_log = ftp_download("/mksoul-pro.com/showroom/file/sr-event-archive-log.txt")
    if existing_log:
        log_text = existing_log + log_text
    ftp_upload("/mksoul-pro.com/showroom/file/sr-event-archive-log.txt", log_text.encode("utf-8"))

    st.success(f"✅ バックアップ更新完了: {added_count}件追加（合計 {after_count}件）")

    # ✅ 更新完了後にダウンロードボタン追加
    st.download_button(
        label="📥 更新後のバックアップCSVをダウンロード",
        data=csv_bytes,
        file_name=f"sr-event-archive_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv"
    )


if "authenticated" not in st.session_state:  #認証用
    st.session_state.authenticated = False  #認証用

@st.cache_data(ttl=600)  # 10分間キャッシュを保持
def get_events(statuses):
    """
    指定されたステータスのイベントリストをAPIから取得します。
    変更点: 各イベント辞書に取得元ステータスを示すキー '_fetched_status' を追加します。
    """
    all_events = []
    # 選択されたステータスごとにAPIを叩く
    for status in statuses:
        page = 1
        # 1ステータスあたり最大20ページまで取得を試みる
        for _ in range(20):
            params = {"status": status, "page": page}
            try:
                response = requests.get(API_EVENT_SEARCH_URL, headers=HEADERS, params=params, timeout=10)
                response.raise_for_status()  # HTTPエラーがあれば例外を発生
                data = response.json()

                # 'events' または 'event_list' キーからイベントリストを取得
                page_events = data.get('events', data.get('event_list', []))

                if not page_events:
                    break  # イベントがなければループを抜ける

                # --- ここが重要: 各イベントに取得元ステータスを注入 ---
                for ev in page_events:
                    try:
                        # in-placeで書き込んでしまって問題ない想定
                        ev['_fetched_status'] = status
                    except Exception:
                        pass

                all_events.extend(page_events)
                page += 1
                time.sleep(0.1) # APIへの負荷を考慮して少し待機
            except requests.exceptions.RequestException as e:
                st.error(f"イベントデータ取得中にエラーが発生しました (status={status}): {e}")
                break
            except ValueError:
                st.error(f"APIからのJSONデコードに失敗しました (status={status})。")
                break
    return all_events



@st.cache_data(ttl=600)
def get_past_events_from_files():
    """
    終了(BU)チェック時に使用される過去イベントデータを取得。
    これまでのインデックス方式ではなく、
    固定ファイル https://mksoul-pro.com/showroom/file/sr-event-archive.csv を直接読み込む。
    """
    all_past_events = pd.DataFrame()
    column_names = [
        "event_id", "is_event_block", "is_entry_scope_inner", "event_name",
        "image_m", "started_at", "ended_at", "event_url_key", "show_ranking"
    ]

    fixed_csv_url = "https://mksoul-pro.com/showroom/file/sr-event-archive.csv"

    try:
        response = requests.get(fixed_csv_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        csv_text = response.content.decode('utf-8-sig')
        csv_file_like_object = io.StringIO(csv_text)
        df = pd.read_csv(csv_file_like_object, dtype=str)

        # 列名チェック（足りない列があれば補う）
        for col in column_names:
            if col not in df.columns:
                df[col] = None
        df = df[column_names]  # 列順を揃える

        # 型整形
        df['is_entry_scope_inner'] = df['is_entry_scope_inner'].astype(str).str.lower().str.strip() == 'true'
        df['started_at'] = pd.to_numeric(df['started_at'], errors='coerce')
        df['ended_at'] = pd.to_numeric(df['ended_at'], errors='coerce')
        df.dropna(subset=['started_at', 'ended_at'], inplace=True)
        df['event_id'] = df['event_id'].apply(normalize_event_id_val)
        df.dropna(subset=['event_id'], inplace=True)
        df.drop_duplicates(subset=['event_id'], keep='last', inplace=True)

        # 終了済みイベントのみに絞る
        now_timestamp = int(datetime.now(JST).timestamp())
        df = df[df['ended_at'] < now_timestamp]

        # ✅ イベント終了日が新しい順にソート（ここが今回の追加）
        df.sort_values(by="ended_at", ascending=False, inplace=True, ignore_index=True)

        all_past_events = df.copy()

    except requests.exceptions.RequestException as e:
        st.warning(f"バックアップCSV取得中にエラーが発生しました: {e}")
    except Exception as e:
        st.warning(f"バックアップCSVの処理中にエラーが発生しました: {e}")

    return all_past_events.to_dict('records')


#@st.cache_data(ttl=300)  # 5分間キャッシュを保持
def get_total_entries(event_id):
    """
    指定されたイベントの総参加ルーム数を取得します。
    """
    params = {"event_id": event_id}
    try:
        response = requests.get(API_EVENT_ROOM_LIST_URL, headers=HEADERS, params=params, timeout=10)
        # 404エラーは参加者情報がない場合なので正常系として扱う
        if response.status_code == 404:
            return 0
        response.raise_for_status()
        data = response.json()
        # 'total_entries' キーから参加ルーム数を取得
        return data.get('total_entries', 0)
    except requests.exceptions.RequestException:
        # エラー時は 'N/A' を返す
        return "N/A"
    except ValueError:
        return "N/A"


# --- ▼ ここから追加: 参加者情報取得ヘルパー（get_total_entries の直後に挿入） ▼ ---
@st.cache_data(ttl=60)
def get_event_room_list_api(event_id):
    """ /api/event/room_list?event_id= を叩いて参加ルーム一覧（主に上位30）を取得する """
    try:
        resp = requests.get(API_EVENT_ROOM_LIST_URL, headers=HEADERS, params={"event_id": event_id}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # キー名が環境で異なるので複数のキーをチェック
        if isinstance(data, dict):
            for k in ('list', 'room_list', 'event_entry_list', 'entries', 'data', 'event_list'):
                if k in data and isinstance(data[k], list):
                    return data[k]
        if isinstance(data, list):
            return data
    except Exception:
        # 何か失敗したら空リストを返す（呼び出し側で扱いやすくするため）
        return []
    return []

@st.cache_data(ttl=60)
def get_room_profile_api(room_id):
    """ /api/room/profile?room_id= を叩いてルームプロフィールを取得する """
    try:
        resp = requests.get(f"https://www.showroom-live.com/api/room/profile?room_id={room_id}", headers=HEADERS, timeout=6)
        resp.raise_for_status()
        return resp.json() or {}
    except Exception:
        return {}


def get_official_mark(room_id):
    """ルームの公式/フリー区分を返す（公/フ）"""
    try:
        prof = get_room_profile_api(room_id)
        if prof.get("is_official") is True:
            return "公"
        else:
            return "フ"
    except Exception:
        return ""


def _show_rank_score(rank_str):
    """
    SHOWランクをソート可能なスコアに変換する簡易ヘルパー。
    完全網羅的ではありませんが、降順ソートができる程度のスコア化を行います。
    """
    if not rank_str:
        return -999
    s = str(rank_str).upper()
    m = re.match(r'([A-Z]+)(\d*)', s)
    if not m:
        return -999
    letters = m.group(1)
    num = int(m.group(2)) if m.group(2).isdigit() else 0
    order_map = {'E':0,'D':1,'C':2,'B':3,'A':4,'S':5,'SS':6,'SSS':7}
    base = order_map.get(letters, 0)
    return base * 100 - num



HEADERS = {"User-Agent": "Mozilla/5.0"}

# ✅ event_id 単位でキャッシュ（ページ単位も含む）
@st.cache_data(ttl=300)
def fetch_room_list_page(event_id: str, page: int):
    """1ページ分の room_list を取得（キャッシュ対象）"""
    url = f"https://www.showroom-live.com/api/event/room_list?event_id={event_id}&p={page}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            return res.json().get("list", [])
    except Exception:
        pass
    return []


def get_event_participants(event, limit=10):
    event_id = event.get("event_id")
    if not event_id:
        return []

    # --- ① room_list 全ページを疑似並列で取得 ---
    max_pages = 30  # 安全上限（900件相当）
    page_indices = list(range(1, max_pages + 1))
    all_entries = []
    seen_ids = set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_page = {
            executor.submit(fetch_room_list_page, event_id, page): page
            for page in page_indices
        }
        for future in concurrent.futures.as_completed(future_to_page):
            try:
                page_entries = future.result()
                for entry in page_entries:
                    rid = str(entry.get("room_id"))
                    if rid and rid not in seen_ids:
                        seen_ids.add(rid)
                        all_entries.append(entry)
                # ページにデータがなくなったら以降は無駄なのでbreak
                if not page_entries:
                    break
            except Exception:
                continue

    if not all_entries:
        return []

    # --- ② 並列で profile 情報を取得 ---
    def fetch_profile(rid):
        """個別room_idのプロフィール取得（安全ラップ）"""
        url = f"https://www.showroom-live.com/api/room/profile?room_id={rid}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=6)
            if r.status_code == 200:
                return r.json()
        except Exception:
            return {}
        return {}

    room_ids = [item.get("room_id") for item in all_entries if item.get("room_id")]

    participants = []
    # 並列取得（I/Oバウンド処理を高速化）
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_id = {executor.submit(fetch_profile, rid): rid for rid in room_ids}
        for future in concurrent.futures.as_completed(future_to_id):
            rid = future_to_id[future]
            try:
                profile = future.result()
                if not profile:
                    continue
                participants.append({
                    "room_id": str(rid),
                    "room_name": profile.get("room_name") or f"room_{rid}",
                    "room_level": int(profile.get("room_level", 0)),
                    "show_rank_subdivided": profile.get("show_rank_subdivided") or "",
                    "follower_num": int(profile.get("follower_num", 0)),
                    "live_continuous_days": int(profile.get("live_continuous_days", 0)),
                })
            except Exception:
                continue

    # --- ③ SHOWランク > ルームレベル > フォロワー数 でソート ---
    rank_order = [
        "SS-5","SS-4","SS-3","SS-2","SS-1",
        "S-5","S-4","S-3","S-2","S-1",
        "A-5","A-4","A-3","A-2","A-1",
        "B-5","B-4","B-3","B-2","B-1",
        "C-10","C-9","C-8","C-7","C-6","C-5","C-4","C-3","C-2","C-1"
    ]
    rank_score = {rank: len(rank_order) - i for i, rank in enumerate(rank_order)}

    def sort_key(x):
        s = rank_score.get(x.get("show_rank_subdivided", ""), 0)
        return (s, x.get("room_level", 0), x.get("follower_num", 0))

    participants_sorted = sorted(participants, key=sort_key, reverse=True)

    if not participants_sorted:
        return []

    # --- ④ 上位 limit 件のみ抽出 ---
    top = participants_sorted[:limit]

    # --- ⑤ rank/point補完（存在しない場合は0補正） ---
    rank_map = {}
    for r in all_entries:
        rid = str(r.get("room_id"))
        if not rid:
            continue
        point_val = r.get("point") or r.get("event_point") or r.get("total_point") or 0
        try:
            point_val = int(point_val)
        except Exception:
            point_val = 0
        rank_map[rid] = {
            "rank": r.get("rank") or r.get("position") or "-",
            "point": point_val
        }

    for p in top:
        rid = p["room_id"]
        rp = rank_map.get(rid, {})
        p["rank"] = rp.get("rank", "-")
        p["point"] = rp.get("point", 0)

    return top



# --- UI表示関数 ---



def get_duration_category(start_ts, end_ts):
    """
    イベント期間からカテゴリを判断します。
    """
    duration = timedelta(seconds=end_ts - start_ts)
    if duration <= timedelta(days=3):
        return "3日以内"
    elif duration <= timedelta(days=7):
        return "1週間"
    elif duration <= timedelta(days=10):
        return "10日"
    elif duration <= timedelta(days=14):
        return "2週間"
    else:
        return "その他"





# --- メイン処理 ---
def main():
    # ページ設定
    st.set_page_config(
        page_title="SHOWROOM イベント一覧",
        page_icon="🎤",
        layout="wide"
    )

    st.markdown(
        "<h1 style='font-size:28px; text-align:left; color:#1f2937;'>🎤 SHOWROOM イベント一覧</h1>",
        unsafe_allow_html=True
    )
    #st.markdown("<h1 style='font-size:2.5em;'>🎤 SHOWROOM イベント一覧</h1>", unsafe_allow_html=True)
    st.write("")


    # ▼▼ 認証ステップ ▼▼
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if "mksp_authenticated" not in st.session_state:
        st.session_state.mksp_authenticated = False
        
    if not st.session_state.authenticated:
        st.markdown("##### 🔑 認証コードを入力してください")
        input_room_id = st.text_input(
            "認証コードを入力してください:",
            placeholder="",
            type="password",
            key="room_id_input"
        )

        # 認証ボタン
        if st.button("認証する"):
            if input_room_id:  # 入力が空でない場合のみ
                if input_room_id.strip() == "mksp154851":
                    st.session_state.authenticated = True
                    st.session_state.mksp_authenticated = True
                    st.success("✅ 特別な認証に成功しました。ツールを利用できます。")
                    st.rerun()
                else:
                    try:
                        response = requests.get(ROOM_LIST_URL, timeout=5)
                        response.raise_for_status()
                        # room_df = pd.read_csv(io.StringIO(response.text), header=None)
                        import pandas # 念のためこの行の直前か、ファイル冒頭に入れておく
                        room_df = pandas.read_csv(io.StringIO(response.text), header=None)
    
                        valid_codes = set(str(x).strip() for x in room_df.iloc[:, 0].dropna())
    
                        if input_room_id.strip() in valid_codes:
                            st.session_state.authenticated = True
                            st.success("✅ 認証に成功しました。ツールを利用できます。")
                            st.rerun()  # 認証成功後に再読み込み
                        else:
                            st.error("❌ 認証コードが無効です。正しい認証コードを入力してください。")
                    except Exception as e:
                        st.error(f"認証リストを取得できませんでした: {e}")
            else:
                st.warning("認証コードを入力してください。")
                
        # 認証が終わるまで他のUIを描画しない
        st.stop()
    # ▲▲ 認証ステップここまで ▲▲


    # 行間と余白の調整
    st.markdown(
        """
        <style>
        /* イベント詳細の行間を詰める */
        .event-info p, .event-info li, .event-info {
            line-height: 1.7;
            margin-top: 0.0rem;
            margin-bottom: 0.4rem;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    # --- フィルタリング機能 ---
    st.sidebar.header("表示フィルタ")
    status_options = {
        "開催中": 1,
        "開催予定": 3,
        "終了": 4,
    }

    # チェックボックスの状態を管理
    use_on_going = st.sidebar.checkbox("開催中", value=True)
    use_upcoming = st.sidebar.checkbox("開催予定", value=False)
    use_finished = st.sidebar.checkbox("終了", value=False)
    use_past_bu = st.sidebar.checkbox("終了(BU)", value=False, help="過去のバックアップファイルから取得した終了済みイベント")


    selected_statuses = []
    if use_on_going:
        selected_statuses.append(status_options["開催中"])
    if use_upcoming:
        selected_statuses.append(status_options["開催予定"])
    if use_finished:
        selected_statuses.append(status_options["終了"])

    if not selected_statuses and not use_past_bu:
        st.warning("表示するステータスをサイドバーで1つ以上選択してください。")
    
    
    # 選択されたステータスに基づいてイベント情報を取得
    # 辞書を使って重複を確実に排除
    unique_events_dict = {}

    # --- カウント用の変数を初期化（追加） ---
    fetched_count_raw = 0
    past_count_raw = 0
    fetched_events = []  # 参照安全のため初期化
    past_events = []     # 参照安全のため初期化

    if selected_statuses:
        with st.spinner("イベント情報を取得中..."):
            fetched_events = get_events(selected_statuses)
            # --- API取得分の「生」件数を保持（変更） ---
            fetched_count_raw = len(fetched_events)
            for event in fetched_events:
                # --- 変更: event_id を正規化して辞書キーにする ---
                eid = normalize_event_id_val(event.get('event_id'))
                if eid is None:
                    # 無効なIDはスキップ
                    continue
                # イベントオブジェクト内の event_id も正規化して上書きしておく（以降の処理を安定させるため）
                event['event_id'] = eid
                # フェッチ元（API）を優先して格納（上書き可）
                unique_events_dict[eid] = event
    
    # --- 「終了(BU)」のデータ取得 ---
    if use_past_bu:
        with st.spinner("過去のイベントデータを取得・処理中..."):
            past_events = get_past_events_from_files()
            past_count_raw = len(past_events)

            # ✅ APIで取得した「終了」イベント（status=4）の event_id 一覧を作成
            api_finished_events = []
            try:
                api_finished_events = get_events([4])  # 明示的に終了ステータスだけ再取得
            except Exception as ex:
                st.warning(f"終了イベント情報の取得中にエラーが発生しました: {ex}")

            api_finished_ids = {
                normalize_event_id_val(e.get("event_id"))
                for e in api_finished_events
                if e.get("event_id")
            }

            # ✅ 「終了(BU)」からAPIの「終了」イベントを除外（重複完全排除）
            filtered_past_events = []
            for e in past_events:
                eid = normalize_event_id_val(e.get("event_id"))
                if eid and eid not in api_finished_ids:
                    filtered_past_events.append(e)

            removed_count = len(past_events) - len(filtered_past_events)
            if removed_count > 0:
                st.info(f"🧹 「終了(BU)」から {removed_count} 件の重複イベントを除外しました。")

            past_events = filtered_past_events

            # --- 正規化＆辞書格納 ---
            for event in past_events:
                eid = normalize_event_id_val(event.get('event_id'))
                if eid is None:
                    continue
                event['event_id'] = eid
                # 既に API から取得されたイベントが存在する場合は上書きしない（API 側を優先）
                if eid not in unique_events_dict:
                    unique_events_dict[eid] = event


    # 辞書の値をリストに変換して、フィルタリング処理に進む
    all_events = list(unique_events_dict.values())
    
    # ✅ 特定イベントを完全除外（フィルタ候補にも残らないように）
    all_events = [e for e in all_events if str(e.get("event_id")) != "12151"]
    
    original_event_count = len(all_events)

    # --- 取得前の合計（生）件数とユニーク件数の差分を算出（追加） ---
    total_raw = fetched_count_raw + past_count_raw
    unique_total_pre_filter = len(all_events)
    duplicates_removed_pre_filter = max(0, total_raw - unique_total_pre_filter)

    if not all_events:
        st.info("該当するイベントはありませんでした。")
        st.stop()
    else:
        # --- reverse制御フラグを定義 ---
        # 「終了」または「終了(BU)」がチェックされている場合は降順（reverse=True）
        # それ以外（＝開催中／開催予定のみ）の場合は昇順（reverse=False）
        reverse_sort = (use_finished or use_past_bu)

        # --- 開始日フィルタの選択肢を生成 ---
        start_dates = sorted(list(set([
            datetime.fromtimestamp(e['started_at'], JST).date() for e in all_events if 'started_at' in e
        ])), reverse=reverse_sort)

        # 日付と曜日の辞書を作成
        start_date_options = {
            d.strftime('%Y/%m/%d') + f"({['月', '火', '水', '木', '金', '土', '日'][d.weekday()]})": d
            for d in start_dates
        }

        selected_start_dates = st.sidebar.multiselect(
            "開始日でフィルタ",
            options=list(start_date_options.keys())
        )

        # --- 終了日フィルタの選択肢を生成 ---
        end_dates = sorted(list(set([
            datetime.fromtimestamp(e['ended_at'], JST).date() for e in all_events if 'ended_at' in e
        ])), reverse=reverse_sort)

        end_date_options = {
            d.strftime('%Y/%m/%d') + f"({['月', '火', '水', '木', '金', '土', '日'][d.weekday()]})": d
            for d in end_dates
        }

        selected_end_dates = st.sidebar.multiselect(
            "終了日でフィルタ",
            options=list(end_date_options.keys())
        )

        # 期間でフィルタ
        duration_options = ["3日以内", "1週間", "10日", "2週間", "その他"]
        selected_durations = st.sidebar.multiselect(
            "期間でフィルタ",
            options=duration_options
        )

        # 対象でフィルタ
        target_options = ["全ライバー", "対象者限定"]
        selected_targets = st.sidebar.multiselect(
            "対象でフィルタ",
            options=target_options
        )
        
        # 認証されていればダウンロードボタンとタイムスタンプ変換機能をここに配置
        if st.session_state.mksp_authenticated:
            st.sidebar.markdown("")
            st.sidebar.markdown("")
            st.sidebar.markdown("---")
            st.sidebar.header("特別機能")

            # --- 🔄 バックアップ更新ボタン ---
            if st.sidebar.button("バックアップ更新"):
                try:
                    update_archive_file()
                except Exception as e:
                    st.sidebar.error(f"バックアップ更新中にエラーが発生しました: {e}")

            if st.sidebar.button("ダウンロード準備"):
                try:
                    all_statuses_to_download = [1, 3, 4]
                    with st.spinner("ダウンロード用の全イベントデータを取得中..."):
                        all_events_to_download = get_events(all_statuses_to_download)
                    events_for_df = []
                    for event in all_events_to_download:
                        if all(k in event for k in ["event_id", "is_event_block", "is_entry_scope_inner", "event_name", "image_m", "started_at", "ended_at", "event_url_key", "show_ranking"]):
                            event_data = {
                                "event_id": event["event_id"],
                                "is_event_block": event["is_event_block"],
                                "is_entry_scope_inner": event["is_entry_scope_inner"],
                                "event_name": event["event_name"],
                                "image_m": event["image_m"],
                                "started_at": event["started_at"], # Unixタイムスタンプ形式に戻す
                                "ended_at": event["ended_at"],     # Unixタイムスタンプ形式に戻す
                                "event_url_key": event["event_url_key"],
                                "show_ranking": event["show_ranking"]
                            }
                            events_for_df.append(event_data)
                    
                    if events_for_df:
                        df = pd.DataFrame(events_for_df)
                        csv_data = df.to_csv(index=False).encode('utf-8-sig')
                        st.sidebar.download_button(
                            label="ダウンロード開始",
                            data=csv_data,
                            file_name=f"showroom_events_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}.csv",
                            mime="text/csv",
                            key="download_button_trigger",
                        )
                        st.sidebar.success("ダウンロード準備ができました。上記のボタンをクリックしてください。")
                    else:
                        st.sidebar.warning("ダウンロード可能なイベントデータがありませんでした。")
                except Exception as e:
                    st.sidebar.error(f"データのダウンロード中にエラーが発生しました: {e}")

            # タイムスタンプ変換機能
            st.sidebar.markdown("---")
            st.sidebar.markdown("#### 🕒 タイムスタンプから日時へ変換")
            timestamp_input = st.sidebar.text_input(
                "タイムスタンプを入力",
                placeholder="例: 1754902800",
                key="timestamp_input"
            )

            if st.sidebar.button("タイムスタンプから日時へ変換"):
                if timestamp_input and timestamp_input.isdigit():
                    try:
                        ts = int(timestamp_input)
                        converted_dt = datetime.fromtimestamp(ts, JST)
                        st.sidebar.success(
                            f"**変換結果:**\n\n"
                            f"**日時:** {converted_dt.strftime('%Y/%m/%d %H:%M:%S')}"
                        )
                    except ValueError:
                        st.sidebar.error("無効なタイムスタンプです。数値を入力してください。")
                else:
                    st.sidebar.warning("タイムスタンプを入力してください。")

            # 日時からタイムスタンプへ変換
            st.sidebar.markdown("---")
            st.sidebar.markdown("#### 📅 日時からタイムスタンプへ変換")
            datetime_input = st.sidebar.text_input(
                "日時を入力 (YYYY/MM/DD HH:MM)",
                placeholder="例: 2025/08/11 18:00",
                key="datetime_input"
            )
            
            # 日時を「開始時間」のタイムスタンプに変換するボタン
            if st.sidebar.button("日時から開始タイムスタンプへ変換"):
                if datetime_input:
                    try:
                        dt_obj_naive = datetime.strptime(datetime_input.strip(), '%Y/%m/%d %H:%M').replace(second=0)
                        dt_obj = JST.localize(dt_obj_naive, is_dst=None)
                        timestamp = int(dt_obj.timestamp())
                        st.sidebar.success(
                            f"**開始タイムスタンプの変換結果:**\n\n"
                            f"**タイムスタンプ:** {timestamp}"
                        )
                    except ValueError:
                        st.sidebar.error("無効な日時形式です。'YYYY/MM/DD HH:MM'形式で入力してください。")
                else:
                    st.sidebar.warning("日時を入力してください。")
            
            # 日時を「終了時間」のタイムスタンプへ変換するボタン
            if st.sidebar.button("日時から終了タイムスタンプへ変換"):
                if datetime_input:
                    try:
                        dt_obj_naive = datetime.strptime(datetime_input.strip(), '%Y/%m/%d %H:%M').replace(second=59)
                        dt_obj = JST.localize(dt_obj_naive, is_dst=None)
                        timestamp = int(dt_obj.timestamp())
                        st.sidebar.success(
                            f"**終了タイムスタンプの変換結果:**\n\n"
                            f"**タイムスタンプ:** {timestamp}"
                        )
                    except ValueError:
                        st.sidebar.error("無効な日時形式です。'YYYY/MM/DD HH:MM'形式で入力してください。")
                else:
                    st.sidebar.warning("日時を入力してください。")
        
        # フィルタリングされたイベントリスト
        filtered_events = all_events
        
        if selected_start_dates:
            # start_date_options を参照する
            selected_dates_set = {start_date_options[d] for d in selected_start_dates}
            filtered_events = [
                e for e in filtered_events
                if 'started_at' in e and datetime.fromtimestamp(e['started_at'], JST).date() in selected_dates_set
            ]
        
        # ▼▼ 終了日フィルタの処理を追加（ここから追加/修正） ▼▼
        if selected_end_dates:
            # end_date_options を参照する
            selected_dates_set = {end_date_options[d] for d in selected_end_dates}
            filtered_events = [
                e for e in filtered_events
                if 'ended_at' in e and datetime.fromtimestamp(e['ended_at'], JST).date() in selected_dates_set
            ]
        # ▲▲ 終了日フィルタの処理を追加（ここまで追加/修正） ▲▲

        if selected_durations:
            filtered_events = [
                e for e in filtered_events
                if get_duration_category(e['started_at'], e['ended_at']) in selected_durations
            ]
        
        if selected_targets:
            target_map = {"全ライバー": False, "対象者限定": True}
            selected_target_values = {target_map[t] for t in selected_targets}
            filtered_events = [
                e for e in filtered_events
                if e.get('is_entry_scope_inner') in selected_target_values
            ]
        
        
        # --- 表示メッセージの改善（汎用的な文言） ---
        filtered_count = len(filtered_events)
        if use_finished and use_past_bu and duplicates_removed_pre_filter > 0:
            st.success(f"{filtered_count}件のイベントが見つかりました。※重複データが存在した場合は1件のみ表示しています。")
        else:
            st.success(f"{filtered_count}件のイベントが見つかりました。")
        
        st.markdown("---")

        # with st.spinner("イベント一覧を生成中..."):
        # render_event_summary_table(filtered_events)
        #
        # st.markdown("---")

        # 取得したイベント情報を1つずつ表示
        for event in filtered_events:
            col1, col2 = st.columns([1, 4])

            with col1:
                st.image(event['image_m'])

            with col2:
                event_url = f"{EVENT_PAGE_BASE_URL}{event['event_url_key']}"
                st.markdown(
                    f'<div class="event-info"><strong><a href="{event_url}">{event["event_name"]}</a></strong></div>',
                    unsafe_allow_html=True
                )

                target_info = "対象者限定" if event.get("is_entry_scope_inner") else "全ライバー"
                st.markdown(f'<div class="event-info"><strong>対象:</strong> {target_info}</div>', unsafe_allow_html=True)

                start_date = datetime.fromtimestamp(event['started_at'], JST).strftime('%Y/%m/%d %H:%M')
                end_date = datetime.fromtimestamp(event['ended_at'], JST).strftime('%Y/%m/%d %H:%M')
                st.markdown(
                    f'<div class="event-info"><strong>期間:</strong> {start_date} - {end_date}</div>',
                    unsafe_allow_html=True
                )

                total_entries = get_total_entries(event['event_id'])
                st.markdown(
                    f'<div class="event-info"><strong>参加ルーム数:</strong> {total_entries}</div>',
                    unsafe_allow_html=True
                )

                # --- ▼ ここから追加: 終了日時に基づいてボタン表示制御（修正版） ▼ ---
                try:
                    now_ts = int(datetime.now(JST).timestamp())
                    ended_ts = int(float(event.get("ended_at", 0)))
                    # ミリ秒表記対策
                    if ended_ts > 20000000000:
                        ended_ts //= 1000
                except Exception:
                    ended_ts = 0
                    now_ts = 0

                # fetched_statusを安全に取得
                try:
                    fetched_status = int(float(event.get("_fetched_status", 0)))
                except Exception:
                    fetched_status = None

                # -------------------------------
                # ① 開催中 or 開催予定 → 参加ルームボタンを表示
                # -------------------------------
                if now_ts < ended_ts:
                    btn_key = f"show_participants_{event.get('event_id')}"
                    if st.button("参加ルーム情報を表示", key=btn_key):
                        with st.spinner("参加ルーム情報を取得中..."):
                            try:
                                participants = get_event_participants(event, limit=10)
                                if not participants:
                                    st.info("参加ルームがありません。")
                                else:
                                    import pandas as _pd
                                    rank_order = [
                                        "SS-5","SS-4","SS-3","SS-2","SS-1",
                                        "S-5","S-4","S-3","S-2","S-1",
                                        "A-5","A-4","A-3","A-2","A-1",
                                        "B-5","B-4","B-3","B-2","B-1",
                                        "C-10","C-9","C-8","C-7","C-6","C-5","C-4","C-3","C-2","C-1"
                                    ]
                                    rank_score = {rank: i for i, rank in enumerate(rank_order[::-1])}
                                    dfp = _pd.DataFrame(participants)
                                    cols = [
                                        'room_name', 'room_level', 'show_rank_subdivided',
                                        'follower_num', 'live_continuous_days', 'room_id', 'rank', 'point'
                                    ]
                                    for c in cols:
                                        if c not in dfp.columns:
                                            dfp[c] = ""
                                    dfp['_rank_score'] = dfp['show_rank_subdivided'].map(rank_score).fillna(-1)
                                    dfp.sort_values(
                                        by=['_rank_score', 'room_level', 'follower_num'],
                                        ascending=[False, False, False],
                                        inplace=True
                                    )
                                    dfp_display = dfp[cols].copy()

                                    # ▼ 1. rename（必ず先）
                                    dfp_display.rename(columns={
                                        'room_name': 'ルーム名',
                                        'room_level': 'ルームレベル',
                                        'show_rank_subdivided': 'SHOWランク',
                                        'follower_num': 'フォロワー数',
                                        'live_continuous_days': 'まいにち配信',
                                        'room_id': 'ルームID',
                                        'rank': '順位',
                                        'point': 'ポイント'
                                    }, inplace=True)

                                    # ▼ 2. 公/フ 追加（rename 後なので安全）
                                    dfp_display["公/フ"] = dfp_display["ルームID"].apply(get_official_mark)

                                    dfp_display = dfp_display[
                                        ['ルーム名', 'ルームレベル', 'SHOWランク', 'フォロワー数',
                                         'まいにち配信', '公/フ', 'ルームID', '順位', 'ポイント']
                                    ]

                                    def _make_link(row):
                                        rid = row['ルームID']
                                        name = row['ルーム名'] or f"room_{rid}"
                                        return f'<a href="https://www.showroom-live.com/room/profile?room_id={rid}" target="_blank">{name}</a>'
                                        # short = name
                                        # if len(short) > 18:  # 一応18文字で省略（必要に応じ変更可）
                                        #     short = short[:15] + "..."

                                        # return (
                                        #     f'<a class="room-name-ellipsis" '
                                        #     f'href="https://www.showroom-live.com/room/profile?room_id={rid}" '
                                        #     f'target="_blank">{short}</a>'
                                        # )

                                    dfp_display['ルーム名'] = dfp_display.apply(_make_link, axis=1)

                                    # 数値フォーマット関数
                                    def _fmt_int_for_display(v, comma=True):
                                        try:
                                            if v is None or (isinstance(v, str) and v.strip() == ""):
                                                return ""
                                            num = float(v)
                                            return f"{int(num):,}" if comma else f"{int(num)}"
                                        except Exception:
                                            return str(v)
                                    if 'ポイント' in dfp_display.columns:
                                        dfp_display['ポイント'] = dfp_display['ポイント'].apply(lambda x: _fmt_int_for_display(x, comma=True))
                                    for col in ['ルームレベル', 'フォロワー数', 'まいにち配信', '順位']:
                                        if col in dfp_display.columns:
                                            dfp_display[col] = dfp_display[col].apply(lambda x: _fmt_int_for_display(x, comma=False))

                                    html_table = "<table style='width:100%; border-collapse:collapse;'>"
                                    html_table += "<thead style='background-color:#f3f4f6;'><tr>"
                                    for col in dfp_display.columns:
                                        html_table += f"<th style='padding:6px; border-bottom:1px solid #ccc; text-align:center;'>{col}</th>"
                                    html_table += "</tr></thead><tbody>"
                                    for _, row in dfp_display.iterrows():
                                        html_table += "<tr>"
                                        for val in row:
                                            html_table += f"<td style='padding:6px; border-bottom:1px solid #eee; text-align:center;'>{val}</td>"
                                        html_table += "</tr>"
                                    html_table += "</tbody></table>"

                                    with st.expander("参加ルーム一覧（最大10ルーム）", expanded=True):
                                        st.markdown(f"<div class='table-wrapper'>{html_table}</div>", unsafe_allow_html=True)
                                        #st.markdown(f"<div style='overflow-x: auto;'>{html_table}</div>", unsafe_allow_html=True)
                            except Exception as e:
                                st.error(f"参加ルーム情報の取得中にエラーが発生しました: {e}")
                # -------------------------------
                # ② ランキングボタンは常に別判定（終了イベントも対象）【終了(BU)完全対応版】
                # -------------------------------

                try:
                    # 終了(BU)イベントIDをキャッシュに保持（型違い両対応）
                    if "past_event_ids" not in st.session_state:
                        st.session_state.past_event_ids = set()
                        for e in past_events:
                            eid = e.get("event_id")
                            if eid is not None:
                                st.session_state.past_event_ids.add(str(eid))
                                try:
                                    st.session_state.past_event_ids.add(str(int(eid)))
                                except Exception:
                                    pass
                    past_event_ids = st.session_state.past_event_ids
                except Exception:
                    past_event_ids = set()

                # 🔹 現在処理中のイベントIDを取得
                eid_str = str(event.get("event_id"))
                fetched_status = None
                try:
                    fetched_status = int(float(event.get("_fetched_status", 0)))
                except Exception:
                    pass

                # --- 条件 ---
                # ① APIから取得（開催中・終了）
                # ② 「終了(BU)」ON時
                cond_is_target = (
                    (fetched_status in (1, 4)) or
                    (use_past_bu)
                )

                if cond_is_target:
                    btn_rank_key = f"show_ranking_{eid_str}"
                    if st.button("ランキングを表示", key=btn_rank_key):
                        with st.spinner("ランキング情報を取得中..."):
                            display_ranking_table(event.get('event_id'))
                # --- ▲ ここまで修正版 ▲ ---
                else:
                    # 終了済みイベントは非表示 or 非活性メッセージを表示
                    #st.markdown('<div class="event-info"><em>（イベント終了済のため参加ルーム情報は非表示）</em></div>', unsafe_allow_html=True)
                    st.markdown('', unsafe_allow_html=True)
                # --- ▲ 追加ここまで ▲ ---

            st.markdown("---")


        # ===============================
        # 一覧表示 & CSVダウンロード
        # ===============================
        import streamlit.components.v1 as components
        import pandas as pd
        import base64

        st.markdown("##### 📋 一覧表示")

        # --- 1. CSVデータの生成 (元の文字化けしないロジックを維持) ---
        download_data = []
        for e in filtered_events:
            download_data.append({
                "イベント名": e['event_name'],
                "対象": "対象者限定" if e.get("is_entry_scope_inner") else "全ライバー",
                "開始": datetime.fromtimestamp(e["started_at"], JST).strftime('%Y/%m/%d %H:%M'),
                "終了": datetime.fromtimestamp(e["ended_at"], JST).strftime('%Y/%m/%d %H:%M'),
                "参加ルーム数": get_total_entries(e["event_id"])
            })

        df_download = pd.DataFrame(download_data)
        # 前に「大丈夫そう」と言っていただいた「utf-8-sig」のエンコードをそのまま使用
        csv_bytes = df_download.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig')
        b64_csv = base64.b64encode(csv_bytes).decode()

        # --- 2. HTMLの作成 (テーブルとボタンを一体化して隙間を無くす) ---
        html = f"""
        <style>
        .summary-wrapper {{
            max-height: 80vh;
            overflow-y: auto;
            border: 1px solid #d1d5db;
            /* 下のボタンとの間に少しだけ余白を作る場合はここ */
            margin-bottom: 0px; 
        }}
        .summary-table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            font-size: 0.85rem; 
            font-family: sans-serif;
        }}

        /* --- 【修正】表の一番下の線がダブるのを防ぐ --- */
        .summary-table tbody tr:last-child td {{
            border-bottom: none;
        }}

        .summary-table thead th {{
            background: #f3f4f6;
            text-align: center;
            padding: 10px 12px;
            border-bottom: 1px solid #d1d5db;
            border-right: 1px solid #d1d5db;
            position: sticky;
            top: 0;
            z-index: 10;
            white-space: nowrap; 
        }}
        .summary-table tbody td {{
            padding: 8px 12px;
            border-bottom: 1px solid #e5e7eb;
            border-right: 1px solid #e5e7eb;
            white-space: nowrap; 
        }}
        .summary-table td:first-child {{
            white-space: normal;
            min-width: 250px;
        }}
        .summary-table tbody td.col-center {{
            text-align: center;
        }}
        .summary-table thead th:last-child,
        .summary-table tbody td:last-child {{
            border-right: none;
        }}

        /* --- 【修正】ボタンの位置の微調整 --- */
        .dl-link {{
            display: inline-flex;
            align-items: center;
            padding: 0.4rem 0.8rem;
            border-radius: 0.5rem;
            color: #31333F;
            background-color: #FFFFFF;
            border: 1px solid #d1d5db;
            text-decoration: none;
            font-size: 0.85rem;
            font-family: sans-serif;
            
            /* ここで表との距離を調整します（10px程度が標準的です） */
            margin-top: 12px; 
        }}
        .dl-link:hover {{
            border-color: #FF4B4B;
            color: #FF4B4B;
        }}
        </style>

        <div class="summary-wrapper">
            <table class="summary-table">
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

        for e in filtered_events:
            html += f"""
                <tr>
                  <td><a href="{EVENT_PAGE_BASE_URL}{e['event_url_key']}" target="_blank">{e['event_name']}</a></td>
                  <td class="col-center">{"対象者限定" if e.get("is_entry_scope_inner") else "全ライバー"}</td>
                  <td class="col-center">{datetime.fromtimestamp(e["started_at"], JST).strftime('%Y/%m/%d %H:%M')}</td>
                  <td class="col-center">{datetime.fromtimestamp(e["ended_at"], JST).strftime('%Y/%m/%d %H:%M')}</td>
                  <td class="col-center">{get_total_entries(e["event_id"])}</td>
                </tr>
            """

        html += f"""
                </tbody>
            </table>
        </div>
        <a class="dl-link" href="data:text/csv;base64,{b64_csv}" download="event_list.csv">
            📊 この内容をCSVでダウンロード
        </a>
        """

        # ボタンまで含めて表示されるよう高さを調整
        components.html(html, height=800, scrolling=False)

            

if __name__ == "__main__":
    main()