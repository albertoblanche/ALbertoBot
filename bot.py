import os
import re
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

API_URL = os.environ["API_URL"]  # e.g. https://tu-api.railway.app

# ── helpers ──────────────────────────────────────────────────────────────────

def get_tasks():
    r = requests.get(f"{API_URL}/tasks", timeout=10)
    r.raise_for_status()
    return r.json()

def create_task(text, status="todo", priority=5):
    r = requests.post(f"{API_URL}/tasks",
                      json={"text": text, "status": status, "priority": priority},
                      timeout=10)
    r.raise_for_status()
    return r.json()

def update_task(task_id, **kwargs):
    r = requests.patch(f"{API_URL}/tasks/{task_id}", json=kwargs, timeout=10)
    r.raise_for_status()
    return r.json()

def delete_task(task_id):
    r = requests.delete(f"{API_URL}/tasks/{task_id}", timeout=10)
    r.raise_for_status()

def format_tasks(tasks):
    if not tasks:
        return "No hay tareas todavía."
    status_labels = {"todo": "📋 Por hacer", "doing": "🔄 En progreso", "done": "✅ Hecho"}
    sections = {}
    for t in tasks:
        s = t["status"]
        sections.setdefault(s, []).append(t)
    lines = []
    for s in ["todo", "doing", "done"]:
        if s not in sections:
            continue
        lines.append(f"\n{status_labels[s]}:")
        for t in sections[s]:
            p = t["priority"]
            lines.append(f"  [{t['id']}] P{p} — {t['text']}")
    return "\n".join(lines).strip()

def find_task_by_words(tasks, words):
    words_lower = words.lower()
    for t in tasks:
        if words_lower in t["text"].lower():
            return t
    return None

# ── intent detection ─────────────────────────────────────────────────────────

def detect_intent(text):
    t = text.lower().strip()

    # listar
    if any(w in t for w in ["lista", "listar", "ver", "muestra", "mostrar", "qué tengo", "que tengo", "todas"]):
        return "list", {}

    # agregar
    add_match = re.search(r"(agrega|agregar|añade|añadir|crea|crear|nueva tarea|nuevo)\s+(.+)", t)
    if add_match:
        content = add_match.group(2).strip()
        priority = 5
        p_match = re.search(r"prioridad\s*(\d+)", content)
        if p_match:
            priority = max(1, min(10, int(p_match.group(1))))
            content = content[:p_match.start()].strip()
        status = "todo"
        if "en progreso" in content:
            status = "doing"; content = content.replace("en progreso", "").strip()
        return "create", {"text": content, "priority": priority, "status": status}

    # completar / marcar hecho
    done_match = re.search(r"(completa|completar|marca|marcar|termina|terminar|hecho|done)\s+(.+)", t)
    if done_match:
        return "done", {"query": done_match.group(2).strip()}

    # mover a en progreso
    doing_match = re.search(r"(empieza|empezar|inicia|iniciar|en progreso)\s+(.+)", t)
    if doing_match:
        return "doing", {"query": doing_match.group(2).strip()}

    # eliminar
    del_match = re.search(r"(elimina|eliminar|borra|borrar|quita|quitar|delete)\s+(.+)", t)
    if del_match:
        return "delete", {"query": del_match.group(2).strip()}

    # editar texto
    edit_match = re.search(r"(edita|editar|cambia|cambiar|renombra|renombrar)\s+(.+?)\s+(a|por|como)\s+(.+)", t)
    if edit_match:
        return "edit", {"query": edit_match.group(2).strip(), "new_text": edit_match.group(4).strip()}

    # prioridad
    prio_match = re.search(r"(prioridad|priority)\s+(\d+)\s+(a|para|de)\s+(.+)", t)
    if prio_match:
        return "priority", {"priority": int(prio_match.group(2)), "query": prio_match.group(4).strip()}

    return "unknown", {}

# ── handlers ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hola! Soy tu asistente de tareas. Puedes decirme cosas como:\n\n"
        "• *Agrega revisar el informe con prioridad 8*\n"
        "• *Muestra mis tareas*\n"
        "• *Completa revisar el informe*\n"
        "• *Elimina revisar el informe*\n"
        "• *Edita revisar el informe por revisar informe final*\n"
        "• *Empieza revisar el informe* (→ en progreso)\n",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    intent, params = detect_intent(text)

    try:
        if intent == "list":
            tasks = get_tasks()
            await update.message.reply_text(format_tasks(tasks))

        elif intent == "create":
            task = create_task(params["text"], params["status"], params["priority"])
            await update.message.reply_text(
                f"Tarea creada [ID {task['id']}]:\n*{task['text']}* — P{task['priority']}",
                parse_mode="Markdown"
            )

        elif intent in ("done", "doing"):
            tasks = get_tasks()
            task = find_task_by_words(tasks, params["query"])
            if not task:
                await update.message.reply_text(f"No encontré ninguna tarea con '{params['query']}'.")
                return
            new_status = "done" if intent == "done" else "doing"
            update_task(task["id"], status=new_status)
            label = "completada" if new_status == "done" else "movida a en progreso"
            await update.message.reply_text(f"Tarea {label}: *{task['text']}*", parse_mode="Markdown")

        elif intent == "delete":
            tasks = get_tasks()
            task = find_task_by_words(tasks, params["query"])
            if not task:
                await update.message.reply_text(f"No encontré ninguna tarea con '{params['query']}'.")
                return
            delete_task(task["id"])
            await update.message.reply_text(f"Tarea eliminada: *{task['text']}*", parse_mode="Markdown")

        elif intent == "edit":
            tasks = get_tasks()
            task = find_task_by_words(tasks, params["query"])
            if not task:
                await update.message.reply_text(f"No encontré ninguna tarea con '{params['query']}'.")
                return
            update_task(task["id"], text=params["new_text"])
            await update.message.reply_text(
                f"Tarea actualizada:\n*{params['new_text']}*", parse_mode="Markdown"
            )

        elif intent == "priority":
            tasks = get_tasks()
            task = find_task_by_words(tasks, params["query"])
            if not task:
                await update.message.reply_text(f"No encontré ninguna tarea con '{params['query']}'.")
                return
            p = max(1, min(10, params["priority"]))
            update_task(task["id"], priority=p)
            await update.message.reply_text(
                f"Prioridad de *{task['text']}* cambiada a P{p}.", parse_mode="Markdown"
            )

        else:
            await update.message.reply_text(
                "No entendí. Prueba con:\n"
                "• *Agrega [tarea]* \n• *Muestra mis tareas*\n"
                "• *Completa [tarea]*\n• *Elimina [tarea]*",
                parse_mode="Markdown"
            )

    except Exception as e:
        await update.message.reply_text(f"Ocurrió un error: {str(e)}")

# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    token = os.environ["TELEGRAM_TOKEN"]
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot corriendo...")
    app.run_polling()
