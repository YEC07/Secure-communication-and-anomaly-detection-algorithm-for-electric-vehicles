import paho.mqtt.client as mqtt
import json
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend
import csv
import hashlib
from anomaly_detector import AnomalyDetector, Geography
import random
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

# AES Şifreleme için anahtar ve IV (Publisher ile aynı olmalı)
AES_KEY = b"1234567890123456"  # Publisher ile aynı key
AES_IV = b"abcdef1234567890"  # Publisher ile aynı IV

# AES Şifre Çözme Fonksiyonu
def decrypt_data(data, key, iv):
    """
    AES ile şifrelenmiş veriyi çözer.
    
    Args:
        data (bytes): Şifreli veri
        key (bytes): AES şifreleme anahtarı
        iv (bytes): Başlangıç vektörü
        
    Returns:
        bytes: Çözülmüş veri
    """
    backend = default_backend()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=backend)
    decryptor = cipher.decryptor()
    unpadder = padding.PKCS7(128).unpadder()
    decrypted_data = decryptor.update(data) + decryptor.finalize()
    return unpadder.update(decrypted_data) + unpadder.finalize()

# Anomali detektörü oluştur
detector = AnomalyDetector()

# Sabit araç listesi tanımlayalım
VEHICLE_IDS = [
    "VHC_01",  
    "VHC_02",  
    "VHC_03",  
    "VHC_04",  
    "VHC_05"   
]

# Araç sırası için sayaç
current_vehicle_index = 0

# InfluxDB bağlantı ayarları
INFLUXDB_URL = "http://localhost:8086"
INFLUXDB_TOKEN = "zvVmH29guaZMTKp_ND5TJVISJuS0kBsDnbvBcoVVSWi8m1znXyOaJaPboGSBriX-1VA-J_7WICAUOSpsB54r2Q=="
INFLUXDB_ORG = "canbus"
INFLUXDB_BUCKET = "can_data"

# InfluxDB istemcisi
influx_client = InfluxDBClient(
    url=INFLUXDB_URL,
    token=INFLUXDB_TOKEN,
    org=INFLUXDB_ORG
)
write_api = influx_client.write_api(write_options=SYNCHRONOUS)

def save_to_influxdb(vehicle_id: str, can_data: dict, geography: Geography):
    """
    CAN verilerini InfluxDB'ye kaydeder.
    """
    try:
        # Temel point oluştur
        point = Point("can_messages") \
            .tag("vehicle_id", vehicle_id) \
            .tag("message_type", can_data["name"]) \
            .tag("geography", geography.value)

        # Sinyal değerlerini ekle
        for signal_name, value in can_data["signals"].items():
            if isinstance(value, (int, float)):
                point = point.field(signal_name, float(value))

        # InfluxDB'ye yaz
        write_api.write(bucket=INFLUXDB_BUCKET, record=point)
        print(f"✅ InfluxDB'ye kaydedildi: {vehicle_id} - {can_data['name']}")

    except Exception as e:
        print(f"❌ InfluxDB kayıt hatası: {e}")

# MQTT Mesaj Alındığında Çalışacak Fonksiyon
def on_message(client, userdata, msg):
    """
    MQTT mesajı alındığında çalışan callback fonksiyonu.
    
    Args:
        client: MQTT istemcisi
        userdata: Kullanıcı verileri
        msg: MQTT mesajı
        
    İşlem Adımları:
        1. JSON mesajını çöz
        2. Veri bütünlüğünü kontrol et (SHA-256)
        3. AES şifresini çöz
        4. Sinyal değerlerini kontrol et ve düzelt
        5. Anomali kontrolü yap
        6. CSV'ye kaydet
        
    Özel Kontroller:
        - AC/Fan ilişkisi kontrolü
        - Tam sayı dönüşümleri
        - Coğrafya bazlı kontroller
    """
    try:
        global current_vehicle_index
        
        # Gelen mesajı JSON olarak yükle
        message = json.loads(msg.payload.decode('utf-8'))
        encrypted_data = bytes.fromhex(message["data"])
        sha256_hash = message["hash"]
        iv = bytes.fromhex(message["iv"])

        # Veri bütünlüğü kontrolü
        calculated_hash = hashlib.sha256(encrypted_data).hexdigest()
        if calculated_hash != sha256_hash:
            print("Veri bütünlüğü bozulmuş!")
            return

        # AES ile şifre çözme
        decrypted_data = decrypt_data(encrypted_data, AES_KEY, iv)
        can_data = json.loads(decrypted_data.decode('utf-8'))

        # Klima ve fan ilişkisini kontrol et ve düzelt
        if can_data["name"] == "ClimateControl":
            signals = can_data["signals"]
            cabin_temp = signals.get("CabinTemp", 20)
            ac_status = int(signals.get("ACStatus", 0))
            fan_speed = int(signals.get("FanSpeed", 0))
            
            print("\n🌡️ KLİMA KONTROL VERİLERİ")
            print(f"   ├─ Kabin Sıcaklığı: {cabin_temp}°C")
            print(f"   ├─ AC Durumu: {'Açık' if ac_status == 1 else 'Kapalı'}")
            print(f"   └─ Fan Hızı: {fan_speed}")
            
            # Mantıksal kontroller
            if ac_status == 1 and fan_speed == 0:
                print("   ⚠️ AC açık ama fan kapalı! Fan hızı ayarlanıyor...")
                can_data["signals"]["FanSpeed"] = random.randint(1, 3)
            elif ac_status == 0 and fan_speed > 0:
                print("   ⚠️ AC kapalı ama fan çalışıyor! Fan kapatılıyor...")
                can_data["signals"]["FanSpeed"] = 0
        
        # Diğer tam sayı dönüşümleri
        if "GearPosition" in can_data["signals"]:
            can_data["signals"]["GearPosition"] = int(can_data["signals"]["GearPosition"])
        
        # Sırayla araç seç
        vehicle_id = VEHICLE_IDS[current_vehicle_index]
        current_vehicle_index = (current_vehicle_index + 1) % len(VEHICLE_IDS)
        geography = random.choice(list(Geography))
        
        print("\n" + "="*60)
        print(f"📍 ARAÇ DURUMU")
        print("="*60)
        print(f"🚗 Araç ID: {vehicle_id}")
        print(f"🌍 Coğrafya: {geography.value}")
        print(f"📝 Mesaj Tipi: {can_data['name']}")
        print(f"📊 Sinyaller:")
        for key, value in can_data['signals'].items():
            if key in ["GearPosition", "FanSpeed", "ACStatus"]:
                print(f"   ├─ {key}: {int(value)}")
            else:
                print(f"   ├─ {key}: {value:.1f}")
        print("-"*60)
        
        # Anomali kontrolü
        detector.update_vehicle_state(vehicle_id, can_data, geography)
        
        # InfluxDB'ye kaydet
        save_to_influxdb(vehicle_id, can_data, geography)
        
        # CSV kayıt
        save_to_csv(can_data)

    except Exception as e:
        print(f"❌ HATA: {e}")

# CSV'ye Veri Kaydetme
def save_to_csv(data):
    """
    CAN verilerini CSV dosyasına kaydeder.
    
    Args:
        data (Dict): Kaydedilecek CAN verisi
        
    Dosya Formatı:
        ID, Name, Raw Data, Signals
        
    Not:
        - İlk çalıştırmada başlıklar eklenir
        - Veriler append modunda eklenir
    """
    filename = "can_data.csv"

    # Başlıkları sadece ilk çalıştırmada ekle
    try:
        with open(filename, mode="x", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["ID", "Name", "Raw Data", "Signals"])
    except FileExistsError:
        pass

    # Veriyi CSV'ye ekle
    with open(filename, mode="a", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([
            data["id"],
            data["name"],
            data["data"],
            json.dumps(data["signals"])
        ])

# MQTT Abonelik Başlatma
def subscribe_to_data():
    """
    MQTT broker'a bağlanır ve veri dinlemeye başlar.
    
    Bağlantı Detayları:
        Host: localhost
        Port: 1883
        Topic: can/data
        
    Not:
        Ctrl+C ile durdurulana kadar çalışır
    """
    client = mqtt.Client()
    client.on_message = on_message
    client.connect("localhost", 1883, 60)
    client.subscribe("can/data")  # Publisher ile aynı kanal
    print("Subscriber dinlemeye başladı...")
    client.loop_forever()

if __name__ == "__main__":
    subscribe_to_data()
