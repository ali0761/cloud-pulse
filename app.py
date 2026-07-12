from flask import Flask, jsonify, render_template
import psutil
import docker

app = Flask(__name__)

# Docker istemcisini güvenli başlat (Windows'ta Docker yoksa veya kapalıysa uygulama çökmesin diye)
try:
    docker_client = docker.from_env()
except Exception as e:
    docker_client = None
    print("Uyarı: Docker istemcisine bağlanılamadı. (Yerel Windows testinde normaldir)")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/stats')
def get_stats():
    """Anlık CPU, RAM ve Disk kullanım verilerini JSON olarak döndürür."""
    # CPU Kullanımı (%)
    cpu_percent = psutil.cpu_percent(interval=0.5)
    
    # RAM Kullanımı (GB ve % olarak)
    memory = psutil.virtual_memory()
    mem_total_gb = round(memory.total / (1024**3), 2)
    mem_used_gb = round(memory.used / (1024**3), 2)
    mem_percent = memory.percent
    
    # Disk Kullanımı (%)
    disk = psutil.disk_usage('/')
    disk_percent = disk.percent
    
    return jsonify({
        "cpu": {
            "percent": cpu_percent
        },
        "memory": {
            "total_gb": mem_total_gb,
            "used_gb": mem_used_gb,
            "percent": mem_percent
        },
        "disk": {
            "percent": disk_percent
        }
    })

@app.route('/api/containers')
def get_containers():
    """Sunucudaki Docker konteynerlerini ve durumlarını listeler."""
    if not docker_client:
        return jsonify({"error": "Docker motoruna ulaşılamadı", "containers": []})
    
    try:
        containers = []
        # all=True parametresi hem çalışan hem de duran tüm konteynerleri getirir
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
    # 0.0.0.0 yapıyoruz ki ileride Docker içinden ve buluttan dış dünyaya açılabilsin
    app.run(host='0.0.0.0', port=5000, debug=True)