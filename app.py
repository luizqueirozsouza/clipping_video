import os

# Força o MediaPipe a rodar em CPU para evitar erros de EGL/GPU em containers Docker
os.environ["MEDIAPIPE_DISABLE_GPU"] = "1"
# Oculta logs excessivos do abseil/mediapipe
os.environ["GLOG_minloglevel"] = "2"

import glob
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import streamlit as st
import yt_dlp
from dotenv import load_dotenv

# Importa serviço Google (Desativado para produtização)
# try:
#     import google_service
#     SERVICE_AVAILABLE = True
# except ImportError:
#     SERVICE_AVAILABLE = False
SERVICE_AVAILABLE = False

# Importa MinIO
try:
    from minio_storage import MinioConfig, MinioStorage

    MINIO_AVAILABLE = True
except ImportError:
    MINIO_AVAILABLE = False

load_dotenv(override=True)

st.set_page_config(
    page_title="AI Video Slicer (V178 TEST)",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==========================================
# CONFIGURAÇÕES
# ==========================================
WORKSPACE_DIR = Path("projeto_atual")
WORKSPACE_DIR.mkdir(exist_ok=True)
BKP_DIR = Path("bkp")
BKP_DIR.mkdir(exist_ok=True)
TOKENS_DIR = Path("tokens")
TOKENS_DIR.mkdir(exist_ok=True)

STRATEGY_MAP = {
    "Vídeos Curtos (Vertical)": "short_form",
    "Vídeos Longos (Horizontal)": "long_form",
}

# Mapeamento de Nichos (Front -> Back)
NICHE_MAP = {
    "✝️ Cristão (Way of the Master)": "christian",
    "🏛️ Política / Debate": "politics",
    "🔴⚫ Flamengo / Futebol": "flamengo",
    "₿ Bitcoin / Cripto": "bitcoin",
    "💸 Economia / Impostos": "economy",
    "✨ Estética / Beleza": "aesthetics",
}

FFMPEG_BINARY = os.getenv("FFMPEG_PATH", "ffmpeg")
# Resolve o caminho absoluto do FFmpeg (importante para Docker e yt-dlp)
if not os.path.isabs(FFMPEG_BINARY):
    found_path = shutil.which(FFMPEG_BINARY)
    if found_path:
        FFMPEG_BINARY = found_path

if os.path.isabs(FFMPEG_BINARY):
    FFMPEG_DIR = os.path.dirname(FFMPEG_BINARY)
else:
    FFMPEG_DIR = None

if "logs" not in st.session_state:
    st.session_state["logs"] = []
if "selected_files" not in st.session_state:
    st.session_state["selected_files"] = []
if "ui_reset_id" not in st.session_state:
    st.session_state["ui_reset_id"] = 0
if "original_url" not in st.session_state:
    # Tenta carregar do arquivo se existir
    url_file = WORKSPACE_DIR / "url_origem.txt"
    if url_file.exists():
        st.session_state["original_url"] = url_file.read_text(encoding="utf-8").strip()
    else:
        st.session_state["original_url"] = ""


def save_original_url(url):
    st.session_state["original_url"] = url
    (WORKSPACE_DIR / "url_origem.txt").write_text(url, encoding="utf-8")


def add_log(text, level="info", replace_last=False):
    """Adiciona um log à sessão. Se replace_last=True, substitui a última linha (ideal para tqdm)."""
    if "logs" not in st.session_state:
        st.session_state["logs"] = []

    msg = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "msg": text,
        "level": level,
    }

    if replace_last and st.session_state["logs"]:
        st.session_state["logs"][-1] = msg
    else:
        st.session_state["logs"].append(msg)

    if len(st.session_state["logs"]) > 100:
        st.session_state["logs"].pop(0)


def format_logs_for_display():
    output = ""
    icons = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"}
    for log_item in st.session_state["logs"]:
        icon = icons.get(log_item["level"], "•")
        output += f"[{log_item['time']}] {icon} {log_item['msg']}\n"
    return output


def update_env_key(key, value):
    """Atualiza ou insere uma chave no arquivo .env preservando o restante."""
    env_path = Path(".env")
    if not env_path.exists():
        env_path.write_text(f"{key}='{value}'\n")
        return

    lines = env_path.read_text(encoding="utf-8").splitlines()
    found = False
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}='{value}'")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"{key}='{value}'")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    os.environ[key] = str(value)
    load_dotenv(override=True)


def check_ffmpeg():
    try:
        subprocess.run(
            [FFMPEG_BINARY, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return True
    except:
        return False


def get_current_video_path():
    files = list(WORKSPACE_DIR.glob("video_master_*.mp4"))
    if not files:
        return None
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]


def cleanup_old_videos():
    for f in WORKSPACE_DIR.glob("video_master_*"):
        try:
            os.remove(f)
        except:
            pass


def cleanup_temp_files():
    for ext in ["*.ytdl", "*.part", "*.f*.mp4", "*.f*.m4a", "*.temp.mp4"]:
        for f in WORKSPACE_DIR.glob(ext):
            try:
                os.remove(f)
            except:
                pass


def reset_analysis():
    files = [
        "transcript_segments.json",
        "video_metadata.json",
        "transcript_words.json",
        "faces_cache.json",
    ]
    for f in files:
        if os.path.exists(f):
            os.remove(f)
    # Limpa URL se existia
    url_file = WORKSPACE_DIR / "url_origem.txt"
    if url_file.exists():
        os.remove(url_file)
    st.session_state["original_url"] = ""
    add_log("Dados de análise e URL limpos.", "info")


def nuke_project(rerun=True, master_video_action="delete"):
    """
    master_video_action: "keep", "delete", "backup"
    """
    if master_video_action == "delete":
        cleanup_old_videos()
        add_log("Vídeos originais removidos.", "warning")
    elif master_video_action == "backup":
        video = get_current_video_path()
        if video and video.exists():
            shutil.move(str(video), BKP_DIR / video.name)
            add_log(f"Vídeo original movido para bkp/{video.name}", "info")
    else:
        add_log("Vídeo original preservado.", "info")

    # Limpa URL se existia
    url_file = WORKSPACE_DIR / "url_origem.txt"
    if url_file.exists():
        os.remove(url_file)

    st.session_state.clear()
    st.session_state["logs"] = []
    st.session_state["selected_files"] = []
    cleanup_temp_files()
    if os.path.exists("cuts"):
        shutil.rmtree("cuts", ignore_errors=True)
    if os.path.exists("subs"):
        shutil.rmtree("subs", ignore_errors=True)
    for f in glob.glob("*.json"):
        try:
            os.remove(f)
        except:
            pass
    add_log("Projeto zerado (Nuclear).", "warning")
    if rerun:
        time.sleep(0.5)
        st.rerun()


def run_command_with_live_logs(cmd_list, env_vars, status_container):
    if cmd_list[0] == sys.executable:
        cmd_list.insert(1, "-u")
    script_name = Path(cmd_list[-1]).name
    add_log(f"Iniciando {script_name}...", "info")
    status_container.code(format_logs_for_display(), language="log")

    process = subprocess.Popen(
        cmd_list,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env_vars,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )

    last_was_pbar = False

    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break

        if line:
            clean = line.strip()
            if not clean:
                continue

            # Detecta se a linha parece uma barra de progresso tqdm (contém %| e [00:)
            is_pbar = "%|" in clean and "[" in clean and ":" in clean

            lvl = "info"
            if "ERRO" in clean or "Error" in clean:
                lvl = "error"
            elif "OK" in clean or "Pronto" in clean:
                lvl = "success"

            # Se for uma barra de progresso, atualiza a última linha em vez de criar uma nova
            # Isso limpa o visual do console no Streamlit
            if is_pbar:
                add_log(clean, lvl, replace_last=last_was_pbar)
                last_was_pbar = True
            else:
                add_log(clean, lvl)
                last_was_pbar = False

            status_container.code(format_logs_for_display(), language="log")

    return process.poll()


# def get_channel_profiles():
#     return [f.stem.replace("token_", "") for f in TOKENS_DIR.glob("token_*.json")]


def get_smart_name_for_file(file_path_str):
    try:
        path = Path(file_path_str)
        filename = path.name

        if not filename.startswith("cut_"):
            return filename

        if not filename.endswith(".mp4"):
            return None

        idx_str = filename.replace("cut_", "").replace(".mp4", "")
        if not idx_str.isdigit():
            return None
        idx = int(idx_str)
        platform = path.parent.name
        json_file = f"{platform}_cuts.json"
        if not os.path.exists(json_file):
            return None
        with open(json_file, "r", encoding="utf-8") as f:
            cuts = json.load(f)
        if idx < len(cuts):
            data = cuts[idx]
            new_name = (
                data.get("slug") or data.get("youtube_title") or data.get("title")
            )
            if new_name:
                clean_name = "".join(
                    [c for c in new_name if c.isalnum() or c in (" ", "-", "_")]
                ).strip()
                return f"{clean_name}.mp4"
    except:
        pass
    return None


def toggle_file_selection(file_path, key):
    if st.session_state[key]:
        if str(file_path) not in st.session_state["selected_files"]:
            st.session_state["selected_files"].append(str(file_path))
    else:
        if str(file_path) in st.session_state["selected_files"]:
            st.session_state["selected_files"].remove(str(file_path))


def select_all_files(files_list):
    uid = st.session_state["ui_reset_id"]
    for f in files_list:
        st.session_state[f"chk_{f}_{uid}"] = True
        if str(f) not in st.session_state["selected_files"]:
            st.session_state["selected_files"].append(str(f))


def deselect_all_files(files_list):
    uid = st.session_state["ui_reset_id"]
    for f in files_list:
        st.session_state[f"chk_{f}_{uid}"] = False
        if str(f) in st.session_state["selected_files"]:
            st.session_state["selected_files"].remove(str(f))


def full_reset_selection():
    st.session_state["selected_files"] = []
    st.session_state["ui_reset_id"] += 1


# === MINIO HELPER ===
def get_minio_storage():
    if not MINIO_AVAILABLE:
        return None
    try:
        config = MinioConfig(
            endpoint=os.getenv("MINIO_ENDPOINT", "localhost:9000"),
            access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
            secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
            secure=os.getenv("MINIO_SECURE", "False").lower() == "true",
            region=os.getenv("MINIO_REGION", None),
        )
        return MinioStorage(config)
    except Exception as e:
        st.error(f"Erro config MinIO: {e}")
        return None


# ==========================================
# INTERFACE PRINCIPAL
# ==========================================
st.title("🎬 AI Video Slicer (V176 - Produtização)")

if not check_ffmpeg():
    st.error("❌ FFmpeg não encontrado!")
    st.info(f"Caminho atual: {FFMPEG_BINARY}")
    st.stop()

# Abas Principais
tab_main, tab_gallery, tab_settings = st.tabs(
    ["🚀 Processamento", "📂 Galeria", "⚙️ Configurações"]
)

# === VARS GLOBAIS ===
video_path = get_current_video_path()
has_video = video_path is not None and video_path.exists()
analysis_done = os.path.exists("video_metadata.json")

# Detecção do que está pronto
available_jsons = {}
if os.path.exists("outputs/shorts.json"):
    available_jsons["vertical"] = "Vertical (Shorts+TikTok+Reels)"
elif os.path.exists("vertical_cuts.json"):
    available_jsons["vertical"] = "Vertical (Shorts+TikTok+Reels)"
if os.path.exists("youtube_cuts.json"):
    available_jsons["youtube"] = "YouTube Long"

# --- Inicialização Global do Nicho (Persistente) ---
if "nicho_session" not in st.session_state:
    env_v = os.getenv("CHANNEL_TYPE", "christian")
    match = [k for k, v in NICHE_MAP.items() if v == env_v]
    st.session_state["nicho_session"] = match[0] if match else list(NICHE_MAP.keys())[0]

selected_niche_ui = st.session_state["nicho_session"]
selected_niche_key = NICHE_MAP[selected_niche_ui]
# --------------------------------------------------

with tab_settings:
    st.header("⚙️ Configurações do Sistema")
    st.info("As alterações feitas aqui são salvas permanentemente no arquivo `.env`.")

    with st.expander("� Chaves de API", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            k_gemini = st.text_input(
                "Gemini API Key", value=os.getenv("GEMINI_API_KEY", ""), type="password"
            )
            k_groq = st.text_input(
                "Groq API Key", value=os.getenv("GROQ_API_KEY", ""), type="password"
            )
        with c2:
            k_openai = st.text_input(
                "OpenAI API Key", value=os.getenv("OPENAI_API_KEY", ""), type="password"
            )
            k_openrouter = st.text_input(
                "OpenRouter API Key",
                value=os.getenv("OPENROUTER_API_KEY", ""),
                type="password",
            )
        if st.button("Salvar Chaves API"):
            update_env_key("GEMINI_API_KEY", k_gemini)
            update_env_key("GROQ_API_KEY", k_groq)
            update_env_key("OPENAI_API_KEY", k_openai)
            update_env_key("OPENROUTER_API_KEY", k_openrouter)
            st.success("Chaves API atualizadas!")

    with st.expander("📦 Armazenamento (MinIO/S3)", expanded=False):
        m_endpoint = st.text_input("Endpoint", value=os.getenv("MINIO_ENDPOINT", ""))
        m_access = st.text_input("Access Key", value=os.getenv("MINIO_ACCESS_KEY", ""))
        m_secret = st.text_input(
            "Secret Key", value=os.getenv("MINIO_SECRET_KEY", ""), type="password"
        )
        m_secure = st.checkbox(
            "Usar SSL (HTTPS)",
            value=os.getenv("MINIO_SECURE", "true").lower() == "true",
        )
        m_bucket = st.text_input(
            "Bucket Padrão", value=os.getenv("MINIO_BUCKET", "youtube")
        )
        if st.button("Salvar MinIO"):
            update_env_key("MINIO_ENDPOINT", m_endpoint)
            update_env_key("MINIO_ACCESS_KEY", m_access)
            update_env_key("MINIO_SECRET_KEY", m_secret)
            update_env_key("MINIO_SECURE", "true" if m_secure else "false")
            update_env_key("MINIO_BUCKET", m_bucket)
            st.success("Configurações MinIO salvas!")

    st.markdown("---")
    if st.button("🚨 RESET TOTAL DO PROJETO", type="secondary"):
        st.session_state["show_reset_confirm"] = True
    if st.session_state.get("show_reset_confirm"):
        st.warning("⚠️ Isso apagará todos os cortes e metadados locais!")
        reset_action = st.radio(
            "Vídeo original:",
            ["Manter", "Fazer Backup", "Excluir"],
            index=0,
            horizontal=True,
        )
        rmap = {"Manter": "keep", "Fazer Backup": "backup", "Excluir": "delete"}

        col_res1, col_res2 = st.columns(2)
        with col_res1:
            if st.button("🗑️ Confirmar Reset", type="primary"):
                nuke_project(master_video_action=rmap[reset_action])
        with col_res2:
            if st.button("🔙 Cancelar"):
                st.session_state["show_reset_confirm"] = False
                st.rerun()

with tab_main:
    with st.sidebar:
        st.header("🎨 Personalização")
        selected_strategies_ui = st.multiselect(
            "Tipos de Cortes:",
            list(STRATEGY_MAP.keys()),
            default=["Vídeos Curtos (Vertical)"],
        )
        active_strategies = [STRATEGY_MAP[s] for s in selected_strategies_ui]
        st.divider()
        video_layout = st.selectbox(
            "Enquadramento:",
            ["single", "auto_podcast"],
            index=1,
            format_func=lambda x: "👤 Solo" if x == "single" else "🧠 Auto Podcast",
        )
        fx_mode = st.selectbox(
            "Anti-Copyright:",
            ["none", "balanced"],
            index=1,
            format_func=lambda x: "🚫 Nenhum" if x == "none" else "⚖️ Equilibrado",
        )
        mirror = st.checkbox("🪞 Espelhar Vídeo", False)
        vignette = st.checkbox("🎭 Vinheta (Foco Central)", True)
        st.divider()
        sub_font = st.selectbox("Fonte:", ["Arial Black", "Impact", "Arial"], index=0)
        sub_color = st.selectbox("Cor:", ["Amarelo", "Verde", "Azul", "Rosa"], index=0)
        sub_upper = st.checkbox("Caixa Alta", value=True)

    st.header("🎮 Pipeline de Produção")
    btn_container = st.container()
    with st.expander("📟 Console de Logs", expanded=True):
        log_ph = st.empty()
        log_ph.code(format_logs_for_display(), language="log")

    st.divider()

    with btn_container:
        c1, c2, c3, c4 = st.columns(4)

        with c1:
            st.markdown("**1. Fonte**")

            # Escolha de Nicho (Persistente no .env)
            niche_labels = list(NICHE_MAP.keys())
            try:
                default_index = niche_labels.index(st.session_state["nicho_session"])
            except:
                default_index = 0

            new_niche_ui = st.selectbox(
                "🎯 Nicho do Conteúdo:", niche_labels, index=default_index
            )

            # Se o usuário mudou o nicho, salva no .env e na sessão
            if new_niche_ui != st.session_state["nicho_session"]:
                st.session_state["nicho_session"] = new_niche_ui
                update_env_key("CHANNEL_TYPE", NICHE_MAP[new_niche_ui])
                st.rerun()

            selected_niche_ui = st.session_state["nicho_session"]
            selected_niche_key = NICHE_MAP[selected_niche_ui]

            if not has_video:
                f = st.file_uploader("Upload MP4", type=["mp4", "mov"])
                if f:
                    new_n = f"video_master_{int(time.time())}.mp4"
                    nuke_project(False)
                    # O nicho já foi salvo acima pelo selectbox
                    with open(WORKSPACE_DIR / new_n, "wb") as o:
                        o.write(f.getbuffer())
                    st.rerun()
                u = st.text_input("YouTube Link")
                if u and st.button("📥 Baixar"):
                    nuke_project(False)
                    save_original_url(u)
                    # O nicho já foi salvo acima pelo selectbox
                    with st.spinner("Baixando..."):
                        yt_opts = {
                            "format": "bestvideo[ext=mp4]+bestaudio/best",
                            "outtmpl": str(
                                WORKSPACE_DIR / "video_master_%(epoch)s.%(ext)s"
                            ),
                            # "ffmpeg_location": FFMPEG_BINARY,
                            "merge_output_format": "mp4",
                        }
                        yt_dlp.YoutubeDL(yt_opts).download([u])
                        st.rerun()
            else:
                st.success(f"✅ Vídeo Ativo ({selected_niche_ui})")
                if st.session_state.get("original_url"):
                    st.caption(f"🔗 Origem: {st.session_state['original_url']}")
                st.button(
                    "🗑️ Remover Vídeo", on_click=nuke_project, args=(False, "delete")
                )

        # Define variáveis de ambiente para os comandos
        env = os.environ.copy()
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "VIDEO": str(video_path) if video_path else "",
                "VIDEO_LAYOUT": video_layout,
                "FX_MODE": fx_mode,
                "SUB_FONT": sub_font,
                "SUB_COLOR": sub_color,
                "SUB_UPPERCASE": "1" if sub_upper else "0",
                "MIRROR_MODE": "1" if mirror else "0",
                "VIGNETTE_MODE": "1" if vignette else "0",
                "STRATEGIES": ",".join(active_strategies),
                "CHANNEL_TYPE": selected_niche_key,
                "ORIGINAL_URL": st.session_state.get("original_url", ""),
                "MINIO_BUCKET": os.getenv("MINIO_BUCKET", "youtube"),
            }
        )

        with c2:
            st.markdown("**2. Transcrição**")
            if has_video:
                if analysis_done:
                    st.success("✅ OK")
                    st.button("🔄 Refazer", on_click=reset_analysis)
                else:
                    if st.button("🚀 Iniciar"):
                        run_command_with_live_logs(
                            [sys.executable, "facecut.py"], env, log_ph
                        )
                        st.rerun()
            else:
                st.info("---")

        with c3:
            st.markdown("**3. Inteligência**")
            if has_video and analysis_done:
                lb = "🔄 Re-analisar" if available_jsons else "🧠 Gerar Cortes"
                if st.button(lb):
                    run_command_with_live_logs(
                        [sys.executable, "segment_agent.py"], env, log_ph
                    )
                    st.rerun()
            else:
                st.info("---")

        with c4:
            st.markdown("**4. Renderização**")
            if available_jsons:
                render_choices = st.multiselect(
                    "Fila:",
                    list(available_jsons.keys()),
                    format_func=lambda x: available_jsons[x],
                    default=list(available_jsons.keys()),
                )
                if st.button("🎬 INICIAR", type="primary"):
                    for p in render_choices:
                        denv = env.copy()
                        denv.update(
                            {
                                "CURRENT_PLATFORM": p,
                                "FACECUT_JSON": "vertical_cuts.json"
                                if p == "vertical"
                                else "youtube_cuts.json",
                                "SUBS_DIR": f"subs/{p}",
                                "OUTPUT_DIR": f"cuts/{p}",
                            }
                        )
                        os.makedirs(f"subs/{p}", exist_ok=True)
                        os.makedirs(f"cuts/{p}", exist_ok=True)
                        run_command_with_live_logs(
                            [sys.executable, "generate_karaoke_from_segment.py"],
                            denv,
                            log_ph,
                        )
                        run_command_with_live_logs(
                            [sys.executable, "render_facecuts.py"], denv, log_ph
                        )
                    st.balloons()
            else:
                st.info("---")

with tab_gallery:
    st.header("📂 Galeria de Clips")
    if os.path.exists("cuts"):
        g_tabs = st.tabs(["Vertical", "YouTube"])
        pmap = {"Vertical": "vertical", "YouTube": "youtube"}
        for gt, gp in zip(g_tabs, pmap.values()):
            pth = Path("cuts") / gp
            with gt:
                if pth.exists():
                    vfiles = sorted(
                        list(pth.glob("*.mp4")), key=os.path.getmtime, reverse=True
                    )
                    if vfiles:
                        cols = st.columns(4)
                        for i, vf in enumerate(vfiles):
                            with cols[i % 4]:
                                # Tenta pegar um nome amigável (slug/título), senão usa o nome do arquivo
                                display_name = (
                                    get_smart_name_for_file(str(vf)) or vf.name
                                )
                                st.markdown(f"**{display_name}**")

                                st.video(str(vf))
                                ck = f"chk_{vf}"
                                if ck not in st.session_state:
                                    st.session_state[ck] = str(
                                        vf
                                    ) in st.session_state.get("selected_files", [])

                                st.checkbox(
                                    "Selecionar",
                                    key=ck,
                                    on_change=toggle_file_selection,
                                    args=(vf, ck),
                                )
    else:
        st.info("Nenhum corte gerado ainda.")

    # Seção de Upload consolidada
    if st.session_state.get("selected_files"):
        selected_videos = st.session_state["selected_files"]
        st.divider()
        st.success(f"📦 {len(selected_videos)} vídeos selecionados para upload.")

        if st.button("🚀 ENVIAR PARA YOUTUBE (MinIO)", type="primary"):
            if not MINIO_AVAILABLE:
                st.error("Serviço de storage indisponível.")
                st.stop()

            storage = get_minio_storage()
            if storage:
                # Carrega banco de descrições
                desc_db = {}
                if os.path.exists("video_descriptions.json"):
                    with open("video_descriptions.json", "r", encoding="utf-8") as f:
                        desc_db = json.load(f)

                progress_bar = st.progress(0)
                for i, v_str in enumerate(selected_videos):
                    v_path = Path(v_str)
                    slug = v_path.stem
                    meta = desc_db.get(
                        slug,
                        {
                            "title": slug,
                            "description": "Upload automático",
                            "tags": [],
                            "slug": slug,
                        },
                    )

                    v_type = "shorts" if "vertical" in str(v_path) else "longs"
                    remote_prefix = f"{selected_niche_key}/{v_type}/{slug}"
                    video_filename = f"{slug}.mp4"
                    meta["video_filename"] = video_filename

                    # Upload Vídeo
                    storage.upload_file(
                        bucket=os.getenv("MINIO_BUCKET", "youtube"),
                        file_path=v_path,
                        object_name=f"{remote_prefix}/{video_filename}",
                        content_type="video/mp4",
                    )

                    # Upload Metadados
                    meta_path = v_path.parent / f"{slug}_meta.json"
                    with open(meta_path, "w", encoding="utf-8") as f:
                        json.dump(meta, f, indent=2, ensure_ascii=False)
                    storage.upload_file(
                        bucket=os.getenv("MINIO_BUCKET", "youtube"),
                        file_path=meta_path,
                        object_name=f"{remote_prefix}/metadata.json",
                        content_type="application/json",
                    )
                    os.remove(meta_path)

                    progress_bar.progress((i + 1) / len(selected_videos))

                st.balloons()
                st.success("✅ Upload concluído!")
                time.sleep(2)
                st.session_state["selected_files"] = []
                st.rerun()
