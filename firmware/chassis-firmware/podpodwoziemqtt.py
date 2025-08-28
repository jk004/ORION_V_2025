import pygame
import tkinter as tk
from tkinter import ttk, scrolledtext
import paho.mqtt.client as mqtt
import json
import math

# --- Inicjalizacja pygame i joysticków ---
pygame.init()
pygame.joystick.init()
joysticks = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
for joy in joysticks:
    joy.init()

# --- Stan blokady kół ---
wheel_locked = [False, False, False, False]

# --- Zmienne do sterowania awaryjnego z klawiatury ---
manual_left = 0.0
manual_right = 0.0
manual_speed_factor = 0.5  # startowo 50%

# --- Kolory ---
BG_COLOR = "#1e1e1e"
FG_COLOR = "#ffffff"
GAUGE_BG = "#555555"

# --- Tkinter GUI ---
root = tk.Tk()
root.title("Joystick Axes Viewer + MQTT")
root.configure(bg=BG_COLOR)
root.geometry("1100x600")

# --- Layout ---
main_frame = tk.Frame(root, bg=BG_COLOR)
main_frame.pack(fill="both", expand=True)

left_frame = tk.Frame(main_frame, bg=BG_COLOR)
left_frame.pack(side="left", fill="y", padx=10, pady=10)

center_frame = tk.Frame(main_frame, bg=BG_COLOR)
center_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)

right_frame = tk.Frame(main_frame, bg=BG_COLOR)
right_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)

# --- Joystick info ---
axis_labels = []
if joysticks:
    for i, joystick in enumerate(joysticks):
        frame = tk.LabelFrame(left_frame, text=f"Joystick {i}: {joystick.get_name()}",
                              bg=BG_COLOR, fg=FG_COLOR, font=("Arial", 10, "bold"))
        frame.pack(fill="x", padx=5, pady=5)
        labels = []
        for axis_index in range(joystick.get_numaxes()):
            label = tk.Label(frame, text=f"Axis {axis_index}: 0.000",
                             bg=BG_COLOR, fg=FG_COLOR, anchor="w")
            label.pack(anchor="w")
            labels.append(label)
        axis_labels.append(labels)
else:
    info = tk.Label(left_frame, text="Brak joysticków\nUżyj klawiatury (W/S, I/K, +/-)",
                    bg=BG_COLOR, fg=FG_COLOR, font=("Arial", 12), justify="left")
    info.pack(pady=10)

# --- Wheel labels and gauges ---
wheel_names = ["Front Left", "Front Right", "Rear Left", "Rear Right"]
positions = [(0, 0), (0, 1), (1, 0), (1, 1)]

gauge_canvases = []
wheel_speed_labels = []

gauge_frame = tk.LabelFrame(center_frame, text="Wheel Speeds", bg=BG_COLOR, fg=FG_COLOR)
gauge_frame.pack(pady=10)

for i, name in enumerate(wheel_names):
    container = tk.Frame(gauge_frame, bg=BG_COLOR)
    container.grid(row=positions[i][0], column=positions[i][1], padx=15, pady=15)

    label = tk.Label(container, text=name, bg=BG_COLOR, fg=FG_COLOR, font=("Arial", 10, "bold"))
    label.pack()

    canvas = tk.Canvas(container, width=400, height=130, bg=BG_COLOR, highlightthickness=0)
    canvas.pack()
    gauge_canvases.append(canvas)

    speed_label = tk.Label(container, text="0", bg=BG_COLOR, fg=FG_COLOR, font=("Arial", 15))
    speed_label.pack()
    wheel_speed_labels.append(speed_label)

# --- Lock buttons ---
lock_frame = tk.LabelFrame(center_frame, text="Wheel Lock Controls", bg=BG_COLOR, fg=FG_COLOR)
lock_frame.pack(pady=10)

button_frame = tk.Frame(lock_frame, bg=BG_COLOR)
button_frame.pack()

buttons = []
def toggle_wheel_lock(index):
    wheel_locked[index] = not wheel_locked[index]
    if wheel_locked[index]:
        buttons[index].config(text=f"{wheel_names[index]} - LOCKED", bg="red", fg="white")
    else:
        buttons[index].config(text=f"{wheel_names[index]} - UNLOCK", bg="SystemButtonFace", fg="black")

for i, name in enumerate(wheel_names):
    btn = tk.Button(button_frame, text=f"{name} - UNLOCK", width=20,
                    command=lambda i=i: toggle_wheel_lock(i))
    buttons.append(btn)

buttons[0].grid(row=0, column=0, padx=10, pady=10)
buttons[1].grid(row=0, column=1, padx=10, pady=10)
buttons[2].grid(row=1, column=0, padx=10, pady=10)
buttons[3].grid(row=1, column=1, padx=10, pady=10)

# --- MQTT Section (prawa strona) ---

# Status komunikacji MQTT - Label na górze
mqtt_status_var = tk.StringVar(value="Status: Nie połączono")
mqtt_status_label = tk.Label(right_frame, textvariable=mqtt_status_var,
                             bg=BG_COLOR, fg=FG_COLOR, font=("Arial", 12, "bold"), anchor="w")
mqtt_status_label.pack(fill="x", padx=5, pady=(5, 5))



# Konsola komunikacji MQTT - scrolled text na dole
mqtt_console = scrolledtext.ScrolledText(right_frame, bg="#222222", fg="#00ff00",
                                         font=("Consolas", 10), height=10)
mqtt_console.pack(fill="both", expand=True, padx=5, pady=(0,10))

def mqtt_console_log(msg: str):
    mqtt_console.insert(tk.END, msg + "\n")
    mqtt_console.see(tk.END)

# --- Funkcje rysowania gauge ---
def draw_gauge(canvas, value, dynamic_max):
    canvas.delete("all")

    center_x, center_y = 90, 110
    radius = 70

    full_range = 255  # stała skala
    clamped = max(min(value, full_range), -full_range)

    # Zakres kąta 180° → -90° do +90°
    angle_range = 90  # dla jednej strony

    if full_range == 0:
        return

    extent = int(abs(clamped) / full_range * 90)

    # Kolor wskaźnika
    if clamped > 0:
        if clamped <= full_range * 0.4:
            color = "#00ff00"
        elif clamped <= full_range * 0.8:
            color = "#ffff00"
        else:
            color = "#ff3333"
    elif clamped < 0:
        color = "#3399ff"
    else:
        color = ""

    # Tło łuku
    canvas.create_arc(center_x - radius, center_y - radius,
                      center_x + radius, center_y + radius,
                      start=0, extent=180, style="arc",
                      outline=GAUGE_BG, width=12)

    # Wskaźnik
    if clamped > 0:
        canvas.create_arc(center_x - radius, center_y - radius,
                          center_x + radius, center_y + radius,
                          start=90, extent=extent,
                          style="arc", outline=color, width=12)
    elif clamped < 0:
        canvas.create_arc(center_x - radius, center_y - radius,
                          center_x + radius, center_y + radius,
                          start=90 - extent, extent=extent,
                          style="arc", outline=color, width=12)

    # Linia środkowa (zero)
    canvas.create_line(center_x, center_y,
                       center_x, center_y - radius,
                       fill="#888888", width=4)

    # --- Przerywane dynamiczne linie po łuku

    def draw_dynamic_line(angle_deg, label_text, color):
        rad = math.radians(angle_deg)
        x = center_x + radius * math.sin(rad)
        y = center_y - radius * math.cos(rad)
        canvas.create_line(center_x, center_y, x, y,
                           fill=color, width=2, dash=(4, 2))
        #canvas.create_text(x, y, text=label_text, fill="#888888", font=("Arial", 8))

    if dynamic_max > 0:
        angle_offset = (dynamic_max / full_range) * angle_range

        # Lewa strona (ujemna wartość)
        draw_dynamic_line( - angle_offset, f"-{dynamic_max}", "#4444aa")

        # Prawa strona (dodatnia wartość)
        draw_dynamic_line(+ angle_offset, f"+{dynamic_max}", "#aa4444")

# --- Wyliczanie prędkości kół ---
def calculate_wheel_speeds():
    global manual_left, manual_right, manual_speed_factor

    left_value = 0.0
    right_value = 0.0
    speed_factor = 0.5

    if joysticks:
        try:
            tflight = None
            logitech = None

            # Przechodzimy przez wszystkie joysticki i identyfikujemy je po nazwie
            for joystick in joysticks:
                if "T.Flight Hotas X" in joystick.get_name():
                    tflight = joystick
                elif "Logitech Extreme 3D" in joystick.get_name():
                    logitech = joystick

            if tflight and tflight.get_numaxes() > 2:
                right_value = -tflight.get_axis(1)  # Oś 1: sterowanie prawą stroną
                speed_factor = (-tflight.get_axis(2) + 1) / 2  # Oś 2: suwak prędkości

            if logitech and logitech.get_numaxes() > 1:
                left_value = -logitech.get_axis(1)  # Oś 1: lewa strona

        except Exception as e:
            mqtt_console_log(f"❗ Błąd odczytu joysticków: {e}")
    else:
        left_value = manual_left
        right_value = manual_right
        speed_factor = manual_speed_factor

    max_speed = 255
    left_speed = int(left_value * speed_factor * max_speed)
    right_speed = int(right_value * speed_factor * max_speed)

    wheel_speeds = [
        left_speed,   # Front Left
        right_speed,  # Front Right
        left_speed,   # Rear Left
        right_speed   # Rear Right
    ]

    for i in range(4):
        if wheel_locked[i]:
            wheel_speeds[i] = 0

    dynamic_max = int(speed_factor * max_speed)
    return wheel_speeds, dynamic_max



# --- Dodaj nowe zmienne do wskaźnika mocy ---
power_indicator_canvas = None
power_indicator_line = None

def create_power_indicator():
    global power_indicator_canvas, power_indicator_line

    # Utwórz nowy canvas dla wskaźnika mocy
    power_indicator_frame = tk.Frame(center_frame, bg="#313131")  # Szare tło
    power_indicator_frame.pack(pady=10)

    power_indicator_canvas = tk.Canvas(power_indicator_frame, width=400, height=30, bg=GAUGE_BG)
    power_indicator_canvas.pack()

    # Dodaj linię środkową (zero) w kolorze szarym
    power_indicator_canvas.create_line(0, 15, 400, 15, fill="#CFCFCF", width=2)
    # Dodaj białą linię w środku
    power_indicator_canvas.create_line(200, 5, 200, 25, fill="light gray", width=10)  # Pozycja środkowa

def draw_power_indicator(left_value, right_value):
    global power_indicator_line

    # Oblicz różnicę i przesuń wskaźnik
    total_width = 400
    center_x = total_width / 2

    # Oblicz moc lewą i prawą
    left_power = (left_value + 1) / 2 * total_width  # Przeskaluj do szerokości
    right_power = (right_value + 1) / 2 * total_width  # Przeskaluj do szerokości

    # Oblicz pozycję wskaźnika
    indicator_position = center_x - (right_power - left_power)

    # Ustal kolor wskaźnika na podstawie wychylenia
    if abs(right_power - left_power) < 50:  # Blisko środka
        color = "light green"
    elif abs(right_power - left_power) < 150:  # Umiarkowane wychylenie
        color = "yellow"
    else:  # Duże wychylenie
        color = "red"

    # Rysuj wskaźnik
    if power_indicator_line:
        power_indicator_canvas.delete(power_indicator_line)

    power_indicator_line = power_indicator_canvas.create_line(indicator_position, 5, indicator_position, 25, fill=color, width=8)

# --- Add this function to publish wheel speeds ---
def publish_wheel_speeds(wheel_speeds):
    payload = {
        "eventType": "chassis",
        "mode": "pwm",
        "payload": {
            "fl": wheel_speeds[0],  # Front Left
            "fr": wheel_speeds[1],  # Front Right
            "rl": wheel_speeds[2],  # Rear Left
            "rr": wheel_speeds[3]   # Rear Right
        }
    }
    try:
        client.publish("orion/topic/chassis/controller/inbound", json.dumps(payload))
        mqtt_console_log(f"📤 Wysłano: {payload}")
    except Exception as e:
        mqtt_console_log(f"❗ Błąd podczas wysyłania wiadomości: {e}")

# --- Modify the update_axes function to call publish_wheel_speeds ---
def update_axes():
    pygame.event.pump()

    if joysticks:
        for i, joystick in enumerate(joysticks):
            for j in range(joystick.get_numaxes()):
                value = joystick.get_axis(j)
                axis_labels[i][j].config(text=f"Axis {j}: {value:.3f}")

    wheel_speeds, dynamic_max = calculate_wheel_speeds()
    for i, speed in enumerate(wheel_speeds):
        wheel_speed_labels[i].config(text=f"{speed}")
        draw_gauge(gauge_canvases[i], speed, dynamic_max)

    # Send the wheel speeds via MQTT
    publish_wheel_speeds(wheel_speeds)

    # Draw power indicator
    left_value = wheel_speeds[0] / 255  # Rescale to range -1 to 1
    right_value = wheel_speeds[1] / 255  # Rescale to range -1 to 1
    draw_power_indicator(left_value, right_value)

    root.after(50, update_axes)

# --- Wywołaj funkcję do utworzenia wskaźnika mocy ---
create_power_indicator()
# --- Obsługa klawiatury ---
def on_key_press(event):
    global manual_left, manual_right, manual_speed_factor

    key = event.keysym.lower()
    if key == 'w':
        manual_left = 1.0
    elif key == 's':
        manual_left = -1.0
    elif key == 'i':
        manual_right = 1.0
    elif key == 'k':
        manual_right = -1.0
    elif key == 'plus' or key == 'equal':
        manual_speed_factor = min(1.0, manual_speed_factor + 0.1)
    elif key == 'minus':
        manual_speed_factor = max(0.0, manual_speed_factor - 0.1)

def on_key_release(event):
    global manual_left, manual_right

    key = event.keysym.lower()
    if key in ['w', 's']:
        manual_left = 0.0
    elif key in ['i', 'k']:
        manual_right = 0.0

# --- MQTT callbacks ---

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        msg = f"✅ Połączono z brokerem MQTT (kod: {rc})"
        mqtt_status_var.set("Status: Połączono")
        mqtt_console_log(msg)
        # Subskrybuj temat
        client.subscribe("orion/topic/chassis/controller/inbound")
    else:
        mqtt_status_var.set(f"Status: Błąd połączenia (kod: {rc})")
        mqtt_console_log(f"❌ Nie udało się połączyć (kod: {rc})")

def on_subscribe(client, userdata, mid, granted_qos):
    mqtt_console_log(f"🔔 Subskrypcja potwierdzona (mid={mid})")

def on_disconnect(client, userdata, rc):
    if rc != 0:
        mqtt_console_log("⚠️ Nieoczekiwane rozłączenie.")
    mqtt_status_var.set("Status: Rozłączono")

def on_message(client, userdata, msg):
    try:
        payload_str = msg.payload.decode()
        message = json.loads(payload_str)

        event_type = message.get("eventType", "Brak eventType")
        payload = message.get("payload", {})

        mqtt_console_log(f"\n📥 Wiadomość z tematu: {msg.topic}")
        mqtt_console_log(f"  Typ zdarzenia: {event_type}")
        mqtt_console_log(f"  Ładunek:")
        for key, value in payload.items():
            mqtt_console_log(f"    - {key}: {value}")

    except json.JSONDecodeError:
        mqtt_console_log(f"❗ Błąd dekodowania JSON w wiadomości z tematu {msg.topic}")
        mqtt_console_log(f"  Treść wiadomości: {msg.payload.decode()}")
    except Exception as e:
        mqtt_console_log(f"❗ Błąd podczas przetwarzania wiadomości: {e}")

# --- Konfiguracja klienta MQTT ---
broker_address = "192.168.11.11"
broker_port = 1883

client = mqtt.Client()
client.username_pw_set("user", "user")

client.on_connect = on_connect
client.on_message = on_message
client.on_subscribe = on_subscribe
client.on_disconnect = on_disconnect

try:
    client.connect(broker_address, broker_port)
except Exception as e:
    mqtt_status_var.set("Status: Błąd połączenia")
    mqtt_console_log(f"❌ Nie udało się połączyć z brokerem MQTT: {e}")

# --- Funkcja integrująca pętlę MQTT z Tkinter ---
def mqtt_loop():
    client.loop(timeout=0.1)
    root.after(100, mqtt_loop)

# --- Bindy i start GUI ---
root.bind("<KeyPress>", on_key_press)
root.bind("<KeyRelease>", on_key_release)

update_axes()
mqtt_loop()
root.mainloop()
