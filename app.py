from flask import Flask, jsonify, render_template, request, redirect, url_for, session
import psutil
import docker
from functools import wraps

app = Flask(__name__)
# Tarayıcı çerezlerini şifrelemek için gizli bir anahtar zorunludur!
app.secret_key = "cloudpulse-super-gizli-devops-anahtari"

# 🔒 GİRİŞ BİLGİLERİMİZ
ADMIN_USER = "admin"
ADMIN_PASS = "devops123"

try:
    docker_client = docker.from_env()
except Exception as e:
    docker_client = None

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
        if request.form['username'] == ADMIN_USER and request.form['password'] == ADMIN_PASS:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            error = 'Hatalı Kullanıcı Adı veya Şifre! Lütfen tekrar deneyin.'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# --- KORUMALI SAYFALAR (@login_required eklendi) ---
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
    if not docker_client:
        return jsonify({"error": "Docker motoruna ulaşılamadı", "containers": []})
    try:
        containers = []
        for container in docker_client.containers.list(all=True):
            containers.append({
                "id": container.short_id,
                "name": container.name,
                "status": container.status,
                "image": container.image.tags[0] if container.image.tags else "bilinmiyor"
            })
        return jsonify({"containers": containers})
    except Exception as e:
        return jsonify({"error": str(e), "containers": []})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)