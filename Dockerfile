# 1. Temel işletim sistemi olarak hafif ve hızlı resmi Python imajını kullan
FROM python:3.10-slim

# 2. Konteyner içinde kendimize bir çalışma klasörü oluşturalım
WORKDIR /app

# 3. Kütüphane listemizi (requirements.txt) kutunun içine kopyala
COPY requirements.txt .

# 4. Kütüphaneleri kutunun içine kur (pip cache kullanmadan hafif tut)
RUN pip install --no-cache-dir -r requirements.txt

# 5. Kalan tüm projemizi (app.py, templates/ vb.) kutunun içine kopyala
COPY . .

# 6. Uygulamamızın 5000 portundan dışarı sesleneceğini belirt
EXPOSE 5000

# 7. Konteyner çalıştığında otomatik olarak uygulamamızı başlatan komut
CMD ["python", "app.py"]