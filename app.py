import os
import time
import requests as req_lib
from flask import Flask, jsonify, render_template, request, redirect, url_for, session
import psutil
import docker
import sqlite3
import threading
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "cloudpulse-super-gizli-2026-devops-key")

# 🚨 TELEGRAM AYARLARIMIZ (Burayı kendi bilgilerinle doldur!)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8505924463:AAFJsvvg3v8CgI6kNZuZsxN5bkbn7rOG6xA")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "985168129")

# 🔒 GÜÇLÜ GİRİŞ BİLGİLERİMİZ (Admin ve Viewer)
ADMIN_USER = os.environ.get("ADMIN_USER", "sysadmin")
ADMIN_PASS_HASH = generate_password_hash(os.environ.get("ADMIN_PASS", "CloudPulse.2026!Secure#"))

VIEWER_USER = os.environ.get("VIEWER_USER", "guest")
VIEWER_PASS_HASH = generate_password_hash(os.environ.get("VIEWER_PASS", "Guest123"))

# --- VERİTABANI VE GEÇMİŞ İZLEME ---
DB_NAME = 'cloudpulse.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS stats_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    cpu REAL,
                    ram REAL
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS alerts_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    message TEXT
                )''')
    conn.commit()
    conn.close()

def background_db_worker():
    while True:
        try:
            cpu_percent = psutil.cpu_percent(interval=0.5)
            memory = psutil.virtual_memory()
            mem_percent = memory.percent
            
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("INSERT INTO stats_history (cpu, ram) VALUES (?, ?)", (cpu_percent, mem_percent))
            # 7 Günden eski verileri sil
            c.execute("DELETE FROM stats_history WHERE timestamp <= datetime('now', '-7 days')")
            # En fazla 1000 alarm kalsın
            c.execute("DELETE FROM alerts_history WHERE id NOT IN (SELECT id FROM alerts_history ORDER BY id DESC LIMIT 1000)")
            conn.commit()
            conn.close()
        except Exception as e:
            print("DB Worker Hatası:", e)
        time.sleep(300) # 5 Dakika bekle

# Veritabanını kur ve arkaplan işçisini başlat
init_db()
threading.Thread(target=background_db_worker, daemon=True).start()

# --- SPAM VE BİLİNÇLİ DURDURMA KORUMASI ---
last_alert_times = {}
ALERT_COOLDOWN = 300
muted_containers = set()

def send_telegram_alert(alert_key, message, force=False):
    global last_alert_times
    now = time.time()
    
    if not force and alert_key in last_alert_times:
        if now - last_alert_times[alert_key] < ALERT_COOLDOWN:
            return False

    # Veritabanına kaydet
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("INSERT INTO alerts_history (message) VALUES (?)", (message,))
        conn.commit()
        conn.close()
    except Exception as e:
        print("Alert DB kayıt hatası:", e)

    if TELEGRAM_TOKEN == "BURAYA_BOTFATHER_TOKEN_GELECEK":
        print("UYARI: Telegram Token ayarlanmamış!")
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        res = req_lib.post(url, json=payload, timeout=5)
        if res.status_code == 200:
            last_alert_times[alert_key] = now
            return True
    except Exception as e:
        print(f"Telegram Gönderim Hatası: {e}")
    return False

# --- GÜVENLİK KALKANI ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'admin':
            return jsonify({"status": "error", "message": "Yetkisiz Erişim! Sadece adminler bu işlemi yapabilir."}), 403
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == ADMIN_USER and check_password_hash(ADMIN_PASS_HASH, password):
            session['logged_in'] = True
            session['role'] = 'admin'
            return redirect(url_for('index'))
        elif username == VIEWER_USER and check_password_hash(VIEWER_PASS_HASH, password):
            session['logged_in'] = True
            session['role'] = 'viewer'
            return redirect(url_for('index'))
        else:
            error = 'Hatalı Kullanıcı Adı veya Şifre!'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('role', None)
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html', role=session.get('role', 'viewer'))

@app.route('/api/stats')
@login_required
def get_stats():
    cpu_percent = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory()
    mem_total_gb = round(memory.total / (1024**3), 2)
    mem_used_gb = round(memory.used / (1024**3), 2)
    mem_percent = memory.percent
    disk = psutil.disk_usage('/')
    
    if cpu_percent >= 80.0:
        send_telegram_alert("high_cpu", f"🚨 *KRİTİK UYARI: Yüksek CPU!*\n\n🖥️ Sunucu işlemci yükü *%{cpu_percent}* seviyesine ulaştı! Lütfen sistemi kontrol edin.")
    if mem_percent >= 80.0:
        send_telegram_alert("high_mem", f"🚨 *KRİTİK UYARI: Yüksek RAM!*\n\n💾 Sunucuda bellek kullanımı *%{mem_percent}* (*{mem_used_gb} GB*) seviyesine çıktı!")

    return jsonify({
        "cpu": {"percent": cpu_percent},
        "memory": {"total_gb": mem_total_gb, "used_gb": mem_used_gb, "percent": mem_percent},
        "disk": {"percent": disk.percent},
        "role": session.get('role', 'viewer') # Arayüze rol bilgisini gönder
    })

@app.route('/api/stats/history')
@login_required
def get_stats_history():
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT datetime(timestamp, 'localtime'), cpu, ram FROM stats_history ORDER BY id DESC LIMIT 288")
        rows = c.fetchall()
        conn.close()
        rows.reverse()
        labels = [r[0] for r in rows]
        cpus = [r[1] for r in rows]
        rams = [r[2] for r in rows]
        return jsonify({"status": "success", "labels": labels, "cpus": cpus, "rams": rams})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/alerts')
@login_required
def get_alerts():
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT datetime(timestamp, 'localtime'), message FROM alerts_history ORDER BY id DESC LIMIT 50")
        rows = c.fetchall()
        conn.close()
        alerts = [{"time": r[0], "message": r[1]} for r in rows]
        return jsonify({"status": "success", "alerts": alerts})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/containers')
@login_required
def get_containers():
    try:
        client = docker.from_env()
        containers = []
        for container in client.containers.list(all=True):
            status = container.status
            name = container.name
            
            if "exited" in status.lower() or "dead" in status.lower():
                exit_code = container.attrs.get('State', {}).get('ExitCode', 0)
                error_msg = container.attrs.get('State', {}).get('Error', '')
                if name not in muted_containers and (exit_code != 0 or error_msg):
                    send_telegram_alert(
                        f"crash_{name}", 
                        f"🚨 *KRİTİK DOCKER ÇÖKME ALARMI!*\n\n🐳 `{name}` servisi BEKLENMEDİK ŞEKİLDE ÇÖKTÜ!\n*Hata Kodu (Exit Code):* `{exit_code}`\n*Detay:* `{error_msg or 'Bilinmeyen Sistem Hatası'}`"
                    )
                
            containers.append({
                "id": container.short_id,
                "name": name,
                "status": status,
                "image": container.image.tags[0] if container.image.tags else "bilinmiyor"
            })
        return jsonify({"containers": containers})
    except Exception as e:
        return jsonify({"error": f"Gerçek Docker Hatası: {str(e)}", "containers": []})

@app.route('/api/containers/<container_id>/<action>', methods=['POST'])
@login_required
@admin_required
def manage_container(container_id, action):
    try:
        client = docker.from_env()
        container = client.containers.get(container_id)
        
        if container.name == "devops-monitor" and action in ["stop", "delete"]:
            return jsonify({"status": "error", "message": "Güvenlik Engeli: Aktif izleme panelini arayüzden durduramaz veya silemezsiniz!"}), 403

        if action == "start":
            muted_containers.discard(container.name)
            container.start()
            send_telegram_alert(f"action_{container.name}", f"▶️ *SERVİS BAŞLATILDI*\n\n`{container.name}` servisi arayüzden aktifleştirildi.", force=True)
        elif action == "stop":
            muted_containers.add(container.name)
            container.stop()
            send_telegram_alert(f"action_{container.name}", f"⏸️ *SERVİS DURDURULDU*\n\n`{container.name}` servisi arayüzden bilinçli durduruldu.", force=True)
        elif action == "restart":
            muted_containers.discard(container.name)
            container.restart()
            send_telegram_alert(f"action_{container.name}", f"🔄 *SERVİS YENİDEN BAŞLATILDI*\n\n`{container.name}` servisi arayüzden yeniden başlatıldı.", force=True)
        elif action == "delete":
            container.remove(force=True)
            send_telegram_alert(f"action_{container.name}", f"🗑️ *SERVİS SİLİNDİ*\n\n`{container.name}` servisi kaldırıldı!", force=True)
        else:
            return jsonify({"status": "error", "message": "Geçersiz işlem!"}), 400
            
        return jsonify({"status": "success", "message": f"Konteyner başarıyla {action} edildi!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/containers/deploy', methods=['POST'])
@login_required
@admin_required
def deploy_container():
    try:
        data = request.get_json()
        image = data.get('image', '').strip()
        name = data.get('name', '').strip()
        port_mapping = data.get('port', '').strip()

        if not image:
            return jsonify({"status": "error", "message": "İmaj adı boş olamaz!"}), 400

        client = docker.from_env()
        ports = {}
        if port_mapping:
            parts = port_mapping.split(':')
            if len(parts) == 2:
                ports[f"{parts[1]}/tcp"] = int(parts[0])

        kwargs = {'detach': True}
        if name: kwargs['name'] = name
        if ports: kwargs['ports'] = ports

        client.containers.run(image, **kwargs)
        send_telegram_alert("deploy", f"🚀 *YENİ SERVİS KURULDU*\n\n`{image}` imajı başarıyla başlatıldı.", force=True)
        return jsonify({"status": "success", "message": f"{image} başarıyla kuruldu ve başlatıldı!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/containers/<container_id>/limit', methods=['POST'])
@login_required
@admin_required
def limit_container(container_id):
    try:
        data = request.get_json()
        mem_mb = data.get('mem_limit_mb')
        if not mem_mb:
            return jsonify({"status": "error", "message": "Geçersiz limit değeri."}), 400
            
        client = docker.from_env()
        container = client.containers.get(container_id)
        container.update(mem_limit=f"{mem_mb}m")
        send_telegram_alert("limit", f"🔒 *LİMİT GÜNCELLENDİ*\n\n`{container.name}` servisine {mem_mb} MB RAM limiti koyuldu.", force=True)
        return jsonify({"status": "success", "message": f"{container.name} için limit {mem_mb} MB olarak güncellendi."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/images')
@login_required
def list_images():
    try:
        client = docker.from_env()
        images = []
        for img in client.images.list():
            if img.tags:
                tag = img.tags[0]
                size_mb = round(img.attrs['Size'] / (1024 * 1024), 2)
                images.append({"id": img.short_id, "tag": tag, "size_mb": size_mb})
        return jsonify({"status": "success", "images": images})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/system/prune', methods=['POST'])
@login_required
@admin_required
def prune_system():
    try:
        client = docker.from_env()
        res = client.images.prune(filters={'dangling': False})
        reclaimed = res.get('SpaceReclaimed', 0)
        reclaimed_mb = round(reclaimed / (1024*1024), 2)
        send_telegram_alert("prune", f"🧹 *DİSK TEMİZLİĞİ YAPILDI*\n\nSunucuda kullanılmayan imajlar silindi. Boşaltılan alan: {reclaimed_mb} MB.", force=True)
        return jsonify({"status": "success", "message": f"Kullanılmayan imajlar silindi. Boşaltılan alan: {reclaimed_mb} MB."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/test-telegram', methods=['POST'])
@login_required
@admin_required
def test_telegram():
    success = send_telegram_alert("test_alert", "⚡ *CLOUDPULSE ALARM TESTİ*\n\nHarika! Telegram bot entegrasyonu başarıyla çalışıyor! 🚀", force=True)
    if success:
        return jsonify({"status": "success", "message": "Telegram bildirim testi başarıyla gönderildi! 📱"})
    else:
        return jsonify({"status": "error", "message": "Telegram bildirimi gönderilemedi!"}), 500

@app.route('/api/containers/<container_id>/logs', methods=['GET'])
@login_required
def get_container_logs(container_id):
    try:
        client = docker.from_env()
        container = client.containers.get(container_id)
        logs = container.logs(tail=200, stdout=True, stderr=True)
        logs_str = logs.decode('utf-8', errors='replace')
        return jsonify({"status": "success", "logs": logs_str})
    except Exception as e:
        return jsonify({"status": "error", "logs": f"Sistem Hatası: {str(e)}"}), 500

@app.route('/api/containers/<container_id>/exec', methods=['POST'])
@login_required
@admin_required
def exec_in_container(container_id):
    try:
        data = request.get_json()
        command = data.get('command', '')
        if not command: return jsonify({"status": "error", "output": "Komut girin!"}), 400
        client = docker.from_env()
        container = client.containers.get(container_id)
        exit_code, output = container.exec_run(command)
        output_str = output.decode('utf-8', errors='replace') if output else "(Komut çalıştı, çıktı yok)"
        return jsonify({"status": "success" if exit_code == 0 else "error", "exit_code": exit_code, "output": output_str})
    except Exception as e:
        return jsonify({"status": "error", "output": f"Sistem Hatası: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)