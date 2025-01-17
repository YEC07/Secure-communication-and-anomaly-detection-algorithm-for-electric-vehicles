from typing import Dict, Any
from dataclasses import dataclass
from enum import Enum
import json
import time
from sklearn.ensemble import IsolationForest
import numpy as np
from collections import defaultdict
from joblib import dump, load
import os
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

class Geography(Enum):
    """
    Araçların bulunabileceği coğrafi konumları tanımlayan enum sınıfı.
    
    Values:
        RAINY: Yağmurlu hava koşulları
        MOUNTAINOUS: Dağlık bölge
        URBAN: Şehir içi
        HIGHWAY: Otoyol
        HOT: Sıcak bölge
        SNOWY: Karlı hava koşulları
    """
    RAINY = "rainy"
    MOUNTAINOUS = "mountainous"
    URBAN = "urban"
    HIGHWAY = "highway"
    HOT = "hot"       
    SNOWY = "snowy"    

@dataclass
class VehicleState:
    """
    Araç durumunu tutan veri sınıfı.
    
    Attributes:
        vehicle_id (str): Araç kimlik numarası
        last_update (float): Son güncelleme zamanı (timestamp)
        last_values (Dict): Son alınan sinyal değerleri
        geography (Geography): Aracın bulunduğu coğrafya
    """
    vehicle_id: str
    last_update: float
    last_values: Dict[str, Dict[str, float]]
    geography: Geography

class AnomalyDetector:
    """
    Araç verilerindeki anomalileri tespit eden ana sınıf.
    
    Methods:
        update_vehicle_state: Araç durumunu günceller ve anomali kontrollerini başlatır
        _check_temporal_anomalies: Zamansal anomalileri kontrol eder
        _check_geography_based_anomalies: Coğrafyaya bağlı anomalileri kontrol eder
        _check_signal_based_anomalies: Sinyal bazlı anomalileri kontrol eder
    """
    
    def __init__(self):
        self.vehicle_states: Dict[str, VehicleState] = {}
        self.min_samples_for_training = 500
        self.models_dir = "trained_models"
        
        # Model dosya yolları
        self.model_paths = {
            "EngineData": f"{self.models_dir}/engine_model.joblib",
            "VehicleData": f"{self.models_dir}/vehicle_model.joblib",
            "ClimateControl": f"{self.models_dir}/climate_model.joblib"
        }
        
        # Modelleri yükle veya oluştur
        self._initialize_models()
        
        self.collected_data = defaultdict(list)
        self.training_progress = {
            "EngineData": 0,
            "VehicleData": 0,
            "ClimateControl": 0
        }
        
        # Eğitilmiş model kontrolü
        self.is_model_trained = self._check_trained_models()
        
        # InfluxDB bağlantısı
        self.influx_client = InfluxDBClient(
            url="http://localhost:8086",
            token="zvVmH29guaZMTKp_ND5TJVISJuS0kBsDnbvBcoVVSWi8m1znXyOaJaPboGSBriX-1VA-J_7WICAUOSpsB54r2Q==",
            org="canbus"
        )
        self.write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)

    def _initialize_models(self):
        """Modelleri yükler veya yeni oluşturur"""
        # Models dizinini oluştur
        os.makedirs(self.models_dir, exist_ok=True)
        
        self.isolation_forests = {}
        
        # Her model için kontrol et ve yükle
        for message_type, model_path in self.model_paths.items():
            if os.path.exists(model_path):
                print(f"💾 Kayıtlı model yükleniyor: {message_type}")
                try:
                    self.isolation_forests[message_type] = load(model_path)
                except Exception as e:
                    print(f"⚠️ Model yüklenirken hata: {e}")
                    self._create_new_model(message_type)
            else:
                self._create_new_model(message_type)

    def _create_new_model(self, message_type: str):
        """Yeni bir Isolation Forest modeli oluşturur"""
        print(f"🆕 Yeni model oluşturuluyor: {message_type}")
        self.isolation_forests[message_type] = IsolationForest(
            contamination=0.05,
            random_state=42,
            n_estimators=100,
            max_samples='auto',
            n_jobs=-1
        )

    def _check_trained_models(self) -> bool:
        """Tüm modellerin eğitilmiş olup olmadığını kontrol eder"""
        for model_path in self.model_paths.values():
            if not os.path.exists(model_path):
                return False
        return True

    def _save_models(self):
        """Eğitilmiş modelleri dosyaya kaydeder"""
        print("\n💾 Modeller kaydediliyor...")
        for message_type, model in self.isolation_forests.items():
            model_path = self.model_paths[message_type]
            try:
                dump(model, model_path)
                print(f"   ├─ {message_type} modeli kaydedildi")
            except Exception as e:
                print(f"   ├─ ❌ {message_type} modeli kaydedilemedi: {e}")
        print("   └─ ✅ Kayıt işlemi tamamlandı")

    def _prepare_data_for_isolation_forest(self, message_name: str, signals: Dict[str, float]) -> np.ndarray:
        """Sinyal verilerini Isolation Forest için hazırlar"""
        if message_name == "EngineData":
            return np.array([[
                signals.get("EngineSpeed", 0),
                signals.get("EngineTemp", 0),
                signals.get("BatteryLevel", 0)
            ]])
        elif message_name == "VehicleData":
            return np.array([[
                signals.get("Speed", 0),
                signals.get("GearPosition", 0),
                signals.get("BatteryVoltage", 0)
            ]])
        elif message_name == "ClimateControl":
            return np.array([[
                signals.get("CabinTemp", 0),
                signals.get("FanSpeed", 0),
                signals.get("ACStatus", 0)
            ]])
        return np.array([])

    def _train_models_if_needed(self):
        """Yeterli veri toplandığında modelleri eğitir"""
        if self.is_model_trained:
            return

        # Eğitim durumunu güncelle ve göster
        for message_name, data in self.collected_data.items():
            current_samples = len(data)
            self.training_progress[message_name] = current_samples
            progress_percentage = (current_samples / self.min_samples_for_training) * 100
            
            print(f"\r🔄 Eğitim İlerlemesi: {message_name}: {progress_percentage:.1f}% "
                  f"({current_samples}/{self.min_samples_for_training})", end="")

        can_train = all(
            len(data) >= self.min_samples_for_training 
            for data in self.collected_data.values()
        )

        if can_train:
            print("\n\n🤖 ISOLATION FOREST MODELLERİ EĞİTİLİYOR")
            print("   ├─ Bu işlem birkaç saniye sürebilir...")
            
            for message_name, data in self.collected_data.items():
                data_array = np.array(data)
                print(f"   ├─ {message_name} modeli eğitiliyor...")
                self.isolation_forests[message_name].fit(data_array)
            
            # Eğitilen modelleri kaydet
            self._save_models()
            
            self.is_model_trained = True
            print("\n📊 Model İstatistikleri:")
            for message_name, data in self.collected_data.items():
                print(f"   ├─ {message_name}: {len(data)} örnek kullanıldı")

    def _check_isolation_forest_anomalies(self, message_name: str, signals: Dict[str, float], vehicle_id: str):
        """Isolation Forest ile anomali tespiti yapar ve sebep analizi ekler"""
        # Önce ClimateControl sinyallerini tam sayıya çevirelim
        if message_name == "ClimateControl":
            signals["ACStatus"] = int(signals["ACStatus"])
            signals["FanSpeed"] = int(signals["FanSpeed"])

        data = self._prepare_data_for_isolation_forest(message_name, signals)
        if len(data) == 0:
            return

        # Veri toplama
        self.collected_data[message_name].append(data[0])
        self._train_models_if_needed()

        if self.is_model_trained:
            prediction = self.isolation_forests[message_name].predict(data)
            if prediction[0] == -1:  # Anomali tespit edildi
                # InfluxDB'ye anomaliyi kaydet
                self._save_anomaly_to_influxdb(
                    vehicle_id=vehicle_id,
                    anomaly_type="isolation_forest",
                    message_type=message_name,
                    signals=signals,
                    geography=self.vehicle_states[vehicle_id].geography,
                    severity="warning",
                    details=f"Isolation Forest anomalisi tespit edildi: {message_name}"
                )
                
                print(f"\n🤖 ISOLATION FOREST ANOMALİSİ")
                print(f"   ├─ Mesaj Tipi: {message_name}")
                print(f"   ├─ Sinyaller: {json.dumps(signals, indent=2)}")
                print(f"   └─ Olası Sebepler:")
                
                if message_name == "ClimateControl":
                    if signals["CabinTemp"] > 30 and signals["ACStatus"] == 0:
                        print(f"      ├─ ⚠️ Yüksek kabin sıcaklığı ({signals['CabinTemp']:.1f}°C) ancak klima kapalı")
                    if signals["CabinTemp"] > 30 and signals["FanSpeed"] == 0:
                        print(f"      ├─ ⚠️ Sıcak kabinde fan çalıştırılması önerilir")
                    if signals["ACStatus"] == 1 and signals["FanSpeed"] == 0:
                        print(f"      ├─ ⚠️ Hatalı veri: Klima açık iken fan kapalı olamaz")
                
                elif message_name == "EngineData":
                    if signals["EngineTemp"] > 100:
                        print(f"      ├─ ⚠️ Motor aşırı sıcak, lütfen kontrol ediniz ({signals['EngineTemp']:.1f}°C)")
                    if signals["EngineSpeed"] > 5000:
                        print(f"      ├─ ⚠️ Motor aşırı yüksek devirde, lütfen kontrol ediniz ({signals['EngineSpeed']:.1f} RPM)")
                    if signals["BatteryLevel"] < 20:
                        print(f"      └─ ⚠️ Kritik batarya seviyesi, lütfen aracı şarj ediniz (%{signals['BatteryLevel']:.1f})") 
                
                elif message_name == "VehicleData":
                    if signals["Speed"] > 120:
                        print(f"      ├─ ⚠️ Aşırı yüksek hız, lütfen yavaşlayınız. ({signals['Speed']:.1f} km/s)")
                    if signals["Speed"] > 60 and signals["GearPosition"] <= 2:
                        print(f"      ├─ ⚠️ Yüksek hızda düşük vites kullanımı, lütfen vites yükseltiniz.")
                    if signals["BatteryVoltage"] < 370 or signals["BatteryVoltage"] > 410:
                        print(f"      └─ ⚠️ Anormal batarya voltajı, lütfen kontrol ediniz ({signals['BatteryVoltage']:.1f}V)")

    def update_vehicle_state(self, vehicle_id: str, message_data: Dict[str, Any], geography: Geography):
        """
        Araç durumunu günceller ve tüm anomali kontrollerini başlatır.
        
        Args:
            vehicle_id (str): Araç kimlik numarası
            message_data (Dict): CAN veri mesajı
            geography (Geography): Aracın bulunduğu coğrafya
            
        Returns:
            None
        """
        current_time = time.time()
        message_name = message_data["name"]
        signals = message_data["signals"]
        
        # Mevcut araç durumunu güncelle veya yeni oluştur
        if vehicle_id not in self.vehicle_states:
            self.vehicle_states[vehicle_id] = VehicleState(
                vehicle_id=vehicle_id,
                last_update=current_time,
                last_values={},
                geography=geography
            )
        
        # Isolation Forest kontrolünü ekle
        self._check_isolation_forest_anomalies(message_name, signals, vehicle_id)
        
        # Diğer mevcut kontroller...
        state = self.vehicle_states[vehicle_id]
        state.geography = geography
        
        if message_name not in state.last_values:
            state.last_values[message_name] = signals
        else:
            self._check_temporal_anomalies(state, message_name, signals)
            self._check_geography_based_anomalies(state, message_name, signals)
            self._check_signal_based_anomalies(signals, message_name)
            
            state.last_values[message_name] = signals
            state.last_update = current_time
    
    def _check_geography_based_anomalies(self, state: VehicleState, message_name: str, signals: Dict[str, float]):
        """
        Coğrafyaya bağlı anomalileri kontrol eder.
        
        Args:
            state (VehicleState): Araç durum bilgisi
            message_name (str): Mesaj tipi
            signals (Dict): Güncel sinyal değerleri
            
        Coğrafya Bazlı Kontroller:
            RAINY: Yağmurda yüksek hız (>70 km/s)
            MOUNTAINOUS: Motor sıcaklığı (>95°C), Düşük hız (<20 km/s)
            HOT: Motor sıcaklığı (>100°C), Kabin sıcaklığı (>28°C)
            SNOWY: Yüksek hız (>50 km/s), Düşük kabin sıcaklığı (<18°C)
            URBAN: Hız limiti (>50 km/s), Yüksek motor devri (>4000 RPM)
            HIGHWAY: Düşük hız (<60 km/s)
        """
        if state.geography == Geography.RAINY:
            if message_name == "VehicleData" and signals.get("Speed", 0) > 70:
                self._save_anomaly_to_influxdb(
                    vehicle_id=state.vehicle_id,
                    anomaly_type="high_speed_in_rain",
                    message_type=message_name,
                    signals=signals,
                    geography=state.geography,
                    severity="warning",
                    details=f"Yağmurlu havada yüksek hız: {signals['Speed']:.1f} km/s"
                )
        
        elif state.geography == Geography.MOUNTAINOUS:
            if message_name == "EngineData" and signals.get("EngineTemp", 0) > 95:
                self._save_anomaly_to_influxdb(
                    vehicle_id=state.vehicle_id,
                    anomaly_type="high_temperature_in_mountainous",
                    message_type=message_name,
                    signals=signals,
                    geography=state.geography,
                    severity="warning",
                    details=f"Dağlık bölgede yüksek motor sıcaklığı: {signals['EngineTemp']:.1f}°C"
                )
            
            if message_name == "VehicleData" and signals.get("Speed", 0) > 70:
                self._save_anomaly_to_influxdb(
                    vehicle_id=state.vehicle_id,
                    anomaly_type="high_speed_in_mountainous",
                    message_type=message_name,
                    signals=signals,
                    geography=state.geography,
                    severity="warning",
                    details=f"Dağlık bölgede yüksek hız uyarısı: {signals['Speed']:.1f} km/s"
                )
        
        elif state.geography == Geography.HOT:
            if message_name == "EngineData" and signals.get("EngineTemp", 0) > 100:
                self._save_anomaly_to_influxdb(
                    vehicle_id=state.vehicle_id,
                    anomaly_type="high_temperature_in_hot",
                    message_type=message_name,
                    signals=signals,
                    geography=state.geography,
                    severity="warning",
                    details=f"Sıcak bölgede yüksek motor sıcaklığı: {signals['EngineTemp']:.1f}°C"
                )
            
            if message_name == "ClimateControl":
                if signals.get("CabinTemp", 20) > 28:
                    self._save_anomaly_to_influxdb(
                        vehicle_id=state.vehicle_id,
                        anomaly_type="high_cabin_temperature",
                        message_type=message_name,
                        signals=signals,
                        geography=state.geography,
                        severity="warning",
                        details=f"Yüksek kabin sıcaklığı: {signals['CabinTemp']:.1f}°C"
                    )
                
                if signals.get("ACStatus", 1) == 0:
                    self._save_anomaly_to_influxdb(
                        vehicle_id=state.vehicle_id,
                        anomaly_type="ac_off_in_hot",
                        message_type=message_name,
                        signals=signals,
                        geography=state.geography,
                        severity="warning",
                        details=f"Klima kapalı!"
                    )
        
        elif state.geography == Geography.SNOWY:
            if message_name == "VehicleData" and signals.get("Speed", 0) > 50:
                self._save_anomaly_to_influxdb(
                    vehicle_id=state.vehicle_id,
                    anomaly_type="high_speed_in_snow",
                    message_type=message_name,
                    signals=signals,
                    geography=state.geography,
                    severity="warning",
                    details=f"Karli havada yüksek hız: {signals['Speed']:.1f} km/s"
                )
            
            if message_name == "ClimateControl" and signals.get("CabinTemp", 20) < 18:
                self._save_anomaly_to_influxdb(
                    vehicle_id=state.vehicle_id,
                    anomaly_type="low_cabin_temperature",
                    message_type=message_name,
                    signals=signals,
                    geography=state.geography,
                    severity="warning",
                    details=f"Düşük kabin sıcaklığı: {signals['CabinTemp']:.1f}°C"
                )
        
        elif state.geography == Geography.URBAN:
            if message_name == "VehicleData" and signals.get("Speed", 0) > 60:
                self._save_anomaly_to_influxdb(
                    vehicle_id=state.vehicle_id,
                    anomaly_type="high_speed_in_urban",
                    message_type=message_name,
                    signals=signals,
                    geography=state.geography,
                    severity="warning",
                    details=f"Şehir içi hız limiti aşımı: {signals['Speed']:.1f} km/s"
                )
            
            if message_name == "EngineData" and signals.get("EngineSpeed", 0) > 4000:
                self._save_anomaly_to_influxdb(
                    vehicle_id=state.vehicle_id,
                    anomaly_type="high_engine_speed",
                    message_type=message_name,
                    signals=signals,
                    geography=state.geography,
                    severity="warning",
                    details=f"Yüksek motor devri: {signals['EngineSpeed']:.1f} RPM"
                )

        elif state.geography == Geography.HIGHWAY:
            if message_name == "VehicleData" and signals.get("Speed", 0) < 60:
                self._save_anomaly_to_influxdb(
                    vehicle_id=state.vehicle_id,
                    anomaly_type="low_speed_in_highway",
                    message_type=message_name,
                    signals=signals,
                    geography=state.geography,
                    severity="warning",
                    details=f"Düşük hız: {signals['Speed']:.1f} km/s"
                )
    
    def _check_temporal_anomalies(self, state: VehicleState, message_name: str, signals: Dict[str, float]):
        """
        Zamansal anomalileri kontrol eder.
        
        Args:
            state (VehicleState): Araç durum bilgisi
            message_name (str): Mesaj tipi (VehicleData, EngineData, ClimateControl)
            signals (Dict): Güncel sinyal değerleri
            
        Kontrol Edilen Anomaliler:
            - Ani hız değişimleri (>20 km/s)
            - Ani vites değişimleri (>1 vites)
            - Ani motor sıcaklığı değişimleri (>15°C)
            - Ani motor devri değişimleri (>2000 RPM)
            - Ani batarya seviyesi düşüşleri (>%10)
            - Ani kabin sıcaklığı değişimleri (>5°C)
        """
        last_signals = state.last_values[message_name]
        time_diff = time.time() - state.last_update
        
        print(f"\n⏱️  ZAMANSAL ANALİZ")
        print(f"   └─ Araç: {state.vehicle_id}")
        
        if message_name == "VehicleData":
            # hız kontrol
            speed_diff = abs(signals["Speed"] - last_signals["Speed"])
            if speed_diff > 20:
                self._save_anomaly_to_influxdb(
                    vehicle_id=state.vehicle_id,
                    anomaly_type="sudden_speed_change",
                    message_type=message_name,
                    signals=signals,
                    geography=state.geography,
                    severity="warning",
                    details=f"Ani hız değişimi: {speed_diff:.1f} km/s"
                )
            
            # Ani vites değişimi
            gear_diff = abs(signals["GearPosition"] - last_signals["GearPosition"])
            if gear_diff > 1:
                print("\n⚠️  VİTES ANOMALİSİ")
                print(f"   ├─ Önceki vites: {last_signals['GearPosition']}")
                print(f"   ├─ Yeni vites: {signals['GearPosition']}")
                print(f"   └─ Atlanan vites sayısı: {gear_diff}")
        
        elif message_name == "EngineData":
            # motor kontrol
            temp_diff = abs(signals["EngineTemp"] - last_signals["EngineTemp"])
            if temp_diff > 15:
                self._save_anomaly_to_influxdb(
                    vehicle_id=state.vehicle_id,
                    anomaly_type="sudden_temperature_change",
                    message_type=message_name,
                    signals=signals,
                    geography=state.geography,
                    severity="warning",
                    details=f"Ani sıcaklık değişimi: {temp_diff:.1f}°C"
                )
            
            # Yeni kontrol: Ani motor devri değişimi
            engine_speed_diff = abs(signals["EngineSpeed"] - last_signals["EngineSpeed"])
            if engine_speed_diff > 2000:
                print("\n⚠️  MOTOR DEVRİ ANOMALİSİ")
                print(f"   ├─ Önceki devir: {last_signals['EngineSpeed']:.1f} RPM")
                print(f"   ├─ Yeni devir: {signals['EngineSpeed']:.1f} RPM")
                print(f"   └─ Değişim: {engine_speed_diff:.1f} RPM")
            
            # Yeni kontrol: Ani batarya seviyesi düşüşü
            battery_diff = last_signals["BatteryLevel"] - signals["BatteryLevel"]
            if battery_diff > 10:  # %10'dan fazla ani düşüş
                print("\n🔋 BATARYA ANOMALİSİ")
                print(f"   ├─ Önceki seviye: %{last_signals['BatteryLevel']:.1f}")
                print(f"   ├─ Yeni seviye: %{signals['BatteryLevel']:.1f}")
                print(f"   └─ Ani düşüş: %{battery_diff:.1f}")
        
        elif message_name == "ClimateControl":
            cabin_temp_diff = abs(signals["CabinTemp"] - last_signals["CabinTemp"])
            if cabin_temp_diff > 5:
                print("\n🌡️  KABİN SICAKLIĞI ANOMALİSİ")
                print(f"   ├─ Önceki sıcaklık: {last_signals['CabinTemp']:.2f}°C")
                print(f"   ├─ Yeni sıcaklık: {signals['CabinTemp']:.2f}°C")
                print(f"   └─ Değişim: {cabin_temp_diff:.2f}°C")
    
    def _check_signal_based_anomalies(self, signals: Dict[str, float], message_name: str):
        """
        Genel sinyal bazlı anomalileri kontrol eder.
        
        Args:
            signals (Dict): Güncel sinyal değerleri
            message_name (str): Mesaj tipi
            
        Kontrol Edilen Anomaliler:
            EngineData:
                - Kritik motor sıcaklığı (>120°C)
                - Düşük batarya seviyesi (<30%)
                - Kritik batarya seviyesi (<20%)
            
            VehicleData:
                - Hız-vites uyumsuzluğu
                - Kritik vites kullanımı (yüksek hızda düşük vites)
            
            ClimateControl:
                - Kritik kabin sıcaklıkları (<15°C, >30°C)
                - Klima kullanım uyarıları
        """
        if message_name == "EngineData":
            # Motor sıcaklığı kontrolü
            if signals.get("EngineTemp", 0) > 120:
                print("\n🌡️  KRİTİK MOTOR SICAKLIĞI")
                print(f"   └─ Motor sıcaklığı: {signals['EngineTemp']:.1f}°C")
            
            # Batarya seviyesi kontrolü (coğrafyadan bağımsız)
            battery_level = signals.get("BatteryLevel", 0)
            if battery_level < 20:
                print("\n🔋 KRİTİK BATARYA SEVİYESİ")
                print(f"   ├─ Mevcut seviye: %{battery_level:.1f}")
                print(f"   └─ Şarj gerekli!")
            elif battery_level < 30:
                print("\n🔋 DÜŞÜK BATARYA SEVİYESİ")
                print(f"   └─ Mevcut seviye: %{battery_level:.1f}")
        
        elif message_name == "VehicleData":
            speed = signals.get("Speed", 0)
            gear = signals.get("GearPosition", 1)
            
            # Hız-vites uyumsuzluğu kontrolü
            if speed > 0:  # Araç hareket halindeyse
                expected_gear = self._get_expected_gear(speed)
                current_gear = int(gear)
                
                if abs(expected_gear - current_gear) > 1:  # 1 vites tolerans
                    print("\n⚙️  VİTES-HIZ UYUMSUZLUĞU")
                    print(f"   ├─ Mevcut Hız: {speed:.1f} km/s")
                    print(f"   ├─ Mevcut Vites: {current_gear}")
                    print(f"   └─ Olması Gereken Vites: {expected_gear}")
                
                # Aşırı düşük/yüksek vites kontrolleri
                if speed > 100 and current_gear <= 2:
                    print("\n⚠️  KRİTİK VİTES UYUMSUZLUĞU")
                    print(f"   ├─ Yüksek Hız: {speed:.1f} km/s")
                    print(f"   ├─ Tehlikeli Düşük Vites: {current_gear}")
                    print(f"   └─ Vites Yükseltilmeli!")
                elif speed < 20 and current_gear >= 3:
                    print("\n⚠️  KRİTİK VİTES UYUMSUZLUĞU")
                    print(f"   ├─ Düşük Hız: {speed:.1f} km/s")
                    print(f"   ├─ Tehlikeli Yüksek Vites: {current_gear}")
                    print(f"   └─ Vites Düşürülmeli!")
        
        elif message_name == "ClimateControl":
            cabin_temp = signals.get("CabinTemp", 20)
            ac_status = signals.get("ACStatus", 0)
            
            # Kabin sıcaklığı kontrolleri
            if cabin_temp < 10:
                print("\n❄️  DÜŞÜK KABİN SICAKLIĞI")
                print(f"   └─ Kabin sıcaklığı: {cabin_temp:.1f}°C")
                if ac_status == 0:
                    print("\n⚠️  UYARI")
                    print(f"   └─ Klimayı çalıştırın ve ısıtmayı açın!")
                
            elif cabin_temp > 30:
                print("\n🌡️  KRİTİK YÜKSEK KABİN SICAKLIĞI")
                print(f"   └─ Kabin sıcaklığı: {cabin_temp:.1f}°C")
                if ac_status == 0:
                    print("\n⚠️  UYARI")
                    print(f"   └─ Klimayı çalıştırın ve soğutmayı açın!")
    
    def _get_expected_gear(self, speed: float) -> int:
        """
        Hıza göre olması gereken vitesi hesaplar.
        
        Args:
            speed (float): Araç hızı (km/s)
            
        Returns:
            int: Olması gereken vites numarası (1-6)
            
        Vites Aralıkları:
            0-20 km/s: 1. vites
            21-40 km/s: 2. vites
            41-70 km/s: 3. vites
            71-100 km/s: 4. vites
            101-150 km/s: 5. vites
            150+ km/s: 6. vites
        """
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

    def print_model_details(self):
        """Eğitilmiş modellerin detaylarını gösterir"""
        print("\n📊 KAYITLI MODEL DETAYLARI")
        print("="*60)
        
        for message_type, model in self.isolation_forests.items():
            model_path = self.model_paths[message_type]
            if os.path.exists(model_path):
                file_size = os.path.getsize(model_path) / 1024  # KB cinsinden
                print(f"\n🔍 {message_type}")
                print(f"   ├─ Dosya Boyutu: {file_size:.2f} KB")
                print(f"   ├─ Ağaç Sayısı: {model.n_estimators}")
                print(f"   ├─ Contamination: {model.contamination}")
                print(f"   └─ Random State: {model.random_state}") 

    def _save_anomaly_to_influxdb(self, vehicle_id: str, anomaly_type: str, message_type: str, 
                                 signals: dict, geography: Geography, severity: str = "warning",
                                 details: str = ""):
        """Tespit edilen anomaliyi InfluxDB'ye kaydeder."""
        try:
            # Ana anomali noktası
            point = Point("anomalies") \
                .tag("vehicle_id", vehicle_id) \
                .tag("anomaly_type", anomaly_type) \
                .tag("message_type", message_type) \
                .tag("geography", geography.value) \
                .tag("severity", severity) \
                .field("anomaly_count", 1)  # Her anomali için sayaç

            # Sadece sayısal değerleri kaydet
            for signal_name, value in signals.items():
                if isinstance(value, (int, float)):
                    point = point.field(f"value_{signal_name}", float(value))

            self.write_api.write(bucket="can_data", record=point)
            print(f"✅ Anomali kaydedildi: {anomaly_type} - {details}")
            
        except Exception as e:
            print(f"❌ Anomali kayıt hatası: {e}")

detector = AnomalyDetector()
detector.print_model_details() 