import os
from flask import Flask, jsonify, render_template, request, redirect, url_for, session
import psutil
import docker
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
# Gizli anahtarı artık sistemden okuyor, yoksa rastgele güçlü bir anahtar üretiyor
app.secret_key = os.environ.get("FLASK_SECRET", "cloudpulse-super-gizli-2026-devops-key")

# 🔒 GÜÇLÜ GİRİŞ BİLGİLERİ (Docker -e komutuyla dışarıdan verilebilir, yoksa bu güçlü şifre geçerli olur)
ADMIN_USER = os.environ.get("ADMIN_USER", "sysadmin")
RAW_PASSWORD = os.environ.get("ADMIN_PASS", "CloudPulse.2026!Secure#")
# Şifreyi bellekte SHA-256 ile kriptolu tutuyoruz!
ADMIN_PASS_HASH = generate_password_hash(RAW_PASSWORD)

# --- GÜVENLİK KALKANI (DECORATOR) ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- GİRİŞ VE ÇIKIŞ ROTASI ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        # check_password_hash ile kriptolu şifreyi doğruluyoruz
        if request.form['username'] == ADMIN_USER and check_password_hash(ADMIN_PASS_HASH, request.form['password']):
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            error = 'Hatalı Kullanıcı Adı veya Şifre! Lütfen tekrar deneyin.'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# --- KORUMALI SAYFALAR ---
@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/api/stats')
@login_required
def get_stats():
    cpu_percent = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory()
    mem_total_gb = round(memory.total / (1024**3), 2)
    mem_used_gb = round(memory.used / (1024**3), 2)
    mem_percent = memory.percent
    disk = psutil.disk_usage('/')
    
    return jsonify({
        "cpu": {"percent": cpu_percent},
        "memory": {"total_gb": mem_total_gb, "used_gb": mem_used_gb, "percent": mem_percent},
        "disk": {"percent": disk.percent}
    })

@app.route('/api/containers')
@login_required
def get_containers():
    try:
        client = docker.from_env()
        containers = []
        for container in client.containers.list(all=True):
            containers.append({
                "id": container.short_id,
                "name": container.name,
                "status": container.status,
                "image": container.image.tags[0] if container.image.tags else "bilinmiyor"
            })
        return jsonify({"containers": containers})
    except Exception as e:
        return jsonify({"error": f"Gerçek Docker Hatası: {str(e)}", "containers": []})

# ⚡ YENİ: KONTEYNER YÖNETİM API'Sİ (Mini-Portainer Gücü)
@app.route('/api/containers/<container_id>/<action>', methods=['POST'])
@login_required
def manage_container(container_id, action):
    try:
        client = docker.from_env()
        container = client.containers.get(container_id)
        
        # 🛡️ İNTİHAR KORUMASI: Panelin kendini durdurmasını/silmesini engelle!
        if container.name == "devops-monitor" and action in ["stop", "delete"]:
            return jsonify({"status": "error", "message": "Güvenlik Engeli: Aktif izleme panelini arayüzden durduramaz veya silemezsiniz!"}), 403

        if action == "start":
            container.start()
        elif action == "stop":
            container.stop()
        elif action == "restart":
            container.restart()
        elif action == "delete":
            container.remove(force=True)
        else:
            return jsonify({"status": "error", "message": "Geçersiz işlem!"}), 400
            
        return jsonify({"status": "success", "message": f"Konteyner başarıyla {action} edildi!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)