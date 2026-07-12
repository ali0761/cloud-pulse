from flask import Flask, render_template, jsonify
import psutil
import docker

app = Flask(__name__)

@app.route('/')
def index():
    return "<h1>CloudPulse DevOps Paneli Çalışıyor! 🚀</h1>"

if __name__ == '__main__':
    # 0.0.0.0 yapıyoruz ki ileride Docker içinden dış dünyaya açılabilsin
    app.run(host='0.0.0.0', port=5000, debug=True)