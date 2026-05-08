import gradio as gr
import ollama
import os
import json
import re
from datetime import datetime

# --- Configuration ---
MODEL_NAME = os.getenv("OLLAMA_MODEL", "mia:latest")
LOG_FILE   = os.path.join("logs", "queries.jsonl")
os.makedirs("logs", exist_ok=True)

SYSTEM_PROMPT = """You are MIA (Medical Intake Assistant), a clinical triage support tool \
assisting registered nurses in a busy clinical setting. Your role is to:

1. Assess the urgency of the patient's chief complaint based on reported symptoms.
2. Ask one or two focused clarifying questions if critical information is missing \
   (onset, severity 1-10, associated symptoms, relevant history).
3. Suggest the most likely triage category: LOW, MODERATE, or HIGH urgency.
4. Recommend immediate nursing actions appropriate to the urgency level.
5. Flag any red-flag symptoms that require immediate physician notification.

Always be concise, clinically precise, and professional. Never diagnose; you support triage \
decision-making only. End every response with a structured summary in this exact format:
TRIAGE: [LOW|MODERATE|HIGH] — [one-sentence rationale]"""

HIGH_KEYWORDS = [
    "chest pain", "difficulty breathing", "shortness of breath",
    "unresponsive", "unconscious", "stroke", "seizure", "severe bleeding",
    "anaphylaxis", "sepsis", "altered mental status", "crushing",
    "radiating", "diaphoresis", "syncope", "fainted", "cannot breathe",
    "cyanosis", "cyanotic", "intubat", "code blue",
]

MODERATE_KEYWORDS = [
    "fever", "vomiting", "moderate pain", "dizziness", "dehydration",
    "laceration", "fracture", "abdominal pain", "headache", "migraine",
    "urinary", "infection", "cellulitis", "tachycardia", "hypertension",
    "nausea", "weakness", "fall", "injury", "elevated",
]

CUSTOM_CSS = """
body { background-color: #f0f4f8; font-family: 'Segoe UI', system-ui, sans-serif; }

.app-header {
    background: linear-gradient(135deg, #0a1628 0%, #0d2137 100%);
    color: #ffffff;
    padding: 20px 28px;
    border-radius: 12px;
    margin-bottom: 16px;
    border-left: 5px solid #00b4b4;
}
.app-header h1 { margin: 0 0 4px 0; font-size: 1.6rem; letter-spacing: 0.5px; }
.app-header p  { margin: 0; color: #8ecae6; font-size: 0.85rem; }

.intake-card {
    background: #ffffff;
    border: 1px solid #d1dce8;
    border-radius: 10px;
    padding: 18px 22px;
    margin-bottom: 12px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}

.disclaimer {
    background: #fff7ed;
    border-left: 4px solid #f97316;
    padding: 10px 16px;
    border-radius: 6px;
    font-size: 0.82rem;
    color: #7c3a00;
    margin-top: 12px;
}

footer { visibility: hidden !important; }
"""


# ── Helper functions ─────────────────────────────────────────────────────────

def extract_text(content) -> str:
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and "text" in item:
                return item["text"]
    elif isinstance(content, dict):
        return content.get("text", str(content))
    return str(content)


def detect_triage_level(text: str) -> tuple:
    lower = text.lower()
    match = re.search(r"triage:\s*(low|moderate|high)", lower)
    if match:
        level = match.group(1).upper()
    elif any(kw in lower for kw in HIGH_KEYWORDS):
        level = "HIGH"
    elif any(kw in lower for kw in MODERATE_KEYWORDS):
        level = "MODERATE"
    else:
        level = "LOW"
    badges = {
        "HIGH":     "🔴 **HIGH URGENCY**",
        "MODERATE": "🟡 **MODERATE URGENCY**",
        "LOW":      "🟢 **LOW URGENCY**",
    }
    return level, badges[level]


def build_system_context(name: str, age: str, gender: str, complaint: str) -> str:
    parts = [
        "=== CURRENT PATIENT ===",
        f"Name: {name.strip() or 'Unknown'}",
        f"Age: {age.strip() or 'Not provided'}",
        f"Gender: {gender or 'Not specified'}",
        f"Chief Complaint: {complaint.strip() or 'Not provided'}",
        "=== END PATIENT DATA ===",
        "",
        SYSTEM_PROMPT,
    ]
    return "\n".join(parts)


def check_ollama_available() -> tuple:
    try:
        ollama.list()
        return True, ""
    except Exception:
        msg = (
            "**Ollama is not running or not installed.**\n\n"
            "To fix this:\n"
            "1. Install Ollama from https://ollama.com/download\n"
            "2. Start it — open a terminal and run: `ollama serve`\n"
            f"3. Pull the model: `ollama pull {MODEL_NAME}`\n"
            "4. Refresh this page."
        )
        return False, msg


def log_interaction(patient_info: dict, question: str, answer: str, level: str):
    record = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "model": MODEL_NAME,
        "patient": patient_info,
        "triage_level": level,
        "question": question,
        "answer": answer,
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Core chat function ───────────────────────────────────────────────────────

def chat(message, history, system_ctx, pat_name, pat_age, pat_gender, pat_complaint):
    if not message.strip():
        return history, "", system_ctx

    if not system_ctx:
        system_ctx = build_system_context(pat_name, pat_age, pat_gender, pat_complaint)

    ok, err_msg = check_ollama_available()
    if not ok:
        error_history = (history or []) + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": err_msg},
        ]
        return error_history, "", system_ctx

    ollama_messages = [{"role": "system", "content": system_ctx}]
    for msg in (history or []):
        role = msg.get("role", "user")
        content = extract_text(msg.get("content", ""))
        ollama_messages.append({"role": role, "content": content})
    ollama_messages.append({"role": "user", "content": message})

    try:
        response = ollama.chat(model=MODEL_NAME, messages=ollama_messages)
        raw_answer = response["message"]["content"].strip()
    except Exception as e:
        raw_answer = f"Model error: {str(e)}"

    level, badge = detect_triage_level(raw_answer)

    final_answer = (
        f"{badge}\n\n"
        f"{raw_answer}\n\n"
        "---\n"
        "⚠️ **[DIL REQUIRED]** — All triage recommendations must be verified by a licensed physician."
    )

    log_interaction(
        patient_info={
            "name": pat_name, "age": pat_age,
            "gender": pat_gender, "complaint": pat_complaint,
        },
        question=message,
        answer=raw_answer,
        level=level,
    )

    new_history = (history or []) + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": final_answer},
    ]
    return new_history, "", system_ctx


def clear_session():
    # Returns: (chatbot, system_ctx, pat_name, pat_age, pat_gender, pat_complaint, msg)
    return [], "", "", "", "Female", "", ""


# ── UI ───────────────────────────────────────────────────────────────────────

with gr.Blocks(title="MIA — Medical Intake Assistant", css=CUSTOM_CSS) as app:

    system_ctx = gr.State("")

    gr.HTML("""
    <div class="app-header">
      <h1>MIA &mdash; Medical Intake Assistant</h1>
      <p>AI-assisted triage support &bull; For registered nursing staff only &bull; Not for clinical diagnosis</p>
    </div>
    """)

    gr.HTML('<div class="intake-card">')
    gr.Markdown("### Patient Intake")
    with gr.Row():
        pat_name   = gr.Textbox(label="Patient Name", placeholder="Full name", scale=3)
        pat_age    = gr.Textbox(label="Age", placeholder="e.g. 45", scale=1)
        pat_gender = gr.Radio(
            choices=["Female", "Male", "Non-binary", "Prefer not to say"],
            label="Gender", value="Female", scale=2,
        )
    pat_complaint = gr.Textbox(
        label="Chief Complaint",
        placeholder="Describe the primary reason for visit...",
        lines=2,
    )
    gr.HTML('</div>')

    gr.Markdown("**Quick Scenarios** — click to pre-fill, then review and press Send")
    with gr.Row():
        qb1 = gr.Button("🤒 Fever + Cough")
        qb2 = gr.Button("💔 Chest Pain")
        qb3 = gr.Button("🤢 Abdominal Pain")
        qb4 = gr.Button("🤕 Headache")
        qb5 = gr.Button("🩹 Fall / Injury")
        qb6 = gr.Button("😮‍💨 Shortness of Breath")

    chatbot = gr.Chatbot(
        label="Triage Conversation",
        height=380,
        type="messages",
        show_copy_button=True,
    )
    with gr.Row():
        msg = gr.Textbox(
            label="",
            placeholder="Describe patient symptoms or ask a triage question...",
            scale=9,
            lines=1,
            show_label=False,
        )
        btn_send = gr.Button("Send", scale=1, variant="primary")

    btn_clear = gr.Button("Clear / New Patient", variant="secondary")

    gr.HTML("""
    <div class="disclaimer">
      <strong>Clinical Disclaimer:</strong> MIA is a decision-support tool only.
      All outputs require verification by a licensed physician before any clinical action.
      Do not use for unsupervised diagnosis or treatment decisions.
    </div>
    """)

    # Event wiring
    btn_send.click(
        fn=chat,
        inputs=[msg, chatbot, system_ctx, pat_name, pat_age, pat_gender, pat_complaint],
        outputs=[chatbot, msg, system_ctx],
    )
    msg.submit(
        fn=chat,
        inputs=[msg, chatbot, system_ctx, pat_name, pat_age, pat_gender, pat_complaint],
        outputs=[chatbot, msg, system_ctx],
    )

    qb1.click(lambda: "Patient presents with fever and productive cough.", None, msg)
    qb2.click(lambda: "Patient reports chest pain, severity 7/10, onset 20 minutes ago.", None, msg)
    qb3.click(lambda: "Patient has abdominal pain, periumbilical, onset 3 hours ago.", None, msg)
    qb4.click(lambda: "Patient reports severe headache, worst of life, sudden onset.", None, msg)
    qb5.click(lambda: "Patient fell from standing height, right hip pain, limited mobility.", None, msg)
    qb6.click(lambda: "Patient experiencing shortness of breath, oxygen saturation unknown.", None, msg)

    btn_clear.click(
        fn=clear_session,
        inputs=[],
        outputs=[chatbot, system_ctx, pat_name, pat_age, pat_gender, pat_complaint, msg],
    )

app.launch(server_name="0.0.0.0", server_port=7870)
