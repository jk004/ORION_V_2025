import paho.mqtt.client as mqtt
import json

# Funkcja wywoływana po połączeniu z brokerem
def on_connect(client, userdata, flags, rc):
    print("✅ Połączono z brokerem MQTT (kod:", rc, ")")
    # Subskrybujemy oba tematy
    print(client.is_connected())
    client.subscribe("orion/topic/science/inbound")
    client.subscribe("orion/topic/science/outbound")
    
    #client.subscribe("chassis_output")  # Upewnij się, że to poprawny temat

# Funkcja wywoływana przy każdej odebranej wiadomości
def on_subscribe(client, userdata, mid, reason_code_list):
    # Since we subscribed only for a single channel, reason_code_list contains
    # a single entry
    print(f"Broker state subscription: {reason_code_list}")
   
        
def on_message(client, userdata, msg):
    try:
        payload_str = msg.payload.decode()
        message = json.loads(payload_str)

        event_type = message.get("eventType", "Brak eventType")
        payload = message.get("payload", {})

        print(f"\n Odebrano wiadomość z tematu: {msg.topic}")
        print(f" Typ zdarzenia: {event_type}")
        print("Ładunek:")

        for key, value in payload.items():
            print(f"  - {key}: {value}")
    
    except json.JSONDecodeError:
        print(f" Błąd dekodowania JSON w wiadomości z tematu {msg.topic}")
        print("Treść wiadomości:", msg.payload.decode())
    except Exception as e:
        print(f" Wystąpił błąd podczas przetwarzania wiadomości: {e}")

# Konfiguracja klienta
broker_address = "192.168.11.11"
broker_port = 1883

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message
client.on_subscribe = on_subscribe
client.username_pw_set("user", "user")
# Połączenie z brokerem
client.connect(broker_address, broker_port)

# Start nasłuchiwania
print("Nasłuchiwanie wiadomości MQTT...")
try:
    client.loop_forever()
    
except KeyboardInterrupt:
    print(" Zatrzymano nasłuchiwanie.")
