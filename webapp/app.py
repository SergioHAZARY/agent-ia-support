"""
Agent IA Support Multicanal — Interface Web
Chat avec les deux agents (Support IT et Admin) via n8n.
"""

import os
import time
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
N8N_BASE = os.getenv("N8N_WEBHOOK_BASE", "https://dev-ai.app.n8n.cloud")
WEBHOOK_SUPPORT = f"{N8N_BASE}/webhook/agent-support-web"
WEBHOOK_ADMIN = f"{N8N_BASE}/webhook/agent-admin-web"

AGENTS = {
    "support": {
        "name": "Agent Support IT",
        "icon": "🤖",
        "webhook": WEBHOOK_SUPPORT,
        "placeholder": "Décrivez votre problème IT...",
        "description": (
            "L'agent de support IT traite vos demandes : accès, comptes, "
            "réseau, messagerie, postes de travail, applicatifs..."
        ),
    },
    "admin": {
        "name": "Agent Admin",
        "icon": "📊",
        "webhook": WEBHOOK_ADMIN,
        "placeholder": "Ex: tickets beautybay, export bazarchic, stats...",
        "description": (
            "L'agent admin consulte la base de tickets : statistiques, "
            "filtrage par société/canal/priorité, export CSV."
        ),
    },
}

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Agent IA Support",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    .stApp { max-width: 1200px; margin: 0 auto; }
    .agent-header {
        background: linear-gradient(135deg, #0066FF 0%, #00D4AA 100%);
        padding: 1.5rem 2rem;
        border-radius: 12px;
        margin-bottom: 1rem;
        color: white;
    }
    .agent-header h2 { margin: 0; color: white; }
    .agent-header p { margin: 0.5rem 0 0 0; opacity: 0.9; }
    .status-badge {
        display: inline-block;
        padding: 0.2rem 0.8rem;
        border-radius: 20px;
        font-size: 0.8rem;
        font-weight: 600;
    }
    .status-online { background: #00D4AA; color: #0E1117; }
    .status-offline { background: #FF4B4B; color: white; }
    div[data-testid="stChatMessage"] {
        border-radius: 12px;
        margin-bottom: 0.5rem;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
if "agent" not in st.session_state:
    st.session_state.agent = "support"
if "messages" not in st.session_state:
    st.session_state.messages = {"support": [], "admin": []}
if "session_id" not in st.session_state:
    st.session_state.session_id = f"web-{int(time.time())}"

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 🤖 Agents disponibles")
    st.markdown("---")

    for key, agent in AGENTS.items():
        is_active = st.session_state.agent == key
        label = f"{agent['icon']} {agent['name']}"
        if is_active:
            label += " ✓"
        if st.button(label, key=f"btn_{key}", use_container_width=True,
                     type="primary" if is_active else "secondary"):
            st.session_state.agent = key
            st.rerun()

    st.markdown("---")
    st.markdown("### Sociétés")
    st.markdown(
        "BeautyBay · Bazarchic · Bouchara · "
        "Atlas For Men · François Saget · IT Support Liban"
    )
    st.markdown("---")
    st.caption("Propulsé par Claude + n8n")

    if st.button("🗑️ Effacer la conversation", use_container_width=True):
        st.session_state.messages[st.session_state.agent] = []
        st.rerun()

# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------
agent_key = st.session_state.agent
agent = AGENTS[agent_key]
messages = st.session_state.messages[agent_key]

st.markdown(f"""
<div class="agent-header">
    <h2>{agent['icon']} {agent['name']}</h2>
    <p>{agent['description']}</p>
    <span class="status-badge status-online">● En ligne</span>
</div>
""", unsafe_allow_html=True)

# Display chat history
for msg in messages:
    with st.chat_message(msg["role"], avatar=agent["icon"] if msg["role"] == "assistant" else "👤"):
        st.markdown(msg["content"])
        if msg.get("metadata"):
            meta = msg["metadata"]
            cols = st.columns(4)
            if meta.get("ticket_ref"):
                cols[0].caption(f"🎫 {meta['ticket_ref']}")
            if meta.get("category"):
                cols[1].caption(f"📁 {meta['category']}")
            if meta.get("priority"):
                cols[2].caption(f"🔴 {meta['priority']}")
            if meta.get("autonomy_level"):
                cols[3].caption(f"🤖 {meta['autonomy_level']}")

# Chat input
if prompt := st.chat_input(agent["placeholder"]):
    messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar="👤"):
        st.markdown(prompt)

    with st.chat_message("assistant", avatar=agent["icon"]):
        with st.spinner("L'agent analyse votre demande..."):
            try:
                payload = {
                    "platform": "web",
                    "text": prompt,
                    "channel_id": f"web-{agent_key}",
                    "user_name": st.session_state.session_id,
                    "user_id": st.session_state.session_id,
                    "message_id": f"web-{int(time.time() * 1000)}",
                }
                resp = requests.post(
                    agent["webhook"],
                    json=payload,
                    timeout=120,
                    headers={"Content-Type": "application/json"},
                )

                if resp.status_code == 200:
                    data = resp.json() if resp.headers.get(
                        "content-type", ""
                    ).startswith("application/json") else {"reply": resp.text}

                    reply_text = (
                        data.get("reply_text")
                        or data.get("reply")
                        or data.get("text")
                        or data.get("message")
                        or str(data)
                    )
                    metadata = {
                        k: data.get(k)
                        for k in ("ticket_ref", "category", "priority",
                                  "autonomy_level", "resolution_status")
                        if data.get(k)
                    }
                else:
                    reply_text = (
                        f"⚠️ Erreur {resp.status_code} du serveur n8n.\n\n"
                        "L'agent n'est peut-être pas encore actif. "
                        "Vérifiez que le workflow est activé sur n8n."
                    )
                    metadata = {}

            except requests.exceptions.Timeout:
                reply_text = (
                    "⏱️ Le traitement prend plus de temps que prévu. "
                    "Votre demande a été enregistrée, un technicien la traitera."
                )
                metadata = {}
            except requests.exceptions.ConnectionError:
                reply_text = (
                    "🔌 Impossible de joindre le serveur n8n.\n\n"
                    "Vérifiez la variable `N8N_WEBHOOK_BASE` ou que "
                    "l'instance n8n est démarrée."
                )
                metadata = {}
            except Exception as exc:
                reply_text = f"❌ Erreur inattendue : {exc}"
                metadata = {}

        st.markdown(reply_text)
        if metadata:
            cols = st.columns(4)
            if metadata.get("ticket_ref"):
                cols[0].caption(f"🎫 {metadata['ticket_ref']}")
            if metadata.get("category"):
                cols[1].caption(f"📁 {metadata['category']}")
            if metadata.get("priority"):
                cols[2].caption(f"🔴 {metadata['priority']}")
            if metadata.get("autonomy_level"):
                cols[3].caption(f"🤖 {metadata['autonomy_level']}")

    messages.append({
        "role": "assistant",
        "content": reply_text,
        "metadata": metadata,
    })
