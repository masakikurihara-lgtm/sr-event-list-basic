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