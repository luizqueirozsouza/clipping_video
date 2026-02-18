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

# Importa serviço Google
try:
    import google_service

    SERVICE_AVAILABLE = True
except ImportError:
    SERVICE_AVAILABLE = False

load_dotenv(override=True)

st.set_page_config(
    page_title="AI Video Slicer (V130)",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==========================================
# CONFIGURAÇÕES
# ==========================================
WORKSPACE_DIR = Path("projeto_atual")
WORKSPACE_DIR.mkdir(exist_ok=True)
TOKENS_DIR = Path("tokens")
TOKENS_DIR.mkdir(exist_ok=True)

STRATEGY_MAP = {
    "Vídeos Curtos (Vertical)": "short_form",
    "Vídeos Longos (Horizontal)": "long_form",
}

# === NOVO: MAPEAMENTO DE NICHOS (V167) ===
# Mapeamento de Nichos (Front -> Back)
NICHE_MAP = {
    "✝️ Cristão (Way of the Master)": "christian",
    "🏛️ Política / Debate": "politics",
    "🔴⚫ Flamengo / Futebol": "flamengo",
    "₿ Bitcoin / Cripto": "bitcoin",
    "💸 Economia / Impostos": "economy"
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


def nuke_project(rerun=True):
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


def get_channel_profiles():
    return [f.stem.replace("token_", "") for f in TOKENS_DIR.glob("token_*.json")]


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


# ==========================================
# INTERFACE
# ==========================================
st.title("🎬 AI Video Slicer (V130)")
if not check_ffmpeg():
    st.error("❌ FFmpeg não encontrado!")
    st.stop()

with st.sidebar:
    st.header("⚙️ Configurações")

    with st.expander("🔑 Credenciais", expanded=True):
        groq_key = st.text_input(
            "Groq Key", value=os.getenv("GROQ_API_KEY", ""), type="password"
        )
        openrouter_key = st.text_input(
            "OpenRouter Key", value=os.getenv("OPENROUTER_API_KEY", ""), type="password"
        )

        model_options = {
            "google/gemini-2.0-flash-001": "⚡ Gemini 2.0 Flash (Stable)",
            "google/gemini-1.5-flash": "🛡️ Gemini 1.5 Flash (Backup)",
            "google/gemini-1.5-pro": "🧠 Gemini 1.5 Pro (Smart)",
            "openai/gpt-4o-mini": "🤖 GPT-4o Mini",
            "anthropic/claude-3.5-haiku": "📝 Claude 3.5 Haiku",
            "deepseek/deepseek-chat": "🇨🇳 DeepSeek V3",
        }
        selected_model_key = st.selectbox(
            "Modelo OpenRouter:",
            list(model_options.keys()),
            format_func=lambda x: model_options[x],
            index=0,
        )

        api_key = st.text_input(
            "OpenAI Key", value=os.getenv("OPENAI_API_KEY", ""), type="password"
        )
        gemini_key = st.text_input(
            "Gemini Key", value=os.getenv("GEMINI_API_KEY", ""), type="password"
        )
    
    # === NOVO SELETOR DE NICHO (V167) ===
    st.markdown("---")
    st.subheader("🎭 Persona / Nicho")
    selected_niche_ui = st.selectbox(
        "Estilo da Descrição:",
        list(NICHE_MAP.keys()),
        index=0,
        help="Define o tom de voz e a estrutura que a IA usará para criar as descrições."
    )
    selected_niche_key = NICHE_MAP[selected_niche_ui]
    st.caption(f"Modo Ativo: {selected_niche_key.upper()}")
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

    st.divider()
    st.subheader("☁️ Drive")
    profiles = get_channel_profiles()
    selected_profile = st.selectbox("Conta:", profiles) if profiles else "default"

    with st.expander("➕ Nova Conta"):
        new_profile = st.text_input("Nome", placeholder="Ex: MeuDrive")
        if st.button("Autenticar"):
            tk = TOKENS_DIR / f"token_{new_profile}.json"
            if tk.exists():
                os.remove(tk)
            try:
                google_service.get_authenticated_creds(str(tk))
                st.success("OK!")
                st.rerun()
            except:
                st.error("Erro Auth")

    drive_folder_id = st.text_input(
        "ID Pasta", value=os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    )

    if st.button("🚨 RESET TOTAL (Apaga Tudo)", type="primary"):
        nuke_project(rerun=True)

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
            "GROQ_API_KEY": groq_key,
            "OPENROUTER_API_KEY": openrouter_key,
            "OPENAI_API_KEY": api_key,
            "GEMINI_API_KEY": gemini_key,
            "OPENROUTER_MODEL": selected_model_key,
            "VIDEO": str(video_path) if video_path else "",
            "VIDEO_LAYOUT": video_layout,
            "FX_MODE": fx_mode,
            "SUB_FONT": sub_font,
            "SUB_COLOR": sub_color,
            "SUB_UPPERCASE": "1" if sub_upper else "0",
            "MIRROR_MODE": "1" if mirror else "0",
            "PYTHONIOENCODING": "utf-8",
            "GOOGLE_DRIVE_FOLDER_ID": drive_folder_id,
            "STRATEGIES": ",".join(active_strategies),
            # === ENVIA A ESCOLHA DO NICHO PARA OS AGENTES ===
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
                    # COMO DEVE FICAR (V167 + Fix Cookies File):
                    yt_opts = {
                        "format": "bestvideo[ext=mp4]+bestaudio/best",
                        "outtmpl": str(
                            WORKSPACE_DIR / "video_master_%(epoch)s.%(ext)s"
                        ),
                        "nopart": True,
                        "ffmpeg_location": FFMPEG_BINARY,
                        "merge_output_format": "mp4",
                        
                        # === MUDANÇA AQUI: Usa o arquivo em vez de tentar ler do Chrome aberto ===
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
        
        # === BOTÃO VÍDEO COMPLETO ===
        st.divider()
        st.markdown("**Vídeo Completo**")
        if analysis_done:
            if st.button("🎞️ Renderizar Vídeo Inteiro", help="Aplica Anti-Copyright no vídeo original completo"):
                env_full = env.copy()
                run_command_with_live_logs(
                    [sys.executable, "full_video_pipeline.py"], 
                    env_full, 
                    log_ph
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
# SEÇÃO DE UPLOAD
# =========================================================
if st.session_state.selected_files:
    jsons_to_upload = []
    video_files = st.session_state.selected_files

    has_vertical = any("vertical" in f for f in video_files)
    has_youtube = any("youtube" in f for f in video_files)

    if has_vertical:
        if os.path.exists("vertical_cuts.json"):
            jsons_to_upload.append("vertical_cuts.json")
        elif os.path.exists("outputs/shorts.json"):
            jsons_to_upload.append("outputs/shorts.json")

    if has_youtube:
        if os.path.exists("youtube_cuts.json"):
            jsons_to_upload.append("youtube_cuts.json")

    if os.path.exists("video_descriptions.json"):
        jsons_to_upload.append("video_descriptions.json")

    json_msg = f"(+ {len(jsons_to_upload)} metadados)" if jsons_to_upload else ""
    st.success(f"📦 {len(video_files)} Vídeos {json_msg}")

    if st.button("🚀 UPLOAD DRIVE", type="primary"):
        tk = str(TOKENS_DIR / f"token_{selected_profile}.json")
        if selected_profile == "default" and not os.path.exists(tk):
            tk = "token.json"
        if not os.path.exists(tk):
            st.error("Erro Auth")
            st.stop()

        pb = st.progress(0)
        tot = len(video_files) + len(jsons_to_upload)
        counter = 0

        for f in video_files:
            smart_name = get_smart_name_for_file(f)
            google_service.upload_to_drive(
                f, drive_folder_id, token_file=tk, rename_to=smart_name
            )
            counter += 1
            pb.progress(counter / tot)

        for j in jsons_to_upload:
            google_service.upload_to_drive(j, drive_folder_id, token_file=tk)
            counter += 1
            pb.progress(counter / tot)

        st.success("Upload Concluído! ☁️")
        time.sleep(2)
        full_reset_selection()
        st.rerun()