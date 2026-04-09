import os
import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])

@app.route("/tasks", methods=["GET"])
def get_tasks():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM tasks ORDER BY status='done', priority DESC, created_at ASC")
    tasks = list(cur.fetchall())
    for t in tasks:
        if t.get("created_at"):
            t["created_at"] = t["created_at"].isoformat()
    cur.close(); conn.close()
    return jsonify(tasks)

@app.route("/tasks", methods=["POST"])
def create_task():
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
def update_task(task_id):
    data = request.json
    fields = []
    values = []
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
    cur.execute(
        f"UPDATE tasks SET {', '.join(fields)} WHERE id = %s RETURNING *",
        values
    )
    row = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    task = dict(row)
    if task.get("created_at"):
        task["created_at"] = task["created_at"].isoformat()
    return jsonify(task)

@app.route("/tasks/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
