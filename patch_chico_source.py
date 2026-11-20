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
