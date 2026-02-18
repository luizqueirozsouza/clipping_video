import glob
import json
import os
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
    page_title="AI Video Slicer (V172)",
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
}

FFMPEG_BINARY = os.getenv("FFMPEG_PATH", "ffmpeg")
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


def add_log(text, level="info"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    st.session_state["logs"].append({"time": timestamp, "level": level, "msg": text})


def format_logs_for_display():
    output = ""
    icons = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"}
    for log in st.session_state["logs"]:
        icon = icons.get(log["level"], "•")
        output += f"[{log['time']}] {icon} {log['msg']}\n"
    return output


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
    files = ["transcript_segments.json", "video_metadata.json", "transcript_words.json"]
    for f in files:
        if os.path.exists(f):
            os.remove(f)
    add_log("Dados de análise limpos (Cache de rostos preservado).", "info")
    st.rerun()


def nuke_project(rerun=True, master_video_action="keep"):
    """
    master_video_action: "keep", "delete", "backup"
    """
    # Gerencia o vídeo original antes de limpar o resto
    video = get_current_video_path()
    if video and video.exists():
        if master_video_action == "delete":
            os.remove(video)
            add_log(f"Vídeo original removido: {video.name}", "warning")
        elif master_video_action == "backup":
            shutil.move(str(video), BKP_DIR / video.name)
            add_log(f"Vídeo original movido para bkp/{video.name}", "info")
        else:
            add_log(f"Vídeo original preservado: {video.name}", "info")

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
    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break
        if line:
            clean = line.strip()
            if clean:
                lvl = (
                    "error"
                    if "❌" in clean or "Error" in clean
                    else ("success" if "✅" in clean else "info")
                )
                add_log(clean, lvl)
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
# INTERFACE
# ==========================================
st.title("🎬 AI Video Slicer (V172 - Named Slugs)")
if not check_ffmpeg():
    st.error("❌ FFmpeg não encontrado!")
    st.stop()

with st.sidebar:
    st.header("⚙️ Configurações")

    # Credenciais agora são lidas apenas do .env para maior segurança em produção

    # === SELETOR DE NICHO (Define Pasta no MinIO) ===
    st.markdown("---")
    st.subheader("🎭 Persona / Nicho")
    selected_niche_ui = st.selectbox(
        "Estilo da Descrição:",
        list(NICHE_MAP.keys()),
        index=0,
        help="Define o tom de voz e a PASTA DE DESTINO no MinIO.",
    )
    selected_niche_key = NICHE_MAP[selected_niche_ui]
    st.caption(f"Pasta MinIO: /{selected_niche_key}/...")
    # ====================================

    st.divider()

    st.subheader("🎯 Estratégia de Análise")
    selected_strategies_ui = st.multiselect(
        "Tipo de Conteúdo:",
        list(STRATEGY_MAP.keys()),
        default=["Vídeos Curtos (Vertical)"],
    )
    active_strategies = [STRATEGY_MAP[s] for s in selected_strategies_ui]

    st.divider()
    st.subheader("📐 Layout de Vídeo")
    video_layout = st.selectbox(
        "Modo de Corte:",
        ["single", "auto_podcast"],
        index=1,
        format_func=lambda x: "👤 Solo (Fixo)"
        if x == "single"
        else "🧠 Auto Podcast (Smart Switch)",
    )
    if video_layout == "auto_podcast":
        st.caption("ℹ️ Divide tela se houver 2 pessoas; Foca se for close-up.")

    st.divider()
    st.subheader("🎨 Legenda")
    sub_font = st.selectbox("Fonte:", ["Arial Black", "Impact", "Arial"], index=0)
    sub_color = st.selectbox("Cor:", ["Amarelo", "Verde", "Azul", "Rosa"], index=0)
    sub_upper = st.checkbox("CAIXA ALTA", value=True)

    st.divider()
    st.subheader("🛡️ Pós-Produção")
    fx_mode = st.selectbox(
        "Anti-Copyright:",
        ["none", "balanced"],
        index=1,
        format_func=lambda x: "🚫 Nenhum" if x == "none" else "⚖️ Equilibrado",
    )
    mirror = st.checkbox("🪞 Espelhar", False)

    # st.divider()
    # st.subheader("☁️ Drive (Backup)")
    # profiles = get_channel_profiles()
    # selected_profile = st.selectbox("Conta:", profiles) if profiles else "default"

    # with st.expander("➕ Nova Conta"):
    #     new_profile = st.text_input("Nome", placeholder="Ex: MeuDrive")
    #     if st.button("Autenticar"):
    #         tk = TOKENS_DIR / f"token_{new_profile}.json"
    #         if tk.exists():
    #             os.remove(tk)
    #         try:
    #             google_service.get_authenticated_creds(str(tk))
    #             st.success("OK!")
    #             st.rerun()
    #         except:
    #             st.error("Erro Auth")

    # drive_folder_id = st.text_input(
    #     "ID Pasta Drive", value=os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    # )

    # MinIO Bucket Config
    minio_bucket = st.text_input(
        "MinIO Bucket", value=os.getenv("MINIO_BUCKET", "youtube")
    )

    if st.button("🚨 RESET TOTAL (Apaga Tudo)", type="primary"):
        st.session_state["show_reset_confirm"] = True

    if st.session_state.get("show_reset_confirm"):
        st.warning("⚠️ O que fazer com o vídeo original em 'projeto_atual'?")
        col_res1, col_res2, col_res3 = st.columns(3)
        with col_res1:
            if st.button("🗑️ Apagar"):
                nuke_project(master_video_action="delete")
        with col_res2:
            if st.button("📁 Backup"):
                nuke_project(master_video_action="backup")
        with col_res3:
            if st.button("🔙 Cancelar"):
                st.session_state["show_reset_confirm"] = False
                st.rerun()

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

if available_jsons:
    st.markdown("### 📊 Status")
    cols = st.columns(len(available_jsons) + 1)
    for i, (key, label) in enumerate(available_jsons.items()):
        n_real = (
            len(list(Path(f"cuts/{key}").glob("*.mp4")))
            if Path(f"cuts/{key}").exists()
            else 0
        )
        cols[i].metric(label, f"{n_real} Prontos")

st.divider()

pipeline_cont = st.container()
with st.expander("📟 Terminal", expanded=False):
    log_ph = st.empty()
    log_ph.code(format_logs_for_display(), language="log")

with pipeline_cont:
    c1, c2, c3, c4 = st.columns(4)
    env = os.environ.copy()
    env.update(
        {
            "GROQ_API_KEY": os.getenv("GROQ_API_KEY", ""),
            "OPENROUTER_API_KEY": os.getenv("OPENROUTER_API_KEY", ""),
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
            "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),
            "OPENROUTER_MODEL": os.getenv(
                "OPENROUTER_MODEL", "google/gemini-2.0-flash-001"
            ),
            "VIDEO": str(video_path) if video_path else "",
            "VIDEO_LAYOUT": video_layout,
            "FX_MODE": fx_mode,
            "SUB_FONT": sub_font,
            "SUB_COLOR": sub_color,
            "SUB_UPPERCASE": "1" if sub_upper else "0",
            "MIRROR_MODE": "1" if mirror else "0",
            "PYTHONIOENCODING": "utf-8",
            "STRATEGIES": ",".join(active_strategies),
            "CHANNEL_TYPE": selected_niche_key,
        }
    )

    with c1:
        st.markdown("**1. Arquivo**")
        if not has_video:
            f = st.file_uploader("MP4", type=["mp4", "mov"])
            if f:
                new_n = f"video_master_{int(time.time())}.mp4"
                nuke_project(False)
                with open(WORKSPACE_DIR / new_n, "wb") as o:
                    o.write(f.getbuffer())
                st.rerun()
            u = st.text_input("YouTube URL")
            if u and st.button("Baixar"):
                nuke_project(False)
                with st.spinner("Baixando..."):
                    yt_opts = {
                        "format": "bestvideo[ext=mp4]+bestaudio/best",
                        "outtmpl": str(
                            WORKSPACE_DIR / "video_master_%(epoch)s.%(ext)s"
                        ),
                        "nopart": True,
                        "ffmpeg_location": FFMPEG_BINARY,
                        "merge_output_format": "mp4",
                        "cookiefile": "cookies.txt",
                    }
                    yt_dlp.YoutubeDL(yt_opts).download([u])
                    st.rerun()
        else:
            st.success("✅ Vídeo OK")
            st.button("Trocar")

    with c2:
        st.markdown("**2. Análise**")
        if has_video:
            if analysis_done:
                st.success("✅ OK")
                st.button("Refazer Análise", on_click=reset_analysis)
            else:
                if st.button("▶️ Iniciar"):
                    run_command_with_live_logs(
                        [sys.executable, "facecut.py"], env, log_ph
                    )
                    st.rerun()
        else:
            st.info("...")

    with c3:
        st.markdown("**3. Cortes**")
        if has_video:
            btn_label = "🔄 Atualizar Cortes" if available_jsons else "🤖 Gerar Cortes"
            if analysis_done:
                if st.button(btn_label):
                    run_command_with_live_logs(
                        [sys.executable, "segment_agent.py"], env, log_ph
                    )
                    st.rerun()
            else:
                st.button("🤖", disabled=True)
        else:
            st.info("...")

    with c4:
        st.markdown("**4. Render**")
        if available_jsons:
            render_choices = st.multiselect(
                "O que renderizar agora?",
                list(available_jsons.keys()),
                format_func=lambda x: available_jsons[x],
                default=list(available_jsons.keys()),
            )

            if st.button("🚀 RENDERIZAR", type="primary"):
                if not render_choices:
                    st.warning("Selecione pelo menos uma opção.")
                else:
                    for p in render_choices:
                        if p == "vertical":
                            env["CURRENT_PLATFORM"] = "vertical"
                            if os.path.exists("outputs/shorts.json"):
                                env["FACECUT_JSON"] = "outputs/shorts.json"
                            elif os.path.exists("vertical_cuts.json"):
                                env["FACECUT_JSON"] = "vertical_cuts.json"
                            else:
                                continue
                        elif p == "youtube":
                            env["CURRENT_PLATFORM"] = "youtube"
                            env["FACECUT_JSON"] = "youtube_cuts.json"

                        env["SUBS_DIR"] = f"subs/{p}"
                        env["OUTPUT_DIR"] = f"cuts/{p}"
                        os.makedirs(f"subs/{p}", exist_ok=True)
                        os.makedirs(f"cuts/{p}", exist_ok=True)

                        add_log(f"Iniciando renderização: {available_jsons[p]}", "info")
                        log_ph.code(format_logs_for_display(), language="log")

                        run_command_with_live_logs(
                            [sys.executable, "generate_karaoke_from_segment.py"],
                            env,
                            log_ph,
                        )
                        run_command_with_live_logs(
                            [sys.executable, "render_facecuts.py"], env, log_ph
                        )
                    st.balloons()
                    st.rerun()
        else:
            st.button("🚀", disabled=True)

        st.divider()
        st.markdown("**Vídeo Completo**")
        if analysis_done:
            if st.button(
                "🎞️ Renderizar Vídeo Inteiro",
                help="Aplica Anti-Copyright no vídeo original completo",
            ):
                env_full = env.copy()
                run_command_with_live_logs(
                    [sys.executable, "full_video_pipeline.py"], env_full, log_ph
                )
                st.success("Processamento do vídeo completo finalizado!")
                st.balloons()

if os.path.exists("cuts"):
    st.divider()
    st.header("📂 Galeria")
    tabs = st.tabs(["Vertical", "YouTube"])
    pmap = {"Vertical": "vertical", "YouTube": "youtube"}
    for t, p in zip(tabs, pmap):
        pth = Path("cuts") / pmap[p]
        with t:
            if pth.exists():
                fs = sorted(list(pth.glob("*.mp4")), key=os.path.getmtime, reverse=True)
                if fs:
                    c_all, c_none, c_rest = st.columns([0.15, 0.15, 0.7])
                    with c_all:
                        st.button(
                            "✅ Tudo",
                            key=f"s_{p}",
                            on_click=select_all_files,
                            args=(fs,),
                        )
                    with c_none:
                        st.button(
                            "⬜ Nada",
                            key=f"d_{p}",
                            on_click=deselect_all_files,
                            args=(fs,),
                        )

                    st.write("")
                    cols = st.columns(5)
                    for i, f in enumerate(fs):
                        with cols[i % 5]:
                            k = f"chk_{f}_{st.session_state.ui_reset_id}"
                            if k not in st.session_state:
                                st.session_state[k] = (
                                    str(f) in st.session_state["selected_files"]
                                )

                            smart_name = get_smart_name_for_file(str(f))
                            label = f"✅ {smart_name}" if smart_name else f"✅ {f.name}"
                            st.checkbox(
                                label,
                                key=k,
                                on_change=toggle_file_selection,
                                args=(f, k),
                            )
                            st.video(str(f))

# =========================================================
# SEÇÃO DE UPLOAD (DRIVE & MINIO)
# =========================================================
if st.session_state.selected_files:
    video_files = st.session_state.selected_files
    st.markdown("---")
    st.success(f"📦 {len(video_files)} Vídeos Selecionados")

    # === UPLOAD MINIO (NOVO E OTIMIZADO V172) ===
    with st.container():
        if st.button(
            "🚀 ENVIAR PARA YOUTUBE (Via MinIO)",
            type="primary",
            disabled=not MINIO_AVAILABLE,
        ):
            if not MINIO_AVAILABLE:
                st.error("MinIO não configurado ou minio_storage.py ausente.")
                st.stop()

            storage = get_minio_storage()
            if not storage:
                st.stop()

            # Carrega banco de descrições para encontrar metadados de cada vídeo
            desc_db = {}
            if os.path.exists("video_descriptions.json"):
                with open("video_descriptions.json", "r", encoding="utf-8") as f:
                    desc_db = json.load(f)

            pb = st.progress(0)

            for i, vf in enumerate(video_files):
                v_path = Path(vf)
                slug = v_path.stem

                meta = desc_db.get(slug)
                if not meta:
                    for k, v in desc_db.items():
                        if v.get("filename") == v_path.name:
                            meta = v
                            break

                if not meta:
                    meta = {
                        "title": slug,
                        "description": "Upload automático MinIO",
                        "tags": [],
                        "slug": slug,
                    }

                v_type = "shorts" if "vertical" in str(v_path) else "longs"

                # Estrutura MinIO: bucket/nicho/tipo/slug/
                remote_prefix = f"{selected_niche_key}/{v_type}/{slug}"

                # 1. Upload do Vídeo (COM NOME DO SLUG AGORA)
                # Mudança aqui: file.mp4 -> slug.mp4
                video_filename = f"{slug}.mp4"

                storage.upload_file(
                    bucket=minio_bucket,
                    file_path=v_path,
                    object_name=f"{remote_prefix}/{video_filename}",
                    content_type="video/mp4",
                    create_bucket=True,
                )

                # 2. Upload do JSON de Metadados
                # Adiciona o nome do arquivo no JSON para o n8n saber
                meta["video_filename"] = video_filename  # <--- IMPORTANTE PARA N8N

                meta_path = v_path.parent / f"{slug}_meta.json"
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=2, ensure_ascii=False)

                storage.upload_file(
                    bucket=minio_bucket,
                    file_path=meta_path,
                    object_name=f"{remote_prefix}/metadata.json",
                    content_type="application/json",
                )
                os.remove(meta_path)

                pb.progress((i + 1) / len(video_files))

            st.balloons()
            st.success("✅ Upload para MinIO concluído! O n8n deve processar em breve.")
            time.sleep(3)
            full_reset_selection()
            st.rerun()

    # === UPLOAD DRIVE (OCULTADO PARA PRODUTIZAÇÃO) ===
    # with col_drive:
    #     if st.button("☁️ Backup no Drive"):
    #         ... (comentado)
