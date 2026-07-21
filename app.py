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

                if cpu_percent > 90.0:
                    send_telegram_alert(f"cpu_pod_{pod_name}", f"DİKKAT! {pod_name} podu %90'ın üzerinde CPU kullanıyor! Mevcut: %{round(cpu_percent, 2)}")
        except Exception as e:
            pass
        time.sleep(5)

init_db()
threading.Thread(target=background_db_worker, daemon=True).start()
threading.Thread(target=background_container_stats_worker, daemon=True).start()

last_alert_times = {}
def send_telegram_alert(alert_key, message, force=False):
    global last_alert_times
    now = time.time()
    if not force:
        if alert_key in last_alert_times and (now - last_alert_times[alert_key]) < 300: # 5 dk içinde tekrar atma
            return
    last_alert_times[alert_key] = now

    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("INSERT INTO alerts_history (message) VALUES (?)", (message,))
        conn.commit()
        conn.close()
    except:
        pass

    try:
        req_lib.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": f"KubePulse Alarm:\n{message}"
        }, timeout=2)
    except:
        pass

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
        elif request.form['username'] == VIEWER_USER and check_password_hash(VIEWER_PASS_HASH, request.form['password']):
            session['logged_in'] = True
            session['role'] = 'viewer'
            return redirect(url_for('index'))
        else:
            error = 'Geçersiz kullanıcı adı veya şifre.'
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

def parse_k8s_cpu(cpu_str):
    try:
        if cpu_str.endswith('m'):
            return int(cpu_str[:-1]) / 1000.0
        elif cpu_str.endswith('n'):
            return int(cpu_str[:-1]) / 1000000000.0
        return float(cpu_str)
    except: return 0.0

def parse_k8s_memory(mem_str):
    try:
        if mem_str.endswith('Ki'):
            return int(mem_str[:-2]) * 1024
        elif mem_str.endswith('Mi'):
            return int(mem_str[:-2]) * 1024 * 1024
        elif mem_str.endswith('Gi'):
            return int(mem_str[:-2]) * 1024 * 1024 * 1024
        return int(mem_str)
    except: return 0

@app.route('/api/nodes')
@login_required
def get_nodes():
    try:
        core_api, _, custom_api = get_k8s_client()
        nodes = core_api.list_node().items
        try:
            metrics = custom_api.list_cluster_custom_object("metrics.k8s.io", "v1beta1", "nodes").get('items', [])
            metrics_dict = {m['metadata']['name']: m['usage'] for m in metrics}
        except:
            metrics_dict = {}
        import json
        node_list = []
        for n in nodes:
            name = n.metadata.name
            capacity = n.status.capacity
            allocatable = n.status.allocatable
            
            cap_cpu = parse_k8s_cpu(capacity.get('cpu', '1'))
            cap_mem = parse_k8s_memory(capacity.get('memory', '0Ki'))
            
            usage = metrics_dict.get(name, {})
            used_cpu = parse_k8s_cpu(usage.get('cpu', '0n'))
            used_mem = parse_k8s_memory(usage.get('memory', '0Ki'))
            
            cpu_percent = round((used_cpu / cap_cpu) * 100, 1) if cap_cpu > 0 else 0
            mem_percent = round((used_mem / cap_mem) * 100, 1) if cap_mem > 0 else 0
            import ast
            try:
                raw_stats = core_api.connect_get_node_proxy_with_path(name, path="stats/summary")
                if isinstance(raw_stats, str):
                    try:
                        node_stats = json.loads(raw_stats)
                    except json.JSONDecodeError:
                        node_stats = ast.literal_eval(raw_stats)
                else:
                    node_stats = raw_stats
                fs_used = node_stats['node']['fs']['usedBytes']
                fs_capacity = node_stats['node']['fs']['capacityBytes']
                disk_percent = round((fs_used / fs_capacity) * 100, 1) if fs_capacity > 0 else 0
            except Exception as e:
                if name == "devops-ubuntu-sunucu":
                    disk_percent = psutil.disk_usage('/').percent
                else:
                    disk_percent = 0.0
            
            node_list.append({
                "name": name,
                "cpu": {
                    "used": used_cpu,
                    "total": cap_cpu,
                    "percent": cpu_percent
                },
                "memory": {
                    "used_gb": round(used_mem / (1024**3), 2),
                    "total_gb": round(cap_mem / (1024**3), 2),
                    "percent": mem_percent
                },
                "disk": {
                    "percent": disk_percent
                }
            })
            
        return jsonify({"nodes": node_list})
    except Exception as e:
        return jsonify({"error": str(e), "nodes": []})

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
            status = pod.status.phase
            name = pod.metadata.name
            namespace = pod.metadata.namespace
            image = pod.spec.containers[0].image if pod.spec.containers else "bilinmiyor"
            c_stats = pod_stats_cache.get(name, {"cpu": 0.0, "ram_percent": 0.0, "ram_mb": 0.0})
            
            is_system = False
            if namespace in ["kube-system", "kube-public", "kube-node-lease"]:
                is_system = True
            elif name.startswith("svclb-") or name.startswith("traefik") or name.startswith("coredns") or name.startswith("local-path") or name.startswith("metrics-server"):
                is_system = True
                
            containers.append({
                "id": pod.metadata.name,
                "name": name,
                "status": status,
                "image": image,
                "node_name": pod.spec.node_name or "Bilinmiyor",
                "stats": c_stats,
                "pod_type": "system" if is_system else "user"
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
        owner_kind = None
        if pod.metadata.owner_references:
            owner_kind = pod.metadata.owner_references[0].kind
            if owner_kind == "ReplicaSet":
                owner_name = "-".join(pod.metadata.owner_references[0].name.split("-")[:-1])
            else:
                owner_name = pod.metadata.owner_references[0].name
        
        if action == "restart":
            core_api.delete_namespaced_pod(name=pod_name, namespace=namespace)
            return jsonify({"status": "success", "message": "Pod yeniden başlatılıyor..."})
            
        elif action == "delete":
            if owner_name and owner_kind == "ReplicaSet":
                apps_api.delete_namespaced_deployment(name=owner_name, namespace=namespace)
                try:
                    core_api.delete_namespaced_service(name=owner_name, namespace=namespace)
                except:
                    pass
            elif owner_name and owner_kind == "DaemonSet":
                apps_api.delete_namespaced_daemon_set(name=owner_name, namespace=namespace)
            elif owner_name and owner_kind == "StatefulSet":
                apps_api.delete_namespaced_stateful_set(name=owner_name, namespace=namespace)
            else:
                core_api.delete_namespaced_pod(name=pod_name, namespace=namespace)
            return jsonify({"status": "success", "message": f"{owner_name or pod_name} kökünden silindi!"})
            
        elif action == "scale":
            if not owner_name:
                return jsonify({"status": "error", "message": "Bu bağımsız bir Pod, ölçeklenemez (Scale edilemez)!"}), 400
            
            data = request.get_json() or {}
            try:
                replicas = int(data.get("replicas", 1))
            except:
                replicas = 1
                
            patch = {"spec": {"replicas": replicas}}
            apps_api.patch_namespaced_deployment(name=owner_name, namespace=namespace, body=patch)
            return jsonify({"status": "success", "message": f"{owner_name} başarıyla {replicas} kopyaya ayarlandı!"})
            
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

@app.route('/api/kubernetes/yaml', methods=['POST'])
@login_required
@admin_required
def deploy_yaml():
    try:
        data = request.get_json()
        yaml_content = data.get('yaml', '').strip()
        if not yaml_content:
            return jsonify({"status": "error", "message": "YAML içeriği boş olamaz."})

        from kubernetes import utils, client
        import tempfile
        import os
        
        get_k8s_client() # config.load... işlemlerini tetikler
        k8s_client = client.ApiClient()
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".yaml", mode='w') as tf:
            tf.write(yaml_content)
            temp_path = tf.name

        try:
            utils.create_from_yaml(k8s_client, temp_path)
            os.remove(temp_path)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return jsonify({"status": "error", "message": f"YAML uygulama hatası: {str(e)}"})

        return jsonify({"status": "success", "message": "YAML başarıyla Kubernetes'e uygulandı!"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Hata: {str(e)}"}), 500

@app.route('/api/system/exec', methods=['POST'])
@login_required
@admin_required
def execute_system_command():
    try:
        data = request.get_json()
        cmd = data.get('command', '')
        if not cmd:
            return jsonify({"status": "error", "output": "Geçersiz komut."})

        output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, text=True, timeout=15)
        return jsonify({"status": "success", "output": output})
    except subprocess.CalledProcessError as e:
        return jsonify({"status": "error", "output": str(e.output)})
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
                        "enable_service_links": False,
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
        name = data.get('name', '').strip()
        
        if not name:
            import uuid
            # İsim boş bırakıldıysa imaj adından (Örn: nginx:latest -> nginx) ve rastgele id'den isim üret
            safe_image = image.split(':')[0].replace('/', '-')
            name = f"{safe_image}-{uuid.uuid4().hex[:5]}"

        # Kubernetes isimlendirme standartlarına uymak için formatı temizle
        import re
        name = re.sub(r'[^a-z0-9\-]', '-', name.lower()).strip('-')

        core_api, apps_api, _ = get_k8s_client()
        
        # Port parse
        port_mapping = data.get('port', '').strip()
        external_port, internal_port = None, None
        if port_mapping:
            if ':' in port_mapping:
                external_port, internal_port = port_mapping.split(':', 1)
            else:
                external_port, internal_port = port_mapping, port_mapping
                
        # Container portları hazırla
        container_ports = []
        if internal_port:
            container_ports.append(client.V1ContainerPort(container_port=int(internal_port)))

        deployment = client.V1Deployment(
            metadata=client.V1ObjectMeta(name=name),
            spec=client.V1DeploymentSpec(
                replicas=1,
                selector=client.V1LabelSelector(match_labels={"app": name}),
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels={"app": name}),
                    spec=client.V1PodSpec(containers=[client.V1Container(name=name, image=image, ports=container_ports if container_ports else None)])
                )
            )
        )
        apps_api.create_namespaced_deployment(namespace="default", body=deployment)
        
        # Eğer port yönlendirmesi istenmişse K8s Service oluştur
        if external_port and internal_port:
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

@app.route('/api/alerts', methods=['GET'])
@login_required
def get_alerts():
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT timestamp, message FROM alerts_history ORDER BY id DESC LIMIT 50")
        rows = c.fetchall()
        conn.close()
        alerts = [{"time": r[0], "message": r[1]} for r in rows]
        return jsonify({"status": "success", "alerts": alerts})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/test-telegram', methods=['POST'])
@login_required
@admin_required
def test_telegram_api():
    try:
        send_telegram_alert("test_alert", "Bu bir KubePulse test bildirimidir! 🚀", force=True)
        return jsonify({"status": "success", "message": "Test mesajı Telegram'a gönderildi!"})
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
