import os
import time
import uuid
import subprocess
import requests as req_lib
from flask import Flask, jsonify, render_template, request, redirect, url_for, session, send_file
import psutil
import sqlite3
import threading
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

# Kubernetes Kütüphanesi
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "cloudpulse-super-gizli-2026-devops-key")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8505924463:AAFJsvvg3v8CgI6kNZuZsxN5bkbn7rOG6xA")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "985168129")

ADMIN_USER = os.environ.get("ADMIN_USER", "sysadmin")
ADMIN_PASS_HASH = generate_password_hash(os.environ.get("ADMIN_PASS", "CloudPulse.2026!Secure#"))
VIEWER_USER = os.environ.get("VIEWER_USER", "guest")
VIEWER_PASS_HASH = generate_password_hash(os.environ.get("VIEWER_PASS", "Guest123"))

BACKUP_DIR = os.path.join(os.getcwd(), "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)
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

def get_k8s_client():
    try:
        config.load_incluster_config()
    except:
        try:
            config.load_kube_config(config_file="/etc/rancher/k3s/k3s.yaml")
            conf = client.Configuration.get_default_copy()
            # Kesin olarak host'un gerçek IP'sine (10.0.0.66) yönlendir
            conf.host = "https://10.0.0.66:6443"
            conf.verify_ssl = False
            client.Configuration.set_default(conf)
        except:
            config.load_kube_config()
    return client.CoreV1Api(), client.AppsV1Api(), client.CustomObjectsApi()

def background_db_worker():
    while True:
        try:
            cpu_percent = psutil.cpu_percent(interval=0.5)
            memory = psutil.virtual_memory()
            mem_percent = memory.percent
            
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("INSERT INTO stats_history (cpu, ram) VALUES (?, ?)", (cpu_percent, mem_percent))
            c.execute("DELETE FROM stats_history WHERE timestamp <= datetime('now', '-7 days')")
            conn.commit()
            conn.close()
        except Exception as e:
            pass
        time.sleep(3)

pod_stats_cache = {}

def background_container_stats_worker():
    while True:
        try:
            _, _, cust = get_k8s_client()
            metrics = cust.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "pods")
            for pod in metrics.get('items', []):
                pod_name = pod['metadata']['name']
                cpu_nano = 0
                mem_ki = 0
                for container in pod.get('containers', []):
                    cpu_usage = container['usage']['cpu']
                    mem_usage = container['usage']['memory']
                    if 'n' in cpu_usage: cpu_nano += int(cpu_usage.replace('n', ''))
                    if 'Ki' in mem_usage: mem_ki += int(mem_usage.replace('Ki', ''))
                
                cpu_percent = (cpu_nano / 1000000000.0) * 100
                mem_mb = mem_ki / 1024.0
                pod_stats_cache[pod_name] = {
                    "cpu": round(cpu_percent, 2),
                    "ram_percent": round((mem_mb / 24000.0) * 100, 2), # Varsayılan RAM oranlaması (Örn: 24GB)
                    "ram_mb": round(mem_mb, 2)
                }
        except Exception as e:
            pass
        time.sleep(5)

init_db()
threading.Thread(target=background_db_worker, daemon=True).start()
threading.Thread(target=background_container_stats_worker, daemon=True).start()

def send_telegram_alert(alert_key, message, force=False):
    pass # Aynı mantık (kısaltıldı)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'admin': return jsonify({"status": "error", "message": "Yetkisiz Erişim!"}), 403
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form['username'] == ADMIN_USER and check_password_hash(ADMIN_PASS_HASH, request.form['password']):
            session['logged_in'] = True
            session['role'] = 'admin'
            return redirect(url_for('index'))
    return render_template('login.html', error=error)

@app.route('/')
@login_required
def index():
    return render_template('index.html', role=session.get('role', 'viewer'))

@app.route('/api/stats')
@login_required
def get_stats():
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    return jsonify({
        "cpu": {"percent": cpu_percent},
        "memory": {
            "percent": memory.percent,
            "used_gb": round(memory.used / (1024**3), 1),
            "total_gb": round(memory.total / (1024**3), 1)
        },
        "disk": {"percent": disk.percent},
        "role": session.get('role', 'viewer')
    })

@app.route('/api/stats/history')
@login_required
def get_stats_history():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT timestamp, cpu, ram FROM stats_history ORDER BY id DESC LIMIT 60")
    rows = c.fetchall()
    conn.close()

    timestamps = []
    cpu_data = []
    ram_data = []
    for row in reversed(rows):
        timestamps.append(row[0].split(" ")[1])
        cpu_data.append(row[1])
        ram_data.append(row[2])

    return jsonify({
        "status": "success",
        "labels": timestamps,
        "cpus": cpu_data,
        "rams": ram_data
    })

@app.route('/api/containers')
@login_required
def get_containers():
    try:
        core_api, apps_api, _ = get_k8s_client()
        # API'nin sonsuza kadar takılmasını önlemek için 3 saniye zaman aşımı (timeout) ekledik
        pods = core_api.list_pod_for_all_namespaces(_request_timeout=3).items
        
        containers = []
        for pod in pods:
            # Sadece default ve kube-system ayırımı yapabiliriz ama genel listeliyoruz.
            status = pod.status.phase
            name = pod.metadata.name
            image = pod.spec.containers[0].image if pod.spec.containers else "bilinmiyor"
            c_stats = pod_stats_cache.get(name, {"cpu": 0.0, "ram_percent": 0.0, "ram_mb": 0.0})
            
            containers.append({
                "id": pod.metadata.name,
                "name": name,
                "status": status,
                "image": image,
                "stats": c_stats
            })
        return jsonify({"containers": containers})
    except Exception as e:
        return jsonify({"error": str(e), "containers": []})

def get_pod_namespace(core_api, pod_name):
    try:
        pods = core_api.list_pod_for_all_namespaces(field_selector=f"metadata.name={pod_name}").items
        if pods:
            return pods[0].metadata.namespace
    except:
        pass
    return "default"

@app.route('/api/containers/<pod_name>/<action>', methods=['POST'])
@login_required
@admin_required
def manage_container(pod_name, action):
    try:
        core_api, apps_api, _ = get_k8s_client()
        namespace = get_pod_namespace(core_api, pod_name)
        
        pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
        owner_name = None
        if pod.metadata.owner_references and pod.metadata.owner_references[0].kind == "ReplicaSet":
            owner_name = "-".join(pod.metadata.owner_references[0].name.split("-")[:-1])
        
        if action == "delete" or action == "restart":
            core_api.delete_namespaced_pod(name=pod_name, namespace=namespace)
            return jsonify({"status": "success", "message": f"Pod {action} tetiklendi. (K8s yenisini başlatacak)"})
            
        elif action == "stop":
            if not owner_name:
                return jsonify({"status": "error", "message": "Bu bağımsız bir Pod, durdurulamaz!"}), 400
            patch = {"spec": {"replicas": 0}}
            apps_api.patch_namespaced_deployment(name=owner_name, namespace=namespace, body=patch)
            return jsonify({"status": "success", "message": f"{owner_name} durduruldu (Scale 0)!"})
            
        elif action == "start":
            if not owner_name:
                return jsonify({"status": "error", "message": "Bu bağımsız bir Pod, başlatılamaz!"}), 400
            patch = {"spec": {"replicas": 1}}
            apps_api.patch_namespaced_deployment(name=owner_name, namespace=namespace, body=patch)
            return jsonify({"status": "success", "message": f"{owner_name} başlatıldı (Scale 1)!"})
            
        else:
            return jsonify({"status": "error", "message": "Geçersiz işlem!"}), 400
            
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/containers/<pod_name>/exec', methods=['POST'])
@login_required
@admin_required
def execute_command(pod_name):
    try:
        data = request.get_json()
        cmd = data.get('command', '')
        if not cmd:
            return jsonify({"status": "error", "output": "Geçersiz komut."})

        core_api, _, _ = get_k8s_client()
        namespace = get_pod_namespace(core_api, pod_name)
        exec_command = ['/bin/sh', '-c', cmd]
        resp = stream(core_api.connect_get_namespaced_pod_exec,
                      pod_name,
                      namespace,
                      command=exec_command,
                      stderr=True, stdin=False,
                      stdout=True, tty=False)
        return jsonify({"status": "success", "output": resp})
    except Exception as e:
        return jsonify({"status": "error", "output": f"Hata: {str(e)}"})

@app.route('/api/containers/<pod_name>/limit', methods=['POST'])
@login_required
@admin_required
def set_limit(pod_name):
    try:
        data = request.get_json()
        limit_mb = data.get('mem_limit_mb')
        core_api, apps_api, _ = get_k8s_client()
        namespace = get_pod_namespace(core_api, pod_name)
        
        pod = core_api.read_namespaced_pod(name=pod_name, namespace=namespace)
        container_name = pod.spec.containers[0].name
        owner_name = None
        if pod.metadata.owner_references and pod.metadata.owner_references[0].kind == "ReplicaSet":
            owner_name = "-".join(pod.metadata.owner_references[0].name.split("-")[:-1])
            
        if not owner_name:
            return jsonify({"status": "error", "message": "Deployment bulunamadı!"}), 400
            
        patch = {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{
                            "name": container_name,
                            "resources": {
                                "limits": {"memory": f"{limit_mb}Mi"}
                            }
                        }]
                    }
                }
            }
        }
        apps_api.patch_namespaced_deployment(name=owner_name, namespace=namespace, body=patch)
        return jsonify({"status": "success", "message": f"RAM Limiti {limit_mb}MB yapıldı!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/containers/deploy', methods=['POST'])
@login_required
@admin_required
def deploy_container():
    try:
        data = request.get_json()
        image = data.get('image', '').strip()
        name = data.get('name', 'new-app').strip()

        core_api, apps_api, _ = get_k8s_client()
        
        deployment = client.V1Deployment(
            metadata=client.V1ObjectMeta(name=name),
            spec=client.V1DeploymentSpec(
                replicas=1,
                selector=client.V1LabelSelector(match_labels={"app": name}),
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels={"app": name}),
                    spec=client.V1PodSpec(containers=[client.V1Container(name=name, image=image)])
                )
            )
        )
        apps_api.create_namespaced_deployment(namespace="default", body=deployment)
        
        # Eğer port yönlendirmesi istenmişse K8s Service (LoadBalancer) oluştur
        port_mapping = data.get('port', '').strip()
        if port_mapping and ':' in port_mapping:
            external_port, internal_port = port_mapping.split(':', 1)
            service = client.V1Service(
                metadata=client.V1ObjectMeta(name=name),
                spec=client.V1ServiceSpec(
                    type="LoadBalancer",
                    selector={"app": name},
                    ports=[client.V1ServicePort(
                        port=int(external_port),
                        target_port=int(internal_port)
                    )]
                )
            )
            core_api.create_namespaced_service(namespace="default", body=service)
            return jsonify({"status": "success", "message": f"{image} başarıyla başlatıldı ve Dışarıya {external_port} portundan açıldı!"})

        return jsonify({"status": "success", "message": f"{image} başarıyla Deployment olarak başlatıldı!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/containers/<pod_name>/logs', methods=['GET'])
@login_required
def get_container_logs(pod_name):
    try:
        core_api, apps_api, _ = get_k8s_client()
        namespace = get_pod_namespace(core_api, pod_name)
        logs = core_api.read_namespaced_pod_log(name=pod_name, namespace=namespace, tail_lines=500)
        return jsonify({"status": "success", "logs": logs})
    except Exception as e:
        return jsonify({"status": "error", "logs": f"Sistem Hatası (veya K8s hazır değil): {str(e)}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
