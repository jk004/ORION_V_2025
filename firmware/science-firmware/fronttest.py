import tkinter as tk
import csv
from tkinter import ttk, scrolledtext, messagebox, filedialog
import paho.mqtt.client as mqtt
import json
import queue
from datetime import datetime
import io
import base64
from PIL import Image, ImageTk

# -------------------------
# Konfiguracja brokera MQTT
# -------------------------
broker_address = "192.168.11.11"
broker_port = 1883
username = "user"
password = "user"
topic_inbound = "orion/topic/science/inbound"
topic_outbound = "orion/topic/science/outbound"

# -------------------------
# Kolejka i pamięć ostatniej wiadomości
# -------------------------
msg_queue = queue.Queue(maxsize=200)
last_message = {"raw": None, "summary": None, "timestamp": None}

# -------------------------
# Helpery JSON / formatowanie
# -------------------------
def compact_json(obj):
    return json.dumps(obj, separators=(",", ":"))

def summarize_message(msgobj):
    eventType = msgobj.get("eventType", "unknown")
    payload = msgobj.get("payload", {})

    summary = {"eventType": eventType}
    if eventType == "science":
        if all(k in payload for k in ("FbDrillA", "FbDrillB", "FbElevatorA", "FbElevatorB")):
            summary["type"] = "telemetry"
            summary.update({k: payload.get(k) for k in ("FbDrillA","FbDrillB","FbElevatorA","FbElevatorB")})
        elif "number" in payload and "mass" in payload and "gasses" in payload and "lights" in payload:
            summary["type"] = "sample"
            summary["number"] = payload.get("number")
            summary["mass"] = payload.get("mass")
            summary["temp"] = payload.get("temp")
            summary["gasses_len"] = len(payload.get("gasses", [])) if isinstance(payload.get("gasses", []), list) else None
            summary["lights_len"] = len(payload.get("lights", [])) if isinstance(payload.get("lights", []), list) else None
        else:
            summary["type"] = "unknown_payload"
            summary["keys"] = list(payload.keys())
    else:
        summary["type"] = "other"
        summary["keys"] = list(payload.keys())

    return summary

def is_sample_message(payload_obj):
    if not isinstance(payload_obj, dict):
        return False
    if payload_obj.get("eventType") != "science":
        return False
    payload = payload_obj.get("payload", {})
    return ("number" in payload) and ("lights" in payload) and ("gasses" in payload)

# -------------------------
# MQTT callbacks
# -------------------------
def on_connect(client, userdata, flags, rc):
    print("✅ Połączono z brokerem MQTT (kod:", rc, ")")
    client.subscribe(topic_outbound)
    client.subscribe(topic_inbound)
    print("Subskrypcje: ", topic_outbound, topic_inbound)

def on_subscribe(client, userdata, mid, granted_qos):
    print("Subscribed:", mid, granted_qos)

def on_message(client, userdata, msg):
    try:
        payload_str = msg.payload.decode()
        parsed = json.loads(payload_str)
        entry = {
            "topic": msg.topic,
            "payload_raw": payload_str,
            "payload_obj": parsed,
            "recv_time": datetime.utcnow().isoformat() + "Z"
        }
        try:
            msg_queue.put_nowait(entry)
        except queue.Full:
            try:
                _ = msg_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                msg_queue.put_nowait(entry)
            except queue.Full:
                pass
    except json.JSONDecodeError:
        entry = {
            "topic": msg.topic,
            "payload_raw": msg.payload.decode(errors="replace"),
            "payload_obj": None,
            "recv_time": datetime.utcnow().isoformat() + "Z"
        }
        try:
            msg_queue.put_nowait(entry)
        except queue.Full:
            pass
    except Exception as e:
        print("Błąd w on_message:", e)

# -------------------------
# Inicjalizacja klienta MQTT
# -------------------------
client = mqtt.Client()
client.username_pw_set(username, password)
client.on_connect = on_connect
client.on_message = on_message
client.on_subscribe = on_subscribe

client.connect(broker_address, broker_port, keepalive=60)
client.loop_start()

# -------------------------
# Funkcja wysyłająca komendę zgodnie z dokumentacją
# -------------------------
def publish_science_command(drill=0, elev=0, conv=0, res_seq=0, rotate = 0, reset = 0):
    cmd = {
        "eventType": "science",
        "payload": {
            "drill": int(drill),
            "elev": int(elev),
            "conv": int(conv),
            "res_seq": int(res_seq),
            "rotate": int(rotate),
            "reset": int(reset)
        }
    }
    payload = compact_json(cmd)
    client.publish(topic_inbound, payload)
    print("[MQTT PUB]", topic_inbound, payload)

# -------------------------
# GUI
# -------------------------
root = tk.Tk()
root.title("Sterowanie modułem science")
root.geometry("1100x700")
root.configure(bg="#2a2a2a")
root.resizable(True, True)

style = ttk.Style()
style.theme_use("clam")
style.configure("TButton", font=("Arial", 11), padding=6)
style.configure("TFrame", background="#2a2a2a")
style.configure("TLabelframe", background="#2a2a2a", foreground="white")
style.configure("TLabelframe.Label", background="#2a2a2a", foreground="white")



# ---------- Zamknięcie i cleanup ----------
def on_close():
    print("Zamykanie aplikacji... rozłączanie MQTT")
    try:
        client.loop_stop()
        client.disconnect()
    except Exception as e:
        print("Błąd przy zamykaniu MQTT:", e)
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_close)
root.bind("<Escape>", lambda e: on_close())
# -----------------------------------
# Kamera sieciowa - wyświetlanie obrazu
# -----------------------------------
# Wymagane biblioteki: requests, pillow
# pip install requests pillow
import threading
import requests
import io
import base64
from PIL import Image, ImageTk

CAM_URL = "http://192.168.11.11:8081/"  # Twój URL kamery
CAM_WIDTH = 640
CAM_HEIGHT = 480

# Frame i label na obraz kamery — umieszczamy na środku strony, nad tabelką
camera_frame = ttk.LabelFrame(root, text="Podgląd kamery", padding=2, style="TLabelframe")
camera_frame.place(relx=0.5, rely=0.05, anchor='n', width=CAM_WIDTH, height=CAM_HEIGHT)

camera_label = tk.Label(camera_frame, bg="black")
camera_label.pack(fill="both", expand=True)

# zmienna do przechowywania ostatniego PhotoImage (trzeba referencję utrzymać)
camera_label._photo = None

# Flaga stopu wątku
_camera_thread_stop = False

def _safe_update_image(pil_image):
    """Aktualizuj obraz w labelu z obiektu PIL.Image."""
    try:
        # dopasuj rozmiar zachowując proporcje
        pil_image.thumbnail((CAM_WIDTH, CAM_HEIGHT), Image.LANCZOS)
        photo = ImageTk.PhotoImage(pil_image)
        camera_label.config(image=photo)
        camera_label._photo = photo  # utrzymujemy referencję
    except Exception as e:
        print("Błąd podczas aktualizacji obrazu w GUI:", e)

def _process_json_frame(jobj):
    """Szukamy pola z base64 (image/frame/jpeg/data) i zwracamy PIL.Image jeśli znajdziemy."""
    for key in ("image", "frame", "jpeg", "data", "snapshot"):
        if key in jobj:
            b64 = jobj[key]
            # jeśli to obiekt z URL -> spróbuj pobrać
            if isinstance(b64, str) and b64.startswith("http"):
                try:
                    r = requests.get(b64, timeout=5)
                    r.raise_for_status()
                    return Image.open(io.BytesIO(r.content)).convert("RGB")
                except Exception:
                    return None
            # jeśli to base64 -> dekoduj
            try:
                raw = base64.b64decode(b64)
                return Image.open(io.BytesIO(raw)).convert("RGB")
            except Exception:
                return None
    return None

def camera_worker():
    """Wątek próbujący różnych trybów: MJPEG -> JSON -> pojedyncze JPEG."""
    global _camera_thread_stop
    session = requests.Session()
    try:
        r = session.get(CAM_URL, stream=True, timeout=5)
    except Exception as e:
        print("Nie udało się połączyć z kamerą (pierwsze żądanie):", e)
        r = None

    # Jeśli odpowiedź istnieje i jest multipart -> traktujemy jako MJPEG
    if r is not None:
        ctype = r.headers.get("Content-Type", "")
        if "multipart" in ctype and "mixed" in ctype or "x-mixed-replace" in ctype:
            print("Kamera: wykryto MJPEG stream, parsuję klatki...")
            boundary = None
            # spróbuj odczytać boundary
            if "boundary=" in ctype:
                boundary = ctype.split("boundary=")[-1].strip()
            buffer = b""
            try:
                for chunk in r.iter_content(chunk_size=1024):
                    if _camera_thread_stop:
                        break
                    if not chunk:
                        continue
                    buffer += chunk
                    # znajdź JPEG start/end
                    start = buffer.find(b'\xff\xd8')
                    end = buffer.find(b'\xff\xd9')
                    if start != -1 and end != -1 and end > start:
                        jpg = buffer[start:end+2]
                        buffer = buffer[end+2:]
                        try:
                            pil = Image.open(io.BytesIO(jpg)).convert("RGB")
                            # aktualizacja w wątku GUI przez after
                            root.after(0, _safe_update_image, pil)
                        except Exception:
                            pass
            except Exception as e:
                print("Błąd podczas parsowania MJPEG:", e)
            finally:
                try:
                    r.close()
                except:
                    pass
            return

    # Jeśli nie MJPEG - spróbuj jako JSON endpoint (dynamic json)
    try:
        r = session.get(CAM_URL, timeout=5)
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "")
        if "application/json" in ct or r.text.strip().startswith("{"):
            try:
                jobj = r.json()
                pil = _process_json_frame(jobj)
                if pil is not None:
                    root.after(0, _safe_update_image, pil)
                    # a następnie w pętli odpytywać co 0.5s
                    while not _camera_thread_stop:
                        try:
                            r2 = session.get(CAM_URL, timeout=5)
                            r2.raise_for_status()
                            jobj = r2.json()
                            pil = _process_json_frame(jobj)
                            if pil is not None:
                                root.after(0, _safe_update_image, pil)
                        except Exception:
                            # jeśli w kolejnych próbach błąd, przerwij i spróbuj fetching obrazu
                            break
                    return
            except Exception as e:
                # nie JSON albo nie zawiera obrazu
                pass
    except Exception:
        pass

    # Na koniec: próbuj traktować URL jako pojedynczy JPEG, odpytywany cyklicznie
    print("Kamera: traktuję URL jako obraz pojedynczy lub snapshot - odpytywanie cykliczne")
    while not _camera_thread_stop:
        try:
            r = session.get(CAM_URL, timeout=5)
            r.raise_for_status()
            content = r.content
            try:
                pil = Image.open(io.BytesIO(content)).convert("RGB")
                root.after(0, _safe_update_image, pil)
            except Exception:
                # spróbuj jeśli odpowiedź była JSON z url do kafli
                try:
                    jobj = r.json()
                    pil = _process_json_frame(jobj)
                    if pil is not None:
                        root.after(0, _safe_update_image, pil)
                except Exception:
                    pass
        except Exception as e:
            # błąd połączenia — ignoruj, spróbuj ponownie po chwili
            # (nie spamujemy terminala)
            pass
        # odśwież co ~0.7s
        for _ in range(7):
            if _camera_thread_stop:
                break
            # krótka pauza bez blokowania GUI
            import time
            time.sleep(0.1)
def save_high_quality_image():
    """Pobierz obraz z kamery i zapisz go w wyższej jakości."""
    try:
        r = requests.get(CAM_URL, timeout=5)
        r.raise_for_status()
        content = r.content
        # Zapisz obraz jako plik JPG
        file_path = filedialog.asksaveasfilename(defaultextension=".jpg",
                                                   filetypes=[("JPEG files", "*.jpg"), ("All files", "*.*")])
        if file_path:
            with open(file_path, 'wb') as f:
                f.write(content)
            print(f"Zdjęcie zapisane jako: {file_path}")
    except Exception as e:
        print("Błąd podczas pobierania lub zapisywania zdjęcia:", e)
        
# Start wątku kamery
_camera_thread = threading.Thread(target=camera_worker, daemon=True)
_camera_thread.start()

# przy zamykaniu aplikacji ustaw flagę
def _stop_camera_thread():
    global _camera_thread_stop
    _camera_thread_stop = True

# dopisz wywołanie stopu podczas zamknięcia — jeśli masz już on_close(), w nim dołącz:
#    _stop_camera_thread()
# jeśli nie — możesz dodać poniższe do istniejącej funkcji on_close()
try:
    # jeśli istnieje on_close, owrapuj call tak, żeby wywoływało także _stop_camera_thread
    _orig_on_close = on_close
    def on_close_with_camera():
        _stop_camera_thread()
        _orig_on_close()
    # zamień globalnie
    on_close = on_close_with_camera
    root.protocol("WM_DELETE_WINDOW", on_close)
except NameError:
    # jeśli on_close nie istnieje w tym momencie, po prostu zarejestruj _stop_camera_thread przy zamknięciu
    root.protocol("WM_DELETE_WINDOW", lambda: (_stop_camera_thread(), root.destroy()))

# Zmienna do przechowywania ostatniego obrazu PIL
last_pil_image = None

def _safe_update_image(pil_image):
    """Aktualizuj obraz w labelu z obiektu PIL.Image."""
    global last_pil_image  # Użyj globalnej zmiennej
    try:
        # dopasuj rozmiar zachowując proporcje
        pil_image.thumbnail((CAM_WIDTH, CAM_HEIGHT), Image.LANCZOS)
        photo = ImageTk.PhotoImage(pil_image)
        camera_label.config(image=photo)
        camera_label._photo = photo  # utrzymujemy referencję
        last_pil_image = pil_image  # Przechowuj ostatni obraz PIL
    except Exception as e:
        print("Błąd podczas aktualizacji obrazu w GUI:", e)

def save_image(pil_image):
    """Zapisz obraz jako plik JPG."""
    file_path = filedialog.asksaveasfilename(defaultextension=".jpg",
                                               filetypes=[("JPEG files", "*.jpg"), ("All files", "*.*")])
    if file_path:
        try:
            pil_image.save(file_path, "JPEG")
            print(f"Zdjęcie zapisane jako: {file_path}")
        except Exception as e:
            print("Błąd podczas zapisywania zdjęcia:", e)

def take_picture():
    """Funkcja do robienia zdjęcia."""
    global last_pil_image
    if last_pil_image is not None:
        save_image(last_pil_image)


# ---------- TABELA: bottom center - pokazuje najnowszy JSON w formie drzewa ----------
table_frame = ttk.LabelFrame(root, text="Dane badawcze", padding=8, style="TLabelframe")
table_frame.place(relx=0.5, rely=0.88, anchor='s', width=320, height=160)

# Dodanie przycisku do robienia zdjęcia
take_picture_button = tk.Button(root, text="Zrób zdjęcie", command=take_picture)
take_picture_button.place(relx=0.5, rely=0.01, anchor='n')
# ---------- LEWY PANEL: pokaż tylko pełny sample JSON (po sekwencji) ----------
# ZWĘŻONY terminal po lewej
LEFT_WIDTH = 280
LEFT_HEIGHT = 520
left_frame = ttk.LabelFrame(root, text="Odebrane - pełny output z sekwencji (surowy JSON)", padding=8, style="TLabelframe")
left_frame.place(relx=0.0, rely=0.0, anchor='nw', x=12, y=12, width=LEFT_WIDTH, height=LEFT_HEIGHT)

scrolled = scrolledtext.ScrolledText(left_frame, wrap=tk.NONE, height=20, width=40, bg="#111", fg="#dcdcdc")
scrolled.pack(fill="both", expand=True)

# Skrócony widok poniżej lewego terminala
summary_frame = ttk.LabelFrame(root, text="Skrócony widok (ostatnia wiadomość)", padding=8, style="TLabelframe")
summary_frame.place(relx=0.0, rely=0.0, anchor='nw', x=12, y=12 + LEFT_HEIGHT + 8, width=LEFT_WIDTH, height=150)

summary_text = tk.Text(summary_frame, height=6, width=40, bg="#111", fg="#aad", state="disabled")
summary_text.pack(fill="both", expand=True)

# ---------- PRAWY PANEL: sterowanie modułem science (do prawego-dolnego rogu) ----------
# frame_sterowanie i frame_karuzela będą wyjustowane do prawego dolnego rogu (stackowane)
frame_sterowanie = ttk.LabelFrame(root, text="Sterowanie modułem science", padding=12, style="TLabelframe")
# umieść tuż nad dolnym marginesem, wyjustowane do prawej
frame_sterowanie.place(relx=1.0, rely=1.0, anchor='se', x=-20, y=-20)

# Karuzela umieszczona powyżej sterowania, ta sama prawa krawędź (wyjustowana)
frame_karuzela = ttk.LabelFrame(root, text="KaruzelaTrim", padding=10, style="TLabelframe")
# ustawiamy ją powyżej frame_sterowanie; wysokość sterowania zależy od zawartości, więc dajemy offset 220
frame_karuzela.place(relx=1.0, rely=0.0, anchor='ne', x=-20, y=20)

def karuzela_cmd(action):
    print(f"[KARUZELA] {action}")

trimer_frame = ttk.Frame(frame_karuzela, style="TFrame")
trimer_frame.pack(side="bottom", pady=5, padx = 5)

def confirm_and_reset():
    if messagebox.askyesno("Potwierdzenie", "Czy na pewno chcesz zrestartować karuzelę?"):
        publish_science_command(drill=0, elev=0, conv=0, res_seq=0, rotate=0, reset=1)

tk.Button(
    frame_karuzela,
    text="Reset",
    bg="#C85D5D",
    fg="white",
    width=5,
    height=1,
    anchor="n",
    command=confirm_and_reset
).pack(side="left", padx=10)

# Suwak do prędkości podnoszenia
trim_scale = tk.Scale(frame_karuzela, from_=0, to=2666, orient="horizontal", label="Prędkość", bg="#2a2a2a", fg="white")
trim_scale.pack(fill="x", pady=3)


# tk.Button(frame_karuzela, text="⟲", bg="#555555", fg="white", width=6, height=2,
#           command=lambda: karuzela_cmd("obrot_lewo")).pack(side="left", padx=3)

tk.Button(frame_karuzela, text="⟲ trim", bg="#555555", fg="white", width=10, height=2,
          command=lambda: publish_science_command(drill=0, elev=0, conv=0, res_seq=0,rotate=16000-trim_scale.get(),reset = 0)).pack(side="left", padx=5)
tk.Button(frame_karuzela, text="⟳ trim", bg="#555555", fg="white", width=10, height=2,
    command=lambda: publish_science_command(drill=0, elev=0, conv=0, res_seq=0,rotate=trim_scale.get(),reset = 0)).pack(side="left", padx=5)


# Control panel inside frame_sterowanie
# Taśmociąg (conveyor -> conv: -1,0,1)
frame_tasmociag = ttk.Frame(frame_sterowanie, style="TFrame")
frame_tasmociag.pack(side="left", anchor="w", pady=(0,12))

taśmociąg_label = tk.Label(frame_tasmociag, text="Taśmociąg", bg="#2a2a2a", fg="white", font=("Arial", 11, "bold"))
taśmociąg_label.pack(side="top", anchor="s", pady=(0, 6))

tk.Button(frame_tasmociag, text="⟲", bg="#555555", fg="white", width=6, height=2,
          command=lambda: publish_science_command(drill=0, elev=0, conv=-1, res_seq=0, rotate=0, reset = 0)).pack(side="left", padx=3)
tk.Button(frame_tasmociag, text="STOP", bg="#d9534f", fg="white", width=6, height=2,
          command=lambda: publish_science_command(drill=0, elev=0, conv=0, res_seq=0, rotate=0, reset = 0)).pack(side="left", padx=3)
tk.Button(frame_tasmociag, text="⟳", bg="#555555", fg="white", width=6, height=2,
          command=lambda: publish_science_command(drill=0, elev=0, conv=1, res_seq=0, rotate=0, reset = 0)).pack(side="left", padx=3)

# Separator
separator = tk.Frame(frame_sterowanie, width=2, bg="grey")
separator.pack(side="left", fill="y", padx=10, pady=6)

# Podnoszenie + Wiertło
frame_prawy = ttk.Frame(frame_sterowanie, style="TFrame")
frame_prawy.pack(side="left", padx=5)

# Podnoszenie
podnoszenie_label = tk.Label(frame_prawy, text="Podnoszenie", bg="#2a2a2a", fg="white", font=("Arial", 11, "bold"))
podnoszenie_label.pack(anchor="w", pady=(0, 4))
frame_podnoszenie = ttk.Frame(frame_prawy, style="TFrame")
frame_podnoszenie.pack(pady=2)

# Suwak do prędkości podnoszenia
elev_scale = tk.Scale(frame_podnoszenie, from_=0, to=255, orient="horizontal", label="Prędkość", bg="#2a2a2a", fg="white")
elev_scale.pack(fill="x", pady=3)

tk.Button(frame_podnoszenie, text="⬆️", bg="#555555", fg="white", width=6, height=2,
          command=lambda: publish_science_command(drill=0, elev=elev_scale.get(), conv=0, res_seq=0, rotate=0, reset=0)).pack(fill="x", pady=1)
tk.Button(frame_podnoszenie, text="STOP", bg="#d9534f", fg="white", width=6, height=2,
          command=lambda: publish_science_command(drill=0, elev=0, conv=0, res_seq=0, rotate=0, reset=0)).pack(fill="x", pady=1)
tk.Button(frame_podnoszenie, text="⬇️", bg="#555555", fg="white", width=6, height=2,
          command=lambda: publish_science_command(drill=0, elev=-elev_scale.get(), conv=0, res_seq=0, rotate=0, reset=0)).pack(fill="x", pady=1)

# Wiertło
wiertlo_label = tk.Label(frame_prawy, text="Wiertło", bg="#2a2a2a", fg="white", font=("Arial", 11, "bold"))
wiertlo_label.pack(anchor="w", pady=(6, 0))
frame_wiertlo = ttk.Frame(frame_prawy, style="TFrame")
frame_wiertlo.pack(pady=2)

# Suwak do prędkości wiertła
drill_scale = tk.Scale(frame_wiertlo, from_=0, to=255, orient="horizontal", label="Prędkość", bg="#2a2a2a", fg="white")
drill_scale.pack(fill="x", pady=3)

tk.Button(frame_wiertlo, text="⟲", bg="#555555", fg="white", width=6, height=2,
          command=lambda: publish_science_command(drill=-drill_scale.get(), elev=0, conv=0, res_seq=0, rotate=0, reset=0)).pack(side="left", padx=3)
tk.Button(frame_wiertlo, text="STOP", bg="#d9534f", fg="white", width=6, height=2,
          command=lambda: publish_science_command(drill=0, elev=0, conv=0, res_seq=0, rotate=0, reset=0)).pack(side="left", padx=3)
tk.Button(frame_wiertlo, text="⟳", bg="#555555", fg="white", width=6, height=2,
          command=lambda: publish_science_command(drill=drill_scale.get(), elev=0, conv=0, res_seq=0, rotate=0, reset=0)).pack(side="left", padx=3)

# ---------- PRzycisk START SEKWENCJI pozostaje tu (nie ruszamy) ----------
def start_research_sequence():
    publish_science_command(drill=0, elev=0, conv=0, res_seq=1)
    print("[SEQUENCE] Uruchomiono sekwencję badawczą")

center_button = tk.Button(root, text="START SEKWENCJI BADAWCZEJ", font=("Arial", 14, "bold"),
                          bg="#2e8b57", fg="white", padx=20, pady=10, command=start_research_sequence)
# zostawiamy przy prawej krawędzi w tej samej pozycji co wcześniej
center_button.place(relx=1.0, rely=0.38, anchor='ne', x=-20)



tree = ttk.Treeview(table_frame, columns=("value",), show="tree")
tree.heading("#0", text="Pole")
tree.column("#0", width=140, anchor="w")
tree.heading("value", text="Wartość")
tree.column("value", width=140, anchor="w")
tree.pack(fill="both", expand=True)

def clear_tree():
    for iid in tree.get_children():
        tree.delete(iid)

def insert_items(parent, obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                node = tree.insert(parent, 'end', text=str(k), values=("",))
                insert_items(node, v)
            else:
                tree.insert(parent, 'end', text=str(k), values=(str(v),))
    elif isinstance(obj, list):
        for idx, v in enumerate(obj):
            label = f"[{idx}]"
            if isinstance(v, (dict, list)):
                node = tree.insert(parent, 'end', text=label, values=("",))
                insert_items(node, v)
            else:
                tree.insert(parent, 'end', text=label, values=(str(v),))
    else:
        tree.insert(parent, 'end', text=str(obj), values=("",))

def expand_all_nodes():
    for child in tree.get_children():
        tree.item(child, open=True)
        for sub in tree.get_children(child):
            tree.item(sub, open=True)

def update_table_with_message(msgobj):
    clear_tree()
    if not isinstance(msgobj, dict):
        return
    tree.insert("", 'end', text="eventType", values=(msgobj.get("eventType", ""),))
    payload = msgobj.get("payload", {})
    payload_node = tree.insert("", 'end', text="payload", values=("",))
    insert_items(payload_node, payload)
    expand_all_nodes()

def save_to_csv():
    # Open a file dialog to choose the save location and filename
    file_path = filedialog.asksaveasfilename(defaultextension=".csv",
                                               filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
    if not file_path:  # If the user cancels the dialog
        return

    with open(file_path, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(["Pole", "Wartość"])  # Header row
        for child in tree.get_children():
            write_tree_items(writer, child)

    messagebox.showinfo("Zapisano", f"Dane zostały zapisane do pliku {file_path}")

def write_tree_items(writer, parent):
    for child in tree.get_children(parent):
        text = tree.item(child, "text")
        value = tree.item(child, "values")[0]
        writer.writerow([text, value])
        write_tree_items(writer, child)

# Add the "Save to CSV" button
save_button = ttk.Button(root, text="Zapisz do CSV", command=save_to_csv)
save_button.place(relx=0.5, rely=0.95, anchor='s')

# ---------- Aktualizacja GUI: co sekundę pobierz z kolejki i pokaż ----------
def gui_update_from_queue():
    updated = False
    processed_sample = False
    while True:
        try:
            entry = msg_queue.get_nowait()
        except queue.Empty:
            break

        # Zawsze aktualizujemy skrócony widok (jeśli możliwe)
        if entry["payload_obj"] is not None:
            summary = summarize_message(entry["payload_obj"])
        else:
            summary = {"type": "invalid_json", "raw": entry.get("payload_raw")}

        last_message["raw"] = entry.get("payload_raw")
        last_message["summary"] = summary
        last_message["timestamp"] = entry.get("recv_time")
        updated = True

        # Jeśli to pełny sample -> aktualizuj tabelę i dopisz surowy JSON do lewego terminala
        if entry["payload_obj"] is not None and is_sample_message(entry["payload_obj"]):
            update_table_with_message(entry["payload_obj"])
            ts = entry.get("recv_time", datetime.utcnow().isoformat()+"Z")
            topic = entry.get("topic", "")
            raw = entry.get("payload_raw", "")
            scrolled.insert(tk.END, f"[{ts}] {topic}\n{raw}\n\n")
            scrolled.see(tk.END)
            processed_sample = True

    # Jeśli w tej iteracji nie przetworzono żadnego sample'a — czyścimy tabelę (nic nie wyświetlamy)
    if not processed_sample:
        #clear_tree()
        pass
    # Aktualizacja skróconego widoku
    if last_message["summary"] is not None:
        summary_text.config(state="normal")
        summary_text.delete("1.0", tk.END)
        s = last_message["summary"]
        summary_text.insert(tk.END, f"Czas: {last_message.get('timestamp')}\n")
        summary_text.insert(tk.END, f"Typ: {s.get('type')}\n")
        if s.get("type") == "telemetry":
            summary_text.insert(tk.END, f"FbDrillA: {s.get('FbDrillA')}\n")
            summary_text.insert(tk.END, f"FbDrillB: {s.get('FbDrillB')}\n")
            summary_text.insert(tk.END, f"FbElevatorA: {s.get('FbElevatorA')}\n")
            summary_text.insert(tk.END, f"FbElevatorB: {s.get('FbElevatorB')}\n")
        elif s.get("type") == "sample":
            summary_text.insert(tk.END, f"Sample #: {s.get('number')}\n")
            summary_text.insert(tk.END, f"Mass (g): {s.get('mass')}\n")
            summary_text.insert(tk.END, f"Temp (C): {s.get('temp')}\n")
            summary_text.insert(tk.END, f"Gasses count: {s.get('gasses_len')}\n")
            summary_text.insert(tk.END, f"Lights count: {s.get('lights_len')}\n")
        else:
            if "keys" in s:
                summary_text.insert(tk.END, f"Klucze payload: {s.get('keys')}\n")
            elif "raw" in s:
                summary_text.insert(tk.END, f"Raw (skrócone): {str(s.get('raw'))[:200]}\n")
        summary_text.config(state="disabled")

    root.after(1000, gui_update_from_queue)
root.after(1000, gui_update_from_queue)





# Uruchom GUI
root.mainloop()
