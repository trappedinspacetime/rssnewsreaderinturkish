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

# Log ayarları
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Ses cihazını başlatmak için boş ses çal
def initialize_audio():
    try:
        #logger.debug("Ses cihazı başlatılıyor...")
        command = ["aplay", "-r", "22050", "-f", "S16_LE", "-t", "raw", "/dev/zero"]
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            logger.warning("Ses cihazı başlatma denemesi zaman aşımına uğradı (zaman aşımı).")
        except Exception as e:
            logger.error(f"aplay başlatma sürecinde hata: {e}")
        #logger.debug("Ses cihazı başlatıldı (deneme yapıldı).")
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

        # Model dosyalarının yolu ve indirme işlemi
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

        # Dosyaları indirme (eğer yoksa)
        for file_type, file_info in model_files.items():
            if not os.path.exists(file_info["path"]):
                logger.info(f"{file_type} dosyası indiriliyor: {file_info['url']}")
                try:
                    response = requests.get(file_info["url"], stream=True)
                    response.raise_for_status()
                    with open(file_info["path"], "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    logger.info(f"{file_type} dosyası indirildi: {file_info['path']}")
                except Exception as e:
                    logger.error(f"{file_type} dosyası indirilemedi: {e}")
                    return

        # Piper komutunu çalıştır
        piper_command = (
            f"echo {quoted_text} | piper "
            f"--model {model_files['model']['path']} "
            f"--config {model_files['config']['path']} "
            "--length-scale 0.833 "
            "--output_raw"
        )

        piper_process = subprocess.Popen(piper_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)

        # ... (kalan kod aynı)
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

# RSS verilerini almak ve HTML'i temizlemek
def get_rss_feed():
    try:
        feed = feedparser.parse("https://www.gercekgundem.com/rss/", request_headers={'User-agent': 'RSSReadScrollWindow'})

        if feed.bozo:
            logger.error(f"RSS ayrıştırma hatası: {feed.bozo_exception}")
            return None

        entries = []
        for entry in feed.entries:
            if not (hasattr(entry, "title") and entry.title.strip()):
                continue

            # Açıklama veya özet al
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

            # Link al
            link = getattr(entry, 'link', '').strip()
            if not link:
                logger.warning(f"Başlık için link bulunamadı: {entry.title[:50]}...")

            entries.append({
                'title': entry.title.strip(),
                'description': description,
                'link': link
            })

        if not entries:
            logger.warning("RSS akışında başlık bulunamadı.")
            return None

        #logger.debug(f"RSS verisi alındı ({len(entries)} başlık).")
        return entries
    except Exception as e:
        logger.error(f"RSS alınırken hata: {e}")
        return None

# Kayan metin penceresi
class ScrollingTextWindow(Gtk.Window):

    SEPARATOR = " ---------- "
    SPEAKING_LOCK = threading.Lock()

    def __init__(self):
        super().__init__(title="Kayan Haberler")

        # Pencere ayarları
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor()
        geometry = monitor.get_geometry()
        self.screen_width = geometry.width
        #logger.debug(f"Ekran genişliği tespit edildi: {self.screen_width}")

        self.set_size_request(self.screen_width, 30)
        self.set_decorated(False)
        self.move(0, 0)

        # Şeffaf arka plan
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)
            self.set_app_paintable(True)
            #logger.debug("Şeffaf arka plan etkin.")
        else:
            logger.warning("Şeffaf arka plan desteklenmiyor, düz arka plan kullanılacak.")
            self.set_app_paintable(False)

        # Çizim alanı
        self.drawing_area = Gtk.DrawingArea()
        self.add(self.drawing_area)
        self.drawing_area.connect("draw", self.on_draw)
        self.drawing_area.connect("size-allocate", self.on_size_allocate)
        # Fare hareketi, tıklama ve ayrılma için olayları etkinleştir
        self.drawing_area.set_events(self.drawing_area.get_events() | 
                                    Gdk.EventMask.POINTER_MOTION_MASK | 
                                    Gdk.EventMask.BUTTON_PRESS_MASK |
                                    Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.drawing_area.connect("motion-notify-event", self.on_motion_notify)
        self.drawing_area.connect("button-press-event", self.on_button_press)
        self.drawing_area.connect("leave-notify-event", self.on_leave_notify)

        # Metin ve animasyon
        self.entries = []
        self.text_with_padding = ""
        self.title_pixel_positions = []
        self.total_text_band_width_px = 0
        self.x_position = self.screen_width
        self.speed = 4
        self.is_paused = False

        self.connect("destroy", Gtk.main_quit)
        #self.set_keep_above(True)

        # Seslendirme takibi
        self.next_title_index_to_speak = 0

        # Ses cihazını başlat
        threading.Thread(target=initialize_audio, daemon=True).start()

        # RSS verisini ilk kez çek
        threading.Thread(target=self.periodic_rss_fetch, daemon=True).start()

        # Animasyon ve RSS güncelleme zamanlayıcıları
        GLib.timeout_add(33, self.update_position)
        GLib.timeout_add(600000, self.update_rss)

    def on_size_allocate(self, widget, allocation):
        #logger.debug(f"Pencere boyutu değişti: {allocation.width}x{allocation.height}. Piksel pozisyonları yeniden hesaplanıyor.")
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
            #logger.debug(f"Başlık üzerine gelindi, kaydırma durdu. İndeks: {title_index}")
        else:
            self.drawing_area.set_tooltip_text(None)
            self.is_paused = False
            self.drawing_area.queue_draw()
            #logger.debug("Fare başlık dışına (ayraç veya boşluk) çıktı, kaydırma devam ediyor.")

        return False

    def on_leave_notify(self, widget, event):
        # Fare DrawingArea'dan çıktığında (ör. şeridin dışına)
        self.drawing_area.set_tooltip_text(None)
        self.is_paused = False
        self.drawing_area.queue_draw()
        #logger.debug("Fare DrawingArea'dan çıktı, kaydırma devam ediyor.")
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
                        #logger.debug(f"URL açıldı: {link}")
                    except Exception as e:
                        logger.error(f"URL açma hatası: {e}")
                else:
                    logger.warning("Tıklanan başlık için link bulunamadı.")
        return False

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
            #logger.debug(f"Hata mesajı piksel pozisyonu hesaplandı: {self.title_pixel_positions}. Toplam genişlik: {self.total_text_band_width_px}")
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
            #logger.debug(f"RSS başlıkları GUI'de güncellendi. Toplam {len(self.entries)} başlık.")
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

        font_size = height * 0.7
        self.set_cairo_font_settings(cr, font_size)
        fascent, fdescent, fheight, fxadvance, fyadvance = cr.font_extents()
        text_y = (height / 2) + (fheight / 2) - fdescent

        cr.set_source_rgb(1, 1, 1)
        text_to_display = self.text_with_padding if self.text_with_padding else "Veri bekleniyor..."
        cr.move_to(self.x_position, text_y)
        cr.show_text(text_to_display)
        return False

    def update_position(self):
        if self.is_paused:
            return True

        self.x_position -= self.speed

        # Eğer şu anda bir seslendirme yapılıyorsa veya başlık yoksa, yeni tetikleme yapma
        if self.SPEAKING_LOCK.locked() or not self.entries:
            self.drawing_area.queue_draw()
            return True

        # Başlık pozisyonlarını kontrol et
        if self.title_pixel_positions and self.next_title_index_to_speak < len(self.title_pixel_positions):
            start_pixel_offset, end_pixel_offset = self.title_pixel_positions[self.next_title_index_to_speak]
            title_start_screen_pos = self.x_position + start_pixel_offset
            title_end_screen_pos = self.x_position + end_pixel_offset

            # Başlığın ortası ekrana geldiğinde seslendirmeyi tetikle
            title_center_screen_pos = (title_start_screen_pos + title_end_screen_pos) / 2
            trigger_threshold = self.screen_width * 0.8  # Ekranın ortası

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
        #logger.debug("RSS güncellemesi başlatılıyor...")
        threading.Thread(target=self.periodic_rss_fetch, daemon=True).start()
        return True

    def periodic_rss_fetch(self):
        entries = get_rss_feed()
        GLib.idle_add(self.update_text_in_gui, entries)

def main():
    win = ScrollingTextWindow()
    win.show_all()
    #logger.debug("Pencere gösterildi, Gtk.main() çalışıyor.")
    Gtk.main()
    #logger.debug("Gtk.main() sonlandı.")

if __name__ == "__main__":
    main()
