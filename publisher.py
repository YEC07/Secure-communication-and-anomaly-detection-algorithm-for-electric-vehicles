import json
import random
import hashlib
import time
import csv
from typing import Dict, Any, List
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend
import paho.mqtt.client as mqtt

# AES Şifreleme için anahtar ve IV (Key 16, 24 veya 32 byte olmalı)
AES_KEY = b"1234567890123456"  # 16 byte sabit key
AES_IV = b"abcdef1234567890"  # 16 byte sabit IV

# DBC formatında JSON verisi
DBC_DATA = {
    "messages": [
        {
            "id": "0x123",
            "name": "EngineData",
            "length": 8,
            "signals": [
                {"name": "EngineSpeed", "start_bit": 0, "bit_length": 16, "factor": 1.0, "offset": 0, "min": 800, "max": 6000},  # RPM
                {"name": "EngineTemp", "start_bit": 16, "bit_length": 8, "factor": 1.0, "offset": 0, "min": 60, "max": 120},    # °C
                {"name": "BatteryLevel", "start_bit": 24, "bit_length": 8, "factor": 1.0, "offset": 0, "min": 0, "max": 100}    # %
            ]
        },
        {
            "id": "0x124",
            "name": "VehicleData",
            "length": 8,
            "signals": [
                {"name": "Speed", "start_bit": 0, "bit_length": 16, "factor": 1.0, "offset": 0, "min": 0, "max": 240},    # km/s
                {"name": "GearPosition", "start_bit": 16, "bit_length": 8, "factor": 1.0, "offset": 0, "min": 1, "max": 6},  # vites
                {"name": "BatteryVoltage", "start_bit": 24, "bit_length": 8, "factor": 1.0, "offset": 0, "min": 360, "max": 420}
            ]
        },
        {
            "id": "0x125",
            "name": "ClimateControl",
            "length": 8,
            "signals": [
                {"name": "CabinTemp", "start_bit": 0, "bit_length": 8, "factor": 1.0, "offset": 0, "min": 10, "max": 35},      # °C (daha geniş aralık)
                {"name": "FanSpeed", "start_bit": 8, "bit_length": 8, "factor": 1.0, "offset": 0, "min": 0, "max": 5},         # Fan hızı (0-5)
                {"name": "ACStatus", "start_bit": 16, "bit_length": 8, "factor": 1.0, "offset": 0, "min": 0, "max": 1}         # Açık/Kapalı
            ]
        }
    ]
}

# Şifreleme Fonksiyonu
def encrypt_data(data, key, iv):
    """
    Veriyi AES algoritması ile şifreler.
    
    Args:
        data (bytes): Şifrelenecek veri
        key (bytes): AES şifreleme anahtarı
        iv (bytes): Başlangıç vektörü
        
    Returns:
        bytes: Şifrelenmiş veri
    """
    backend = default_backend()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=backend)
    encryptor = cipher.encryptor()
    padder = padding.PKCS7(128).padder()
    padded_data = padder.update(data) + padder.finalize()
    return encryptor.update(padded_data) + encryptor.finalize()

# Sinyal Değerlerini Oluşturma
def generate_signal_value(factor: float, offset: float, max_raw: int, signal_info: Dict):
    """
    DBC formatına uygun sinyal değerleri üretir.
    
    Args:
        factor (float): Çarpan değeri
        offset (float): Ofset değeri
        max_raw (int): Maksimum ham değer
        signal_info (Dict): Sinyal bilgileri
        
    Returns:
        Tuple[int, float]: (ham_değer, fiziksel_değer)
    """
    if "min" in signal_info and "max" in signal_info:
        physical_value = random.uniform(signal_info["min"], signal_info["max"])
        raw_value = int((physical_value - offset) / factor)
    else:
        raw_value = random.randint(0, max_raw)
        physical_value = (raw_value * factor) + offset
    return raw_value, physical_value

# Mesajları Bitlere Paketleme
def pack_signals_to_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sinyal değerlerini CAN mesaj formatına paketler.
    
    Args:
        message (Dict): DBC mesaj formatı
        
    Returns:
        Dict: Paketlenmiş CAN mesajı
        
    Format:
        {
            "id": "0x123",
            "name": "MessageName",
            "data": [byte1, byte2, ...],
            "signals": {"SignalName": value, ...}
        }
    """
    raw_data = [0] * message["length"]

    signal_values = {}
    for signal in message["signals"]:
        raw_value, physical_value = generate_signal_value(
            signal["factor"],
            signal["offset"],
            (1 << signal["bit_length"]) - 1,
            signal
        )

        start_byte = signal["start_bit"] // 8
        start_bit = signal["start_bit"] % 8
        for i in range(signal["bit_length"]):
            bit_index = start_bit + i
            byte_index = start_byte + (bit_index // 8)
            bit_position = bit_index % 8
            raw_data[byte_index] |= ((raw_value >> i) & 1) << bit_position

        signal_values[signal["name"]] = physical_value

    return {"id": message["id"], "name": message["name"], "data": raw_data, "signals": signal_values}

# Rastgele CAN Mesajı Üretimi
def generate_can_message():
    messages = DBC_DATA["messages"]
    selected_message = random.choice(messages)
    return pack_signals_to_message(selected_message)

# Mesaj Oluşturma ve Şifreleme
def create_and_encrypt_message():
    can_message = generate_can_message()
    can_json = json.dumps(can_message).encode('utf-8')
    encrypted_data = encrypt_data(can_json, AES_KEY, AES_IV)
    sha256_hash = hashlib.sha256(encrypted_data).hexdigest()

    return {
        "message": can_message,
        "mqtt_payload": json.dumps({
            "data": encrypted_data.hex(),
            "hash": sha256_hash,
            "iv": AES_IV.hex()
        }),
        "encrypted_data": encrypted_data.hex(),
        "hash": sha256_hash
    }

# MQTT ile Mesaj Yayını
def publish_message(client, message):
    client.publish("can/data", message)
    print("Mesaj yayınlandı:", message)

# Şifreli Mesajları CSV'ye Yazma
def save_encrypted_to_csv(data, filename="encrypted_messages.csv"):
    try:
        # Başlıkları yalnızca dosya ilk oluşturulduğunda ekle
        with open(filename, mode="x", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["Message ID", "Name", "Encrypted Data", "SHA-256 Hash"])
    except FileExistsError:
        pass

    # Veriyi CSV'ye ekle
    with open(filename, mode="a", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([data["message"]["id"], data["message"]["name"], data["encrypted_data"], data["hash"]])

# Publisher
def start_publisher():
    client = mqtt.Client()
    client.connect("localhost", 1883, 60)

    try:
        while True:
            result = create_and_encrypt_message()
            publish_message(client, result["mqtt_payload"])
            save_encrypted_to_csv(result)

            # Her 5 saniyede bir mesaj gönder
            time.sleep(5)
    except KeyboardInterrupt:
        print("Publisher durduruldu.")
    finally:
        client.disconnect()

def get_appropriate_gear(speed: float) -> int:
    """Hıza göre uygun vitesi belirler"""
    if speed == 0:
        return 1
    elif speed <= 20:
        return 1
    elif speed <= 40:
        return 2
    elif speed <= 70:
        return 3
    elif speed <= 100:
        return 4
    elif speed <= 150:
        return 5
    else:
        return 6

def generate_vehicle_data() -> Dict[str, float]:
    """
    VehicleData mesajı için gerçekçi veriler üretir.
    
    Returns:
        Dict: Araç verileri
        
    Üretilen Veriler:
        Speed: 0-240 km/s arası hız
        GearPosition: Hıza uygun vites (1-6)
        BatteryVoltage: 360-420V arası voltaj
    """
    # Önce hızı üret
    speed = round(random.uniform(0, 240), 1)
    
    # Hıza uygun vitesi belirle (tam sayı olarak)
    gear = int(get_appropriate_gear(speed))
    
    # Batarya voltajını üret
    battery_voltage = round(random.uniform(360, 420), 1)
    
    return {
        "Speed": speed,
        "GearPosition": gear,
        "BatteryVoltage": battery_voltage
    }

def generate_climate_control_data() -> Dict[str, float]:
    """
    ClimateControl mesajı için gerçekçi veriler üretir.
    
    Returns:
        Dict: Klima kontrol verileri
        
    Üretilen Veriler:
        CabinTemp: 10-35°C arası kabin sıcaklığı
        FanSpeed: 0-5 arası fan hızı
        ACStatus: 0 (kapalı) veya 1 (açık)
        
    Özel Durumlar:
        - Kritik sıcaklıklarda (%90) AC açık olma olasılığı
        - Normal sıcaklıklarda (%70) AC açık olma olasılığı
        - AC açıksa fan hızı 1-5 arası
        - AC kapalıysa fan hızı 0
    """
    # Daha geniş sıcaklık aralığında veri üret
    cabin_temp = round(random.uniform(10, 35), 1)
    
    # Sıcaklık kritik seviyelerdeyse AC'nin açık olma olasılığını artır
    if cabin_temp < 15 or cabin_temp > 30:
        ac_status = int(random.choices([1, 0], weights=[60, 40])[0])  # %90 açık olma olasılığı
    else:
        ac_status = int(random.choices([1, 0], weights=[30, 70])[0])  # Normal durumdaki %70 açık olma olasılığı
    
    # AC açıksa fan da çalışmalı (1-5 arası)
    fan_speed = int(random.randint(1, 5)) if ac_status == 1 else 0
    
    return {
        "CabinTemp": cabin_temp,
        "FanSpeed": fan_speed,
        "ACStatus": ac_status
    }

def generate_message_data(message_info: Dict) -> Dict[str, Any]:
    """Mesaj tipine göre uygun veri üretir"""
    signals = {}
    
    for signal in message_info["signals"]:
        signal_name = signal["name"]
        
        # Tam sayı olması gereken sinyaller
        if signal_name in ["GearPosition", "FanSpeed", "ACStatus"]:
            value = int(random.randint(int(signal["min"]), int(signal["max"])))
        else:
            # Ondalıklı sayı olabilecek sinyaller
            value = round(random.uniform(signal["min"], signal["max"]), 1)
        
        signals[signal_name] = value
    
    return {
        "id": message_info["id"],
        "name": message_info["name"],
        "data": random.randint(0, 255),
        "signals": signals
    }

if __name__ == "__main__":
    start_publisher()