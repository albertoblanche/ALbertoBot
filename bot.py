import os
import re
import threading
import requests
import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request
from flask_cors import CORS
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import asyncio

# ── Flask API ─────────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app)

def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])

@app.route("/tasks", methods=["GET"])
def get_tasks_api():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM tasks ORDER BY (status='done'), priority DESC, created_at ASC")
    tasks = list(cur.fetchall())
    for t in tasks:
        if t.get("created_at"):
            t["created_at"] = t["created_at"].isoformat()
    cur.close(); conn.close()
    return jsonify(tasks)

@app.route("/tasks", methods=["POST"])
def create_task_api():
    data = request.json
    text = data.get("text", "").strip()
    status = data.get("status", "todo")
    priority = int(data.get("priority", 5))
    if not text:
        return jsonify({"error": "text required"}), 400
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "INSERT INTO tasks (text, status, priority) VALUES (%s, %s, %s) RETURNING *",
        (text, status, priority)
    )
    task = dict(cur.fetchone())
    if task.get("created_at"):
        task["created_at"] = task["created_at"].isoformat()
    conn.commit(); cur.close(); conn.close()
    return jsonify(task), 201

@app.route("/tasks/<int:task_id>", methods=["PATCH"])
def update_task_api(task_id):
    data = request.json
    fields = []; values = []
    if "text" in data:
        fields.append("text = %s"); values.append(data["text"])
    if "status" in data:
        fields.append("status = %s"); values.append(data["status"])
    if "priority" in data:
        fields.append("priority = %s"); values.append(int(data["priority"]))
    if not fields:
        return jsonify({"error": "nothing to update"}), 400
    values.append(task_id)
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = %s RETURNING *", values)
    row = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    task = dict(row)
    if task.get("created_at"):
        task["created_at"] = task["created_at"].isoformat()
    return jsonify(task)

@app.route("/tasks/<int:task_id>", methods=["DELETE"])
def delete_task_api(task_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

# ── Bot helpers ───────────────────────────────────────────────────────────────

def bot_get_tasks():
    port = os.environ.get("PORT", 5000)
    r = requests.get(f"http://localhost:{port}/tasks", timeout=10)
    r.raise_for_status()
    return r.json()

def bot_create_task(text, status="todo", priority=5):
    port = os.environ.get("PORT", 5000)
    r = requests.post(f"http://localhost:{port}/tasks",
                      json={"text": text, "status": status, "priority": priority},
                      timeout=10)
    r.raise_for_status()
    return r.json()

def bot_update_task(task_id, **kwargs):
    port = os.environ.get("PORT", 5000)
    r = requests.patch(f"http://localhost:{port}/tasks/{task_id}", json=kwargs, timeout=10)
    r.raise_for_status()
    return r.json()

def bot_delete_task(task_id):
    port = os.environ.get("PORT", 5000)
    r = requests.delete(f"http://localhost:{port}/tasks/{task_id}", timeout=10)
    r.raise_for_status()

def format_tasks(tasks):
    if not tasks:
        return "No hay tareas todavía."
    status_labels = {"todo": "📋 Por hacer", "doing": "🔄 En progreso", "done": "✅ Hecho"}
    sections = {}
    for t in tasks:
        sections.setdefault(t["status"], []).append(t)
    lines = []
    for s in ["todo", "doing", "done"]:
        if s not in sections:
            continue
        lines.append(f"\n{status_labels[s]}:")
        for t in sections[s]:
            lines.append(f"  [{t['id']}] P{t['priority']} — {t['text']}")
    return "\n".join(lines).strip()

def find_task(tasks, query):
    query_lower = query.lower()
    for t in tasks:
        if query_lower in t["text"].lower():
            return t
    return None

def detect_intent(text):
    t = text.lower().strip()
    if any(w in t for w in ["lista", "listar", "ver", "muestra", "mostrar", "qué tengo", "que tengo", "todas"]):
        return "list", {}
    m = re.search(r"(agrega|agregar|añade|añadir|crea|crear|nueva tarea|nuevo)\s+(.+)", t)
    if m:
        content = m.group(2).strip()
        priority = 5
        pm = re.search(r"prioridad\s*(\d+)", content)
        if pm:
            priority = max(1, min(10, int(pm.group(1))))
            content = content[:pm.start()].strip()
        status = "todo"
        if "en progreso" in content:
            status = "doing"; content = content.replace("en progreso", "").strip()
        return "create", {"text": content, "priority": priority, "status": status}
    m = re.search(r"(completa|completar|marca|marcar|termina|terminar|hecho|done)\s+(.+)", t)
    if m:
        return "done", {"query": m.group(2).strip()}
    m = re.search(r"(empieza|empezar|inicia|iniciar|en progreso)\s+(.+)", t)
    if m:
        return "doing", {"query": m.group(2).strip()}
    m = re.search(r"(elimina|eliminar|borra|borrar|quita|quitar|delete)\s+(.+)", t)
    if m:
        return "delete", {"query": m.group(2).strip()}
    m = re.search(r"(edita|editar|cambia|cambiar|renombra|renombrar)\s+(.+?)\s+(a|por|como)\s+(.+)", t)
    if m:
        return "edit", {"query": m.group(2).strip(), "new_text": m.group(4).strip()}
    m = re.search(r"prioridad\s+(\d+)\s+(a|para|de)\s+(.+)", t)
    if m:
        return "priority", {"priority": int(m.group(1)), "query": m.group(3).strip()}
    return "unknown", {}

# ── Bot handlers ──────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hola! Soy tu asistente de tareas. Puedes decirme:\n\n"
        "• *Agrega revisar el informe con prioridad 8*\n"
        "• *Muestra mis tareas*\n"
        "• *Completa revisar el informe*\n"
        "• *Elimina revisar el informe*\n"
        "• *Edita revisar el informe por revisar informe final*\n"
        "• *Empieza revisar el informe*\n",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    intent, params = detect_intent(text)
    try:
        if intent == "list":
            tasks = bot_get_tasks()
            await update.message.reply_text(format_tasks(tasks))
        elif intent == "create":
            task = bot_create_task(params["text"], params["status"], params["priority"])
            await update.message.reply_text(
                f"Tarea creada [ID {task['id']}]:\n*{task['text']}* — P{task['priority']}",
                parse_mode="Markdown"
            )
        elif intent in ("done", "doing"):
            tasks = bot_get_tasks()
            task = find_task(tasks, params["query"])
            if not task:
                await update.message.reply_text(f"No encontré ninguna tarea con '{params['query']}'.")
                return
            new_status = "done" if intent == "done" else "doing"
            bot_update_task(task["id"], status=new_status)
            label = "completada ✅" if new_status == "done" else "movida a en progreso 🔄"
            await update.message.reply_text(f"Tarea {label}:\n*{task['text']}*", parse_mode="Markdown")
        elif intent == "delete":
            tasks = bot_get_tasks()
            task = find_task(tasks, params["query"])
            if not task:
                await update.message.reply_text(f"No encontré ninguna tarea con '{params['query']}'.")
                return
            bot_delete_task(task["id"])
            await update.message.reply_text(f"Tarea eliminada: *{task['text']}*", parse_mode="Markdown")
        elif intent == "edit":
            tasks = bot_get_tasks()
            task = find_task(tasks, params["query"])
            if not task:
                await update.message.reply_text(f"No encontré ninguna tarea con '{params['query']}'.")
                return
            bot_update_task(task["id"], text=params["new_text"])
            await update.message.reply_text(
                f"Tarea actualizada:\n*{params['new_text']}*", parse_mode="Markdown"
            )
        elif intent == "priority":
            tasks = bot_get_tasks()
            task = find_task(tasks, params["query"])
            if not task:
                await update.message.reply_text(f"No encontré ninguna tarea con '{params['query']}'.")
                return
            p = max(1, min(10, params["priority"]))
            bot_update_task(task["id"], priority=p)
            await update.message.reply_text(
                f"Prioridad de *{task['text']}* cambiada a P{p}.", parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "No entendí. Prueba con:\n"
                "• *Agrega [tarea]*\n• *Muestra mis tareas*\n"
                "• *Completa [tarea]*\n• *Elimina [tarea]*",
                parse_mode="Markdown"
            )
    except Exception as e:
        await update.message.reply_text(f"Ocurrió un error: {str(e)}")

# ── Bot corre en hilo secundario ──────────────────────────────────────────────

def run_bot():
    token = os.environ["TELEGRAM_TOKEN"]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    tg_app = ApplicationBuilder().token(token).build()
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot de Telegram corriendo en hilo secundario...")
    tg_app.run_polling()

# ── Entry point: Flask en hilo principal ─────────────────────────────────────

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    port = int(os.environ.get("PORT", 5000))
    print(f"API Flask corriendo en puerto {port}...")
    app.run(host="0.0.0.0", port=port, use_reloader=False)
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
