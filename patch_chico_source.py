"""Clean C++ source patcher for 78/xiaozhi-esp32 -> Chico for Nieves."""
import os
import re

REPO = "xiaozhi-esp32"
OTA_URL = os.environ.get("CHICO_OTA_URL", "https://chico-nieves.onrender.com/ota/")


def patch_board_pins():
    path = os.path.join(REPO, "main", "boards", "bread-compact-wifi-lcd", "config.h")
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    replacements = {
        r"#define AUDIO_OUTPUT_SAMPLE_RATE\s+\d+": "#define AUDIO_OUTPUT_SAMPLE_RATE 16000",
        r"#define AUDIO_I2S_MIC_GPIO_WS\s+\S+": "#define AUDIO_I2S_MIC_GPIO_WS   GPIO_NUM_40",
        r"#define AUDIO_I2S_MIC_GPIO_SCK\s+\S+": "#define AUDIO_I2S_MIC_GPIO_SCK  GPIO_NUM_42",
        r"#define AUDIO_I2S_MIC_GPIO_DIN\s+\S+": "#define AUDIO_I2S_MIC_GPIO_DIN  GPIO_NUM_41",
        r"#define AUDIO_I2S_SPK_GPIO_DOUT\s+\S+": "#define AUDIO_I2S_SPK_GPIO_DOUT GPIO_NUM_21",
        r"#define AUDIO_I2S_SPK_GPIO_BCLK\s+\S+": "#define AUDIO_I2S_SPK_GPIO_BCLK GPIO_NUM_47",
        r"#define AUDIO_I2S_SPK_GPIO_LRCK\s+\S+": "#define AUDIO_I2S_SPK_GPIO_LRCK GPIO_NUM_48",
        r"#define BUILTIN_LED_GPIO\s+\S+": "#define BUILTIN_LED_GPIO        GPIO_NUM_NC",
        r"#define DISPLAY_BACKLIGHT_PIN\s+\S+": "#define DISPLAY_BACKLIGHT_PIN GPIO_NUM_10",
        r"#define DISPLAY_MOSI_PIN\s+\S+": "#define DISPLAY_MOSI_PIN      GPIO_NUM_8",
        r"#define DISPLAY_CLK_PIN\s+\S+": "#define DISPLAY_CLK_PIN       GPIO_NUM_9",
        r"#define DISPLAY_DC_PIN\s+\S+": "#define DISPLAY_DC_PIN        GPIO_NUM_12",
        r"#define DISPLAY_RST_PIN\s+\S+": "#define DISPLAY_RST_PIN       GPIO_NUM_13",
        r"#define DISPLAY_CS_PIN\s+\S+": "#define DISPLAY_CS_PIN        GPIO_NUM_11",
    }
    for pat, rep in replacements.items():
        new_src, n = re.subn(pat, rep, src)
        assert n >= 1, f"Pattern not found in config.h: {pat}"
        src = new_src

    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print("[OK] Patched bread-compact-wifi-lcd/config.h with exact Chico GPIO pins")


def patch_application_cc():
    path = os.path.join(REPO, "main", "application.cc")
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    # 1. Anti-cutoff: NEVER keep wake word detection enabled while speaking (prevents speaker->mic echo abort!)
    old_speaking = "audio_service_.EnableWakeWordDetection(audio_service_.IsAfeWakeWord());"
    if old_speaking in src:
        idx_spk = src.find("case kDeviceStateSpeaking:")
        if idx_spk != -1:
            idx_call = src.find(old_speaking, idx_spk)
            if idx_call != -1 and idx_call - idx_spk < 500:
                src = src[:idx_call] + "audio_service_.EnableWakeWordDetection(false); // Chico anti-cutoff" + src[idx_call + len(old_speaking):]
                print("[OK] Anti-cutoff enabled in kDeviceStateSpeaking")

    # 2. Alexa Mode: On tts: stop, ALWAYS transition to kDeviceStateIdle (keeps WebSocket open for timer alerts!)
    old_tts_stop = "SetDeviceState(kDeviceStateListening);"
    idx_stop = src.find('strcmp(state->valuestring, "stop")')
    if idx_stop != -1:
        idx_listen = src.find(old_tts_stop, idx_stop)
        if idx_listen != -1 and idx_listen - idx_stop < 400:
            src = src[:idx_listen] + "SetDeviceState(kDeviceStateIdle); // Chico Alexa Mode" + src[idx_listen + len(old_tts_stop):]
            print("[OK] Alexa Mode enabled on tts: stop -> kDeviceStateIdle")

    # 3. Native LVGL Animated Blue Eyes (Solo Ojos) + Animated Blue-Ring Timer
    chico_ui_code = r'''
// --- CHICO NATIVE LVGL ANIMATED EYES + BLUE-RING TIMER ---
#include <lvgl.h>

static lv_obj_t* g_chico_eyes_bg = nullptr;
static lv_obj_t* g_chico_eye_l = nullptr;
static lv_obj_t* g_chico_eye_r = nullptr;
static lv_timer_t* g_chico_eyes_timer = nullptr;
static int g_chico_eye_tick = 0;

static void ChicoEyesTick(lv_timer_t* t) {
    (void)t;
    if (!g_chico_eye_l || !g_chico_eye_r) return;
    g_chico_eye_tick++;
    int phase = g_chico_eye_tick % 45; // ~4.5s cycle at 100ms tick

    // Natural blink
    if (phase == 40 || phase == 41) {
        lv_obj_set_height(g_chico_eye_l, 10);
        lv_obj_set_height(g_chico_eye_r, 10);
    } else {
        lv_obj_set_height(g_chico_eye_l, 76);
        lv_obj_set_height(g_chico_eye_r, 76);
    }

    // Subtle lively look left / center / right
    int dx = 0;
    if (phase >= 12 && phase <= 20) dx = -8;
    else if (phase >= 26 && phase <= 34) dx = 8;
    lv_obj_align(g_chico_eye_l, LV_ALIGN_CENTER, -42 + dx, 0);
    lv_obj_align(g_chico_eye_r, LV_ALIGN_CENTER,  42 + dx, 0);
}

static void ChicoEyesInit() {
    if (g_chico_eyes_bg) return;
    g_chico_eyes_bg = lv_obj_create(lv_layer_top());
    lv_obj_set_size(g_chico_eyes_bg, LV_PCT(100), LV_PCT(100));
    lv_obj_set_style_bg_color(g_chico_eyes_bg, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(g_chico_eyes_bg, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(g_chico_eyes_bg, 0, 0);
    lv_obj_set_style_pad_all(g_chico_eyes_bg, 0, 0);
    lv_obj_clear_flag(g_chico_eyes_bg, LV_OBJ_FLAG_SCROLLABLE);

    auto make_eye = [&](int x_off) -> lv_obj_t* {
        lv_obj_t* eye = lv_obj_create(g_chico_eyes_bg);
        lv_obj_set_size(eye, 64, 76);
        lv_obj_set_style_radius(eye, 32, 0);
        lv_obj_set_style_bg_color(eye, lv_color_hex(0x00D8FF), 0);
        lv_obj_set_style_bg_opa(eye, LV_OPA_COVER, 0);
        lv_obj_set_style_border_width(eye, 0, 0);
        lv_obj_clear_flag(eye, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_align(eye, LV_ALIGN_CENTER, x_off, 0);
        return eye;
    };
    g_chico_eye_l = make_eye(-42);
    g_chico_eye_r = make_eye(42);
    g_chico_eyes_timer = lv_timer_create(ChicoEyesTick, 100, nullptr);
}

static lv_obj_t* g_chico_timer_bg = nullptr;
static lv_obj_t* g_chico_timer_arc = nullptr;
static lv_obj_t* g_chico_timer_time_lbl = nullptr;
static lv_obj_t* g_chico_timer_name_lbl = nullptr;
static lv_timer_t* g_chico_lv_timer = nullptr;
static int g_chico_timer_total = 1;
static int g_chico_timer_elapsed = 0;

static void ChicoTimerHide() {
    if (g_chico_lv_timer) { lv_timer_del(g_chico_lv_timer); g_chico_lv_timer = nullptr; }
    if (g_chico_timer_bg) { lv_obj_del(g_chico_timer_bg); g_chico_timer_bg = nullptr; }
    g_chico_timer_arc = nullptr;
    g_chico_timer_time_lbl = nullptr;
    g_chico_timer_name_lbl = nullptr;
}

static void ChicoTimerTick(lv_timer_t* t) {
    (void)t;
    g_chico_timer_elapsed++;
    int rem = g_chico_timer_total - g_chico_timer_elapsed;
    if (rem < 0) rem = 0;
    int pct = (g_chico_timer_total > 0) ? (g_chico_timer_elapsed * 100 / g_chico_timer_total) : 100;
    if (pct > 100) pct = 100;
    if (g_chico_timer_arc) lv_arc_set_value(g_chico_timer_arc, pct);
    if (g_chico_timer_time_lbl) {
        char buf[16];
        snprintf(buf, sizeof(buf), "%02d:%02d", rem / 60, rem % 60);
        lv_label_set_text(g_chico_timer_time_lbl, buf);
    }
    if (rem <= 0) {
        ChicoTimerHide();
    }
}

static void ChicoTimerShow(int seconds, const char* label) {
    ChicoTimerHide();
    if (seconds <= 0) return;
    g_chico_timer_total = seconds;
    g_chico_timer_elapsed = 0;

    g_chico_timer_bg = lv_obj_create(lv_layer_top());
    lv_obj_set_size(g_chico_timer_bg, LV_PCT(100), LV_PCT(100));
    lv_obj_set_style_bg_color(g_chico_timer_bg, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(g_chico_timer_bg, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(g_chico_timer_bg, 0, 0);
    lv_obj_set_style_pad_all(g_chico_timer_bg, 0, 0);
    lv_obj_clear_flag(g_chico_timer_bg, LV_OBJ_FLAG_SCROLLABLE);

    g_chico_timer_arc = lv_arc_create(g_chico_timer_bg);
    lv_obj_set_size(g_chico_timer_arc, 216, 216);
    lv_obj_center(g_chico_timer_arc);
    lv_arc_set_rotation(g_chico_timer_arc, 270);
    lv_arc_set_bg_angles(g_chico_timer_arc, 0, 360);
    lv_arc_set_range(g_chico_timer_arc, 0, 100);
    lv_arc_set_value(g_chico_timer_arc, 0);
    lv_obj_remove_style(g_chico_timer_arc, NULL, LV_PART_KNOB);
    lv_obj_clear_flag(g_chico_timer_arc, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_arc_width(g_chico_timer_arc, 14, LV_PART_MAIN);
    lv_obj_set_style_arc_color(g_chico_timer_arc, lv_color_hex(0x1E293B), LV_PART_MAIN);
    lv_obj_set_style_arc_width(g_chico_timer_arc, 14, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(g_chico_timer_arc, lv_color_hex(0x00C8FF), LV_PART_INDICATOR);

    g_chico_timer_time_lbl = lv_label_create(g_chico_timer_bg);
    char buf[16];
    snprintf(buf, sizeof(buf), "%02d:%02d", seconds / 60, seconds % 60);
    lv_label_set_text(g_chico_timer_time_lbl, buf);
    lv_obj_set_style_text_color(g_chico_timer_time_lbl, lv_color_white(), 0);
    lv_obj_set_style_text_font(g_chico_timer_time_lbl, &lv_font_montserrat_28, 0);
    lv_obj_align(g_chico_timer_time_lbl, LV_ALIGN_CENTER, 0, -10);

    g_chico_timer_name_lbl = lv_label_create(g_chico_timer_bg);
    lv_label_set_text(g_chico_timer_name_lbl, (label && label[0]) ? label : "TEMPORIZADOR");
    lv_obj_set_style_text_color(g_chico_timer_name_lbl, lv_color_hex(0x00C8FF), 0);
    lv_obj_align(g_chico_timer_name_lbl, LV_ALIGN_CENTER, 0, 28);

    g_chico_lv_timer = lv_timer_create(ChicoTimerTick, 1000, nullptr);
}
// --- END CHICO UI ---
'''
    tag_anchor = '#define TAG "Application"'
    if tag_anchor in src and "ChicoTimerShow" not in src:
        src = src.replace(tag_anchor, tag_anchor + "\n" + chico_ui_code, 1)

    # Show Chico animated blue eyes once WiFi is connected & device enters Idle (so WiFi setup screen still works!)
    idle_anchor = "case kDeviceStateIdle:"
    if idle_anchor in src and "ChicoEyesInit()" not in src:
        src = src.replace(
            idle_anchor,
            idle_anchor + "\n            { DisplayLockGuard lock(display); ChicoEyesInit(); }",
            1,
        )
        print("[OK] Injected ChicoEyesInit() on kDeviceStateIdle (Solo Ojos!)")

    # Handle {"type":"timer","action":"start"|"stop",...} inside OnIncomingJson
    tts_anchor = 'if (strcmp(type->valuestring, "tts") == 0)'
    timer_handler = '''if (strcmp(type->valuestring, "timer") == 0) {
            auto action = cJSON_GetObjectItem(root, "action");
            if (cJSON_IsString(action) && strcmp(action->valuestring, "start") == 0) {
                auto secs = cJSON_GetObjectItem(root, "seconds");
                auto lbl = cJSON_GetObjectItem(root, "label");
                int s = cJSON_IsNumber(secs) ? secs->valueint : 60;
                std::string label_str = cJSON_IsString(lbl) ? lbl->valuestring : "TEMPORIZADOR";
                Schedule([display, s, label_str]() {
                    DisplayLockGuard lock(display);
                    ChicoTimerShow(s, label_str.c_str());
                });
            } else {
                Schedule([display]() {
                    DisplayLockGuard lock(display);
                    ChicoTimerHide();
                });
            }
        } else if (strcmp(type->valuestring, "tts") == 0)'''
    if tts_anchor in src and '"timer"' not in src:
        src = src.replace(tts_anchor, timer_handler, 1)
        print("[OK] Injected native LVGL animated blue-ring timer handler in application.cc")

    with open(path, "w", encoding="utf-8") as f:
        f.write(src)


def write_sdkconfig_defaults():
    path = os.path.join(REPO, "sdkconfig.defaults.chico")
    cfg = f'''
CONFIG_IDF_TARGET="esp32s3"
CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y
CONFIG_SPIRAM=y
CONFIG_SPIRAM_MODE_OCT=y
CONFIG_SPIRAM_SPEED_80M=y

CONFIG_BOARD_TYPE_BREAD_COMPACT_WIFI_LCD=y
CONFIG_LCD_ST7789_240X240=y
CONFIG_LANGUAGE_ES_ES=y
CONFIG_OTA_URL="{OTA_URL}"

CONFIG_USE_AUDIO_PROCESSOR=y
CONFIG_USE_CUSTOM_WAKE_WORD=y
CONFIG_SR_MN_CN_NONE=y
CONFIG_SR_MN_EN_MULTINET7_QUANT=y
CONFIG_CUSTOM_WAKE_WORD="chico"
CONFIG_CUSTOM_WAKE_WORD_DISPLAY="Chico"
CONFIG_CUSTOM_WAKE_WORD_THRESHOLD=10
CONFIG_WAKE_WORD_DETECTION_IN_LISTENING=n
CONFIG_LV_FONT_MONTSERRAT_28=y
'''
    with open(path, "w", encoding="utf-8") as f:
        f.write(cfg.strip() + "\n")
    print(f"[OK] Wrote sdkconfig.defaults.chico pointing to {OTA_URL}")


if __name__ == "__main__":
    patch_board_pins()
    patch_application_cc()
    write_sdkconfig_defaults()
    print("=== ALL CHICO C++ SOURCE PATCHES APPLIED CLEANLY ===")
