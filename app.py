import os
import time
import requests as req_lib  # Telegram API'sine istek atmak için
from flask import Flask, jsonify, render_template, request, redirect, url_for, session
import psutil
import docker
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "cloudpulse-super-gizli-2026-devops-key")

# 🚨 TELEGRAM AYARLARIMIZ 
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8505924463:AAFJsvvg3v8CgI6kNZuZsxN5bkbn7rOG6xA")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "985168129")

# 🔒 GİRİŞ BİLGİLERİMİZ
ADMIN_USER = os.environ.get("ADMIN_USER", "sysadmin")
RAW_PASSWORD = os.environ.get("ADMIN_PASS", "CloudPulse.2026!Secure#")
ADMIN_PASS_HASH = generate_password_hash(RAW_PASSWORD)

# --- SPAM KORUMALI TELEGRAM ALARM MOTORU ---
last_alert_times = {}
ALERT_COOLDOWN = 300  # Aynı uyarıyı 5 dakikada (300 sn) bir gönder (Telefonu spamlama!)

def send_telegram_alert(alert_key, message, force=False):
    global last_alert_times
    now = time.time()
    
    # Eğer zorunlu (force) değilse ve cooldown süresi dolmadıysa bildirimi engelle
    if not force and alert_key in last_alert_times:
        if now - last_alert_times[alert_key] < ALERT_COOLDOWN:
            return False

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

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form['username'] == ADMIN_USER and check_password_hash(ADMIN_PASS_HASH, request.form['password']):
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            error = 'Hatalı Kullanıcı Adı veya Şifre!'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

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
    
    # 🚨 PROAKTİF KONTROL: RAM veya CPU %80'i geçerse otomatik Telegram at!
    if cpu_percent >= 80.0:
        send_telegram_alert("high_cpu", f"🚨 *KRİTİK UYARI: Yüksek CPU!*\n\n🖥️ Sunucu işlemci yükü *%{cpu_percent}* seviyesine ulaştı! Lütfen sistemi kontrol edin.")
    if mem_percent >= 80.0:
        send_telegram_alert("high_mem", f"🚨 *KRİTİK UYARI: Yüksek RAM!*\n\n💾 12 GB sunucuda bellek kullanımı *%{mem_percent}* (*{mem_used_gb} GB*) seviyesine çıktı!")

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
            status = container.status
            # 🚨 PROAKTİF KONTROL: Eğer bir konteyner "exited" (çökmüş/durmuş) ise bildir!
            if "exited" in status.lower() or "dead" in status.lower():
                send_telegram_alert(f"container_{container.name}", f"⚠️ *DOCKER SERVİS ALARMI!*\n\n🐳 `{container.name}` isimli servis durdu veya çöktü!\n*Durum:* `{status}`")
                
            containers.append({
                "id": container.short_id,
                "name": container.name,
                "status": status,
                "image": container.image.tags[0] if container.image.tags else "bilinmiyor"
            })
        return jsonify({"containers": containers})
    except Exception as e:
        return jsonify({"error": f"Gerçek Docker Hatası: {str(e)}", "containers": []})

@app.route('/api/containers/<container_id>/<action>', methods=['POST'])
@login_required
def manage_container(container_id, action):
    try:
        client = docker.from_env()
        container = client.containers.get(container_id)
        
        if container.name == "devops-monitor" and action in ["stop", "delete"]:
            return jsonify({"status": "error", "message": "Güvenlik Engeli: Aktif izleme panelini arayüzden durduramaz veya silemezsiniz!"}), 403

        if action == "start":
            container.start()
            send_telegram_alert(f"action_{container.name}", f"▶️ *SERVİS BAŞLATILDI*\n\n`{container.name}` servisi arayüzden basılarak aktifleştirildi.", force=True)
        elif action == "stop":
            container.stop()
            send_telegram_alert(f"action_{container.name}", f"⏸️ *SERVİS DURDURULDU*\n\n`{container.name}` servisi arayüzden basılarak durduruldu.", force=True)
        elif action == "restart":
            container.restart()
            send_telegram_alert(f"action_{container.name}", f"🔄 *SERVİS YENİDEN BAŞLATILDI*\n\n`{container.name}` servisi arayüzden yeniden başlatıldı.", force=True)
        elif action == "delete":
            container.remove(force=True)
            send_telegram_alert(f"action_{container.name}", f"🗑️ *SERVİS SİLİNDİ*\n\n`{container.name}` servisi kalıcı olarak kaldırıldı!", force=True)
        else:
            return jsonify({"status": "error", "message": "Geçersiz işlem!"}), 400
            
        return jsonify({"status": "success", "message": f"Konteyner başarıyla {action} edildi!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ⚡ YENİ: ARAYÜZDEN ANINDA TEST ETME BUTONU İÇİN API
@app.route('/api/test-telegram', methods=['POST'])
@login_required
def test_telegram():
    success = send_telegram_alert("test_alert", "⚡ *CLOUDPULSE ALARM TESTİ*\n\nHarika! Telegram bot entegrasyonu başarıyla çalışıyor! 🚀 Sunucundan gelen bildirimler artık burada görünecek.", force=True)
    if success:
        return jsonify({"status": "success", "message": "Telegram bildirim testi başarıyla cep telefonuna gönderildi! 📱"})
    else:
        return jsonify({"status": "error", "message": "Telegram bildirimi gönderilemedi! Token veya Chat ID'nizi kontrol edin."}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)