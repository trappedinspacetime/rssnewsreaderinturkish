#!/usr/bin/env python3

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import feedparser
import subprocess
import logging
import re
import threading
import io
import cairo
import time
import lxml.html
import webbrowser
import os
import requests
import configparser
import validators
import socket

# Log ayarları
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Konfigürasyon dosyası ayarları
CONFIG_DIR = os.path.expanduser("~/.config")
CONFIG_FILE = os.path.join(CONFIG_DIR, "rss.ini")
DEFAULT_RSS_URL = "https://www.gercekgundem.com/rss/"

# Ses cihazını başlatmak için boş ses çal
def initialize_audio():
    try:
        command = ["aplay", "-r", "22050", "-f", "S16_LE", "-t", "raw", "/dev/zero"]
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            logger.debug("Ses cihazı başlatma denemesi zaman aşımına uğradı, devam ediliyor.")
        except Exception as e:
            logger.error(f"aplay başlatma sürecinde hata: {e}")
    except FileNotFoundError:
        logger.error("aplay komutu bulunamadı. Lütfen aplay'in yüklü olduğundan emin olun.")
    except Exception as e:
        logger.error(f"Ses cihazı başlatma hatası: {e}")

# Metni seslendirme fonksiyonu (RAM'de işleme)
def speak_text(text):
    if not text or text.isspace() or re.fullmatch(r'[- .]*', text):
        return

    try:
        cleaned_text = re.sub(r'\s*----------\s*', '. ', text).strip()
        if len(cleaned_text) < 10:
            return

        escaped_text_for_shell = cleaned_text.replace("'", "'\"'\"'")
        quoted_text = f"'{escaped_text_for_shell}'"

        home_dir = os.path.expanduser("~")
        model_dir = os.path.join(home_dir, "piper-voices", "tr", "tr_TR", "fettah", "medium")
        os.makedirs(model_dir, exist_ok=True)

        model_files = {
            "model": {
                "url": "https://huggingface.co/rhasspy/piper-voices/raw/main/tr/tr_TR/fettah/medium/tr_TR-fettah-medium.onnx",
                "path": os.path.join(model_dir, "tr_TR-fettah-medium.onnx")
            },
            "config": {
                "url": "https://huggingface.co/rhasspy/piper-voices/raw/main/tr/tr_TR/fettah/medium/tr_TR-fettah-medium.onnx.json",
                "path": os.path.join(model_dir, "tr_TR-fettah-medium.onnx.json")
            }
        }

        for file_type, file_info in model_files.items():
            if not os.path.exists(file_info["path"]):
                logger.info(f"{file_type} dosyası indiriliyor: {file_info['url']}")
                try:
                    response = requests.get(file_info["url"], stream=True, timeout=10)
                    response.raise_for_status()
                    with open(file_info["path"], "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    logger.info(f"{file_type} dosyası indirildi: {file_info['path']}")
                except Exception as e:
                    logger.error(f"{file_type} dosyası indirilemedi: {e}")
                    return

        piper_command = (
            f"echo {quoted_text} | piper "
            f"--model {model_files['model']['path']} "
            f"--config {model_files['config']['path']} "
            f"--length-scale 0.833 "
            "--output_raw"
        )

        piper_process = subprocess.Popen(piper_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)

        try:
            audio_data, piper_stderr = piper_process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            piper_process.kill()
            logger.error("Piper komutu zaman aşımına uğradı.")
            return

        if piper_stderr:
            stderr_str = piper_stderr.decode('utf-8', errors='ignore')
            meaningful_stderr_lines = [line for line in stderr_str.splitlines() if not line.strip().startswith("Playing raw data")]
            if meaningful_stderr_lines:
                logger.error(f"Piper stderr: {'\\n'.join(meaningful_stderr_lines)}")

        if piper_process.returncode != 0:
            logger.error(f"Piper komutu başarısız, çıkış kodu: {piper_process.returncode}")
            return

        if not audio_data:
            logger.warning("Piper'dan ses verisi alınamadı.")
            return

        audio_buffer = io.BytesIO(audio_data)

        aplay_command = ["aplay", "-r", "22050", "-f", "S16_LE", "-t", "raw", "-"]
        aplay_process = subprocess.Popen(aplay_command, stdin=subprocess.PIPE, stderr=subprocess.PIPE, text=False)

        try:
            _, aplay_stderr = aplay_process.communicate(input=audio_buffer.getvalue(), timeout=15)
        except subprocess.TimeoutExpired:
            aplay_process.kill()
            logger.error("Aplay komutu zaman aşımına uğradı.")
            return

        if aplay_stderr:
            stderr_str = aplay_stderr.decode('utf-8', errors='ignore')
            meaningful_stderr_lines = [line for line in stderr_str.splitlines() if not line.strip().startswith("Playing raw data")]
            if meaningful_stderr_lines:
                logger.error(f"Aplay stderr: {'\\n'.join(meaningful_stderr_lines)}")

        if aplay_process.returncode != 0:
            logger.error(f"Aplay komutu başarısız, çıkış kodu: {aplay_process.returncode}")
            return

    except FileNotFoundError:
        logger.error("Piper veya aplay komutu bulunamadı. Lütfen kurulu olduklarından ve PATH'inizde olduklarından veya tam yolların doğru olduğundan emin olun.")
    except Exception as e:
        logger.error(f"Seslendirme hatası: {e}")

# Konfigürasyon dosyasını oku ve varsayılan RSS adresini ekle
def load_rss_feeds():
    config = configparser.ConfigParser()
    os.makedirs(CONFIG_DIR, exist_ok=True)

    if not os.path.exists(CONFIG_FILE):
        config['RSS'] = {'feeds': DEFAULT_RSS_URL}
        with open(CONFIG_FILE, 'w') as configfile:
            config.write(configfile)
        logger.info(f"Konfigürasyon dosyası oluşturuldu: {CONFIG_FILE}")
    else:
        config.read(CONFIG_FILE)
        if 'RSS' not in config or 'feeds' not in config['RSS']:
            config['RSS'] = {'feeds': DEFAULT_RSS_URL}
            with open(CONFIG_FILE, 'w') as configfile:
                config.write(configfile)
            logger.info(f"Konfigürasyon dosyasına varsayılan RSS adresi eklendi.")

    feeds = config['RSS'].get('feeds', DEFAULT_RSS_URL).split(',')
    feeds = [feed.strip() for feed in feeds if feed.strip()]
    if not feeds:
        feeds = [DEFAULT_RSS_URL]
    return feeds

# Konfigürasyon dosyasına RSS feed'lerini kaydet
def save_rss_feeds(feeds):
    config = configparser.ConfigParser()
    config['RSS'] = {'feeds': ','.join(feeds)}
    with open(CONFIG_FILE, 'w') as configfile:
        config.write(configfile)
    logger.info(f"RSS feed'leri kaydedildi: {CONFIG_FILE}")

# Ağ bağlantısını kontrol et
def check_network():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=5)
        return True
    except OSError as e:
        logger.debug(f"Ağ bağlantısı kontrolü başarısız: {e}")
        return False

# RSS verilerini almak ve HTML'i temizlemek
def get_rss_feed(feeds, max_retries=3, initial_delay=5):
    all_entries = []
    for url in feeds:
        for attempt in range(max_retries):
            try:
                feed = feedparser.parse(url, request_headers={'User-agent': 'RSSReadScrollWindow'})
                if feed.bozo:
                    logger.error(f"RSS ayrıştırma hatası ({url}): {feed.bozo_exception}")
                    continue

                for entry in feed.entries:
                    if not (hasattr(entry, "title") and entry.title.strip()):
                        continue

                    raw_description = getattr(entry, 'description', getattr(entry, 'summary', '')).strip()
                    if not raw_description:
                        description = "Açıklama bulunamadı."
                    else:
                        try:
                            doc = lxml.html.fromstring(raw_description)
                            for a_tag in doc.xpath('//a'):
                                a_tag.drop_tree()
                            h4_elements = doc.xpath('//h4')
                            if h4_elements:
                                description = h4_elements[0].text_content().strip()
                            else:
                                description = doc.text_content().strip()
                            if not description:
                                description = "Açıklama bulunamadı."
                        except Exception as e:
                            logger.error(f"HTML ayrıştırma hatası: {e}")
                            description = "Açıklama ayrıştırılamadı."

                    link = getattr(entry, 'link', '').strip()
                    if not link:
                        logger.warning(f"Başlık için link bulunamadı: {entry.title[:50]}...")

                    all_entries.append({
                        'title': entry.title.strip(),
                        'description': description,
                        'link': link
                    })
                break
            except Exception as e:
                logger.error(f"RSS alınırken hata ({url}, deneme {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    delay = initial_delay * (2 ** attempt)
                    logger.info(f"{delay} saniye sonra tekrar denenecek...")
                    time.sleep(delay)
                continue
    if not all_entries:
        logger.warning("RSS akışlarında başlık bulunamadı.")
        return None
    return all_entries

# Kayan metin penceresi
class ScrollingTextWindow(Gtk.Window):

    SEPARATOR = " ---------- "
    SPEAKING_LOCK = threading.Lock()

    def __init__(self):
        super().__init__(title="Kayan Haberler")

        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor()
        geometry = monitor.get_geometry()
        self.screen_width = geometry.width

        self.set_size_request(self.screen_width, 30)
        self.set_decorated(False)
        self.move(0, 0)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)
            self.set_app_paintable(True)
        else:
            logger.warning("Şeffaf arka plan desteklenmiyor, düz arka plan kullanılacak.")
            self.set_app_paintable(False)

        self.drawing_area = Gtk.DrawingArea()
        self.add(self.drawing_area)
        self.drawing_area.connect("draw", self.on_draw)
        self.drawing_area.connect("size-allocate", self.on_size_allocate)
        self.drawing_area.set_events(self.drawing_area.get_events() | 
                                    Gdk.EventMask.POINTER_MOTION_MASK | 
                                    Gdk.EventMask.BUTTON_PRESS_MASK |
                                    Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.drawing_area.connect("motion-notify-event", self.on_motion_notify)
        self.drawing_area.connect("button-press-event", self.on_button_press)
        self.drawing_area.connect("leave-notify-event", self.on_leave_notify)

        self.rss_feeds = load_rss_feeds()

        self.entries = []
        self.text_with_padding = ""  # Start with empty text
        self.title_pixel_positions = []
        self.total_text_band_width_px = 0
        self.x_position = self.screen_width
        self.speed = 4
        self.is_paused = False
        self.network_available = False
        self.initial_fetch_attempted = False

        self.connect("destroy", Gtk.main_quit)

        self.next_title_index_to_speak = 0

        threading.Thread(target=initialize_audio, daemon=True).start()
        # Perform initial fetch in a separate thread to avoid blocking
        threading.Thread(target=self.initial_fetch, daemon=True).start()
        GLib.timeout_add(10000, self.check_network_and_fetch)  # Check every 10 seconds
        GLib.timeout_add(33, self.update_position)

    def initial_fetch(self):
        # Perform initial network check and fetch
        self.network_available = check_network()
        if self.network_available:
            logger.info("Başlangıçta ağ bağlantısı algılandı, RSS verisi alınıyor...")
            entries = get_rss_feed(self.rss_feeds)
            GLib.idle_add(self.update_text_in_gui, entries)
        else:
            logger.debug("Başlangıçta ağ bağlantısı yok, 10 saniye sonra hata mesajı gösterilecek.")
            # Delay showing error message for 10 seconds
            GLib.timeout_add(10000, self.show_initial_error)
        self.initial_fetch_attempted = True

    def show_initial_error(self):
        if not self.entries and not self.network_available:
            GLib.idle_add(self.update_text_in_gui, None)
        return False  # One-shot timeout

    def check_network_and_fetch(self):
        was_available = self.network_available
        self.network_available = check_network()
        if self.network_available and not was_available:
            logger.info("Ağ bağlantısı algılandı, RSS verisi alınıyor...")
            self.update_rss()
        elif not self.network_available:
            logger.debug("Ağ bağlantısı yok, 10 saniye sonra tekrar kontrol edilecek.")
            if was_available and self.entries:
                GLib.idle_add(self.update_text_in_gui, None)
        return True  # Keep the timeout active

    def on_size_allocate(self, widget, allocation):
        if self.entries:
            self.calculate_title_pixel_positions()
        elif "RSS verisi alınamadı" in self.text_with_padding:
            self.calculate_title_pixel_positions()

    def on_motion_notify(self, widget, event):
        mouse_x = event.x
        title_index = self.get_title_index_at_position(mouse_x)

        if title_index is not None and title_index < len(self.entries):
            description = self.entries[title_index]['description']
            self.drawing_area.set_tooltip_text(description)
            self.is_paused = True
        else:
            self.drawing_area.set_tooltip_text(None)
            self.is_paused = False
            self.drawing_area.queue_draw()

        return False

    def on_leave_notify(self, widget, event):
        self.drawing_area.set_tooltip_text(None)
        self.is_paused = False
        self.drawing_area.queue_draw()
        return False

    def on_button_press(self, widget, event):
        if event.button == 1:
            mouse_x = event.x
            title_index = self.get_title_index_at_position(mouse_x)
            if title_index is not None and title_index < len(self.entries):
                link = self.entries[title_index]['link']
                if link:
                    try:
                        webbrowser.open(link)
                    except Exception as e:
                        logger.error(f"URL açma hatası: {e}")
                else:
                    logger.warning("Tıklanan başlık için link bulunamadı.")
        elif event.button == 3:
            self.show_context_menu(event)
        return True

    def show_context_menu(self, event):
        menu = Gtk.Menu()

        add_feed_item = Gtk.MenuItem(label="Yeni RSS Ekle")
        add_feed_item.connect("activate", self.on_add_feed)
        menu.append(add_feed_item)

        manage_feeds_item = Gtk.MenuItem(label="RSS Listesini Yönet")
        manage_feeds_item.connect("activate", self.on_manage_feeds)
        menu.append(manage_feeds_item)

        exit_item = Gtk.MenuItem(label="Kapat")
        exit_item.connect("activate", self.on_exit)
        menu.append(exit_item)

        menu.show_all()
        menu.popup(None, None, None, None, event.button, event.time)

    def on_exit(self, widget):
        Gtk.main_quit()

    def on_add_feed(self, widget):
        dialog = Gtk.Dialog(title="Yeni RSS Ekle", parent=self, flags=0)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)

        entry = Gtk.Entry()
        entry.set_placeholder_text("RSS URL'sini girin (örn. https://example.com/rss)")
        entry.set_activates_default(True)
        dialog.get_content_area().pack_start(entry, True, True, 0)
        dialog.show_all()

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            url = entry.get_text().strip()
            if validators.url(url):
                if url not in self.rss_feeds:
                    self.rss_feeds.append(url)
                    save_rss_feeds(self.rss_feeds)
                    logger.info(f"Yeni RSS feed eklendi: {url}")
                    self.update_rss()
                else:
                    logger.warning(f"Bu RSS feed zaten mevcut: {url}")
            else:
                logger.error(f"Geçersiz URL: {url}")
        dialog.destroy()

    def on_manage_feeds(self, widget):
        dialog = Gtk.Dialog(title="RSS Listesini Yönet", parent=self, flags=0)
        dialog.add_buttons(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
        dialog.set_default_size(400, 300)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        listbox = Gtk.ListBox()
        scrolled.add(listbox)
        dialog.get_content_area().pack_start(scrolled, True, True, 0)

        adjustment = scrolled.get_vadjustment()

        self.populate_feed_list(listbox, adjustment)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def populate_feed_list(self, listbox, adjustment):
        scroll_position = adjustment.get_value() if adjustment else 0

        for child in listbox.get_children():
            listbox.remove(child)

        for index, feed in enumerate(self.rss_feeds):
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            label = Gtk.Label(label=feed, xalign=0)

            up_button = Gtk.Button()
            up_button.set_image(Gtk.Image.new_from_icon_name("go-up", Gtk.IconSize.BUTTON))
            up_button.set_sensitive(index > 0)
            up_button.connect("clicked", self.on_move_feed, feed, listbox, -1, adjustment)

            down_button = Gtk.Button()
            down_button.set_image(Gtk.Image.new_from_icon_name("go-down", Gtk.IconSize.BUTTON))
            down_button.set_sensitive(index < len(self.rss_feeds) - 1)
            down_button.connect("clicked", self.on_move_feed, feed, listbox, 1, adjustment)

            delete_button = Gtk.Button(label="Sil")
            delete_button.connect("clicked", self.on_delete_feed, feed, listbox, adjustment)

            hbox.pack_start(label, True, True, 0)
            hbox.pack_end(delete_button, False, False, 0)
            hbox.pack_end(down_button, False, False, 0)
            hbox.pack_end(up_button, False, False, 0)
            row.add(hbox)
            listbox.add(row)

        listbox.show_all()
        if adjustment:
            GLib.idle_add(adjustment.set_value, scroll_position)

    def on_move_feed(self, button, feed, listbox, direction, adjustment):
        index = self.rss_feeds.index(feed)
        new_index = index + direction
        if 0 <= new_index < len(self.rss_feeds):
            self.rss_feeds.pop(index)
            self.rss_feeds.insert(new_index, feed)
            save_rss_feeds(self.rss_feeds)
            logger.info(f"RSS feed taşındı: {feed} -> pozisyon {new_index}")
            self.populate_feed_list(listbox, adjustment)
            self.update_rss()

    def on_delete_feed(self, button, feed, listbox, adjustment):
        if feed in self.rss_feeds:
            self.rss_feeds.remove(feed)
            save_rss_feeds(self.rss_feeds)
            logger.info(f"RSS feed silindi: {feed}")
            self.populate_feed_list(listbox, adjustment)
            self.update_rss()

    def get_title_index_at_position(self, mouse_x):
        if not self.title_pixel_positions:
            return None

        for i, (start_px, end_px) in enumerate(self.title_pixel_positions):
            title_start_screen_pos = self.x_position + start_px
            title_end_screen_pos = self.x_position + end_px
            if title_start_screen_pos <= mouse_x <= title_end_screen_pos:
                return i
        return None

    def get_cairo_context_for_measurement(self):
        rect = self.get_allocation()
        width = max(1, rect.width)
        height = max(1, rect.height)

        if width < 1 or height < 1:
            logger.warning(f"Pencere boyutu ({width}x{height}) metin ölçümü için uygun değil. Dummy boyut kullanılıyor.")
            width = self.screen_width
            height = 30

        try:
            format = cairo.Format.ARGB32
        except AttributeError:
            format = cairo.FORMAT_ARGB32

        surface = cairo.ImageSurface(format, width, height)
        cr = cairo.Context(surface)
        font_size = height * 0.7
        self.set_cairo_font_settings(cr, font_size)
        return cr

    def set_cairo_font_settings(self, cr, font_size):
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(font_size)

    def calculate_title_pixel_positions(self):
        self.title_pixel_positions = []
        self.total_text_band_width_px = 0

        if not self.entries and "RSS verisi alınamadı" not in self.text_with_padding:
            return

        cr = self.get_cairo_context_for_measurement()
        if not cr:
            logger.error("Metin ölçümü için Cairo context alınamadı.")
            return

        current_pixel_offset = 0

        if not self.entries and "RSS verisi alınamadı" in self.text_with_padding:
            _, _, _, _, msg_width_px, _ = cr.text_extents(self.text_with_padding)
            self.title_pixel_positions = [(0, msg_width_px)]
            self.total_text_band_width_px = msg_width_px + self.screen_width
            return

        for i, entry in enumerate(self.entries):
            title = entry['title']
            _, _, _, _, title_width_px, _ = cr.text_extents(title)
            start_pixel_offset = current_pixel_offset
            end_pixel_offset = current_pixel_offset + title_width_px
            self.title_pixel_positions.append((start_pixel_offset, end_pixel_offset))

            if i < len(self.entries) - 1:
                _, _, _, _, separator_width_px, _ = cr.text_extents(self.SEPARATOR)
                current_pixel_offset += title_width_px + separator_width_px
            else:
                current_pixel_offset += title_width_px

        self.total_text_band_width_px = current_pixel_offset + self.screen_width

    def update_text_in_gui(self, entries):
        if entries and isinstance(entries, list) and entries:
            self.entries = entries
            self.text_with_padding = self.SEPARATOR.join(entry['title'] for entry in self.entries)
            self.calculate_title_pixel_positions()
            self.x_position = self.screen_width
            self.next_title_index_to_speak = 0
            if self.SPEAKING_LOCK.locked():
                logger.warning("RSS güncellendi, devam eden seslendirme olabilir.")
        else:
            self.entries = []
            self.title_pixel_positions = []
            self.total_text_band_width_px = 0
            self.text_with_padding = "RSS verisi alınamadı veya boş. Tekrar deneniyor..."
            self.calculate_title_pixel_positions()
            self.x_position = self.screen_width
            self.next_title_index_to_speak = 0
            logger.warning("RSS başlıkları güncellenemedi veya boştu.")

        self.drawing_area.queue_draw()
        return False

    def on_draw(self, widget, cr):
        rect = self.get_allocation()
        width = rect.width
        height = rect.height

        if self.get_app_paintable():
            cr.set_source_rgba(0, 0, 0, 0.7)
        else:
            cr.set_source_rgb(0, 0, 0)
        cr.paint()

        if not self.text_with_padding:
            return False  # Don't draw anything if text is empty

        font_size = height * 0.7
        self.set_cairo_font_settings(cr, font_size)
        fascent, fdescent, fheight, fxadvance, fyadvance = cr.font_extents()
        text_y = (height / 2) + (fheight / 2) - fdescent

        cr.set_source_rgb(1, 1, 1)
        cr.move_to(self.x_position, text_y)
        cr.show_text(self.text_with_padding)
        return False

    def update_position(self):
        if self.is_paused:
            return True

        self.x_position -= self.speed

        if self.SPEAKING_LOCK.locked() or not self.entries:
            self.drawing_area.queue_draw()
            return True

        if self.title_pixel_positions and self.next_title_index_to_speak < len(self.title_pixel_positions):
            start_pixel_offset, end_pixel_offset = self.title_pixel_positions[self.next_title_index_to_speak]
            title_start_screen_pos = self.x_position + start_pixel_offset
            title_end_screen_pos = self.x_position + end_pixel_offset

            title_center_screen_pos = (title_start_screen_pos + title_end_screen_pos) / 2
            trigger_threshold = self.screen_width * 0.8

            if title_center_screen_pos <= trigger_threshold:
                text_to_speak = self.entries[self.next_title_index_to_speak]['title']
                with self.SPEAKING_LOCK:
                    threading.Thread(target=self.speak_and_unlock, args=(text_to_speak,), daemon=True).start()
                self.next_title_index_to_speak += 1

        if self.total_text_band_width_px > 0 and self.x_position + self.total_text_band_width_px < 0:
            self.x_position = self.screen_width
            self.next_title_index_to_speak = 0

        self.drawing_area.queue_draw()
        return True

    def speak_and_unlock(self, text):
        try:
            speak_text(text)
        finally:
            pass

    def update_rss(self):
        threading.Thread(target=self.periodic_rss_fetch, daemon=True).start()

    def periodic_rss_fetch(self):
        if not self.network_available:
            logger.debug("Ağ bağlantısı yok, RSS alınmadı.")
            GLib.idle_add(self.update_text_in_gui, None)
            return
        entries = get_rss_feed(self.rss_feeds)
        GLib.idle_add(self.update_text_in_gui, entries)

def main():
    win = ScrollingTextWindow()
    win.show_all()
    Gtk.main()

if __name__ == "__main__":
    main()
